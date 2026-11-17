"""End-to-end controlled acceptance for the WorkBuddy 6008 credits fix.

The classification contract is covered at the function level in
``test_cpa_admission_credits.py``. These tests drive the *real* HTTP stack --
a fake upstream returning the provider's actual 6008 body, the real
``AdmissionProxy``/``Server``, and a real client -- so probe, classification,
latch, persistence, and the per-model refusal are exercised in the order
production sees them.

Regression target: one drained credential used to gate every model, including
zero-cost ones, and reported the cause as a missing deadline. Production
currently carries no zero-cost model (hy4flash-free retired 2026-11-17), so
the zero-cost scenario rides a synthetic fixture name injected into the intl
lane config here; the bypass logic itself is generic lane configuration.

``traffic_lock_path`` is deliberately left unset: that guard needs ``fcntl``,
which does not exist on the Windows development host. With no lock path the
guard yields immediately, so this suite exercises the proxy without claiming to
cover the cross-process drain lock (that path is covered on Linux by the
deployed acceptance run).
"""

from __future__ import annotations

import http.client
import json
import runpy
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast


from test_cpa_admission import config, lane
from script_validation_support import server_thread

MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "remote" / "cpa-admission.py")
)
AdmissionProxy = cast(Any, MODULE["AdmissionProxy"])
AdmissionServer = cast(Any, MODULE["Server"])

# Production carries no zero-cost model since the 2026-11-17 hy4flash-free
# retirement; the zero-cost bypass scenario rides this synthetic fixture name
# injected into the intl lane by _run().
ZERO_COST_FIXTURE = "wb-zero-cost-fixture"


def _credits_body(code: int = 6008) -> bytes:
    """The shape CPA logs: a rate_limit_error wrapper quoting the provider object."""
    inner = json.dumps(
        {"code": code, "msg": "Credits exhausted. Please visit the link"}
    )
    return json.dumps(
        {
            "error": {
                "message": f"upstream 429: {inner}",
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
            }
        }
    ).encode("utf-8")


def _serve(handler: Any) -> tuple[Any, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = server_thread(server)
    thread.start()
    return server, thread


def _post(port: int, model: str) -> bytes:
    body = json.dumps(
        {
            "model": model,
            "stream": True,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        }
    ).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    try:
        conn.request(
            "POST",
            "/v1/messages",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        return conn.getresponse().read()
    finally:
        conn.close()


def _run(
    tmp_path: Path, models: list[str], code: int = 6008
) -> tuple[list[bytes], dict[str, Any]]:
    calls: list[str] = []
    body = _credits_body(code)

    class CreditsUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(json.loads(payload)["model"])
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    upstream, upstream_thread = _serve(CreditsUpstream)
    loaded = config()
    # The test intentionally shortens both lane queues, but keeps their
    # production routing and independent persistent circuit state. The intl
    # lane carries no zero-cost model in production any more, so the bypass
    # scenario rides the synthetic fixture name.
    intl_configured = lane(loaded, "workbuddy-intl-oauth")
    intl_configured["models"] = list(intl_configured["models"]) + [ZERO_COST_FIXTURE]
    intl_configured["zero_cost_models"] = [ZERO_COST_FIXTURE]
    for lane_name in ("workbuddy-oauth", "workbuddy-intl-oauth"):
        loaded_lane = lane(loaded, lane_name)
        loaded_lane["max_inflight"] = 2
        loaded_lane["queue_timeout_seconds"] = 1
    loaded["upstream_port"] = upstream.server_port
    cn_state_path = tmp_path / "workbuddy-risk.json"
    intl_state_path = tmp_path / "workbuddy-intl-risk.json"
    # LaneState refuses to construct without a readable, schema-valid state file.
    initial_state = json.dumps({"version": 1, "not_before": 0, "hold_reason": None})
    cn_state_path.write_text(
        initial_state,
        encoding="utf-8",
    )
    intl_state_path.write_text(
        initial_state,
        encoding="utf-8",
    )
    proxy = AdmissionProxy(
        loaded,
        workbuddy_state_path=cn_state_path,
        workbuddy_intl_state_path=intl_state_path,
    )
    server = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_worker = server_thread(server)
    admission_worker.start()
    responses: list[bytes] = []
    try:
        for model in models:
            responses.append(_post(server.server_address[1], model))
    finally:
        server.shutdown()
        upstream.shutdown()
        server.server_close()
        upstream.server_close()
        admission_worker.join(timeout=3)
        upstream_thread.join(timeout=3)
    states = {
        "cn": json.loads(cn_state_path.read_text(encoding="utf-8")),
        "intl": json.loads(intl_state_path.read_text(encoding="utf-8")),
    }
    return responses, {"calls": calls, "states": states}


def test_6008_arms_the_capacity_ladder_without_a_hold(tmp_path: Path) -> None:
    """2026-11-14 revision: the daily-allowance refusal is window-class.

    It arms the strict lane's capacity ladder (first failure already parks a
    strict lane) and persists a plain cooldown deadline -- it must never
    resurrect the durable `account_credits_exhausted` hold whose
    operator-review gate manufactured the false "all 14 credentials dead,
    purchase add-on" verdict.
    """
    responses, info = _run(
        tmp_path,
        ["deepseek-v4.1-flash-sg", "deepseek-v4.1-flash"],
    )
    assert len(responses) == 2
    assert info["states"]["intl"]["hold_reason"] is None
    assert info["states"]["intl"]["not_before"] > 0
    # A 6008 on the INTL second bucket must not cool the CN bare-name lane.
    # The CN request therefore reaches the fake upstream as well.
    assert info["calls"] == ["deepseek-v4.1-flash-sg", "deepseek-v4.1-flash"]
    assert info["states"]["cn"]["hold_reason"] is None
    assert info["states"]["cn"]["not_before"] > 0


def test_credits_window_admits_the_zero_cost_model(tmp_path: Path) -> None:
    """A zero-cost lane model costs x0.00 credits, so a burned-through
    allowance must not gate it.

    The scenario rides the synthetic ZERO_COST_FIXTURE name (production
    carries no zero-cost model since the hy4flash-free retirement).

    The fake upstream refuses everything, so the free request also ends in an
    upstream error -- what matters is that it was *admitted* (it reached the
    upstream) while the paid request after the ladder armed never left the
    proxy. The workbuddy lane is strict-backoff, so the FIRST refusal already
    arms the cooldown.
    """
    responses, info = _run(
        tmp_path,
        [
            "deepseek-v4.1-flash-sg",
            "deepseek-v4.1-flash-intl",
            "deepseek-v4.1-flash",
            ZERO_COST_FIXTURE,
        ],
    )
    assert info["states"]["intl"]["hold_reason"] is None
    # The first paid INTL request reached the upstream and armed that lane...
    assert info["calls"][0] == "deepseek-v4.1-flash-sg", info["calls"]
    # ...the other paid INTL alias was refused locally, while CN remained
    # independent and the zero-cost INTL model still passed its cooldown...
    assert "deepseek-v4.1-flash-intl" not in info["calls"], info["calls"]
    assert "deepseek-v4.1-flash" in info["calls"], info["calls"]
    assert ZERO_COST_FIXTURE in info["calls"], info["calls"]
    refused = responses[-1].decode("utf-8", errors="replace")
    assert "operator_review_required" not in refused


def test_credits_refusal_stays_a_typed_messages_error(tmp_path: Path) -> None:
    """The ladder refusal must stay protocol-parseable for a Messages client."""
    responses, _ = _run(tmp_path, ["deepseek-v4.1-flash-sg", "deepseek-v4.1-flash-sg"])
    refused = responses[-1].decode("utf-8", errors="replace")
    frame = refused.split("data: ", 1)[1].split("\n", 1)[0]
    decoded = json.loads(frame)
    assert decoded["type"] == "error"
    assert decoded["error"]["code"] == "cooldown"
    assert "cooling down" in decoded["error"]["message"]


def test_a_rate_window_code_does_not_latch_the_billing_hold(tmp_path: Path) -> None:
    """6004 is a model rate window; it must not be recorded as drained credits."""
    _, info = _run(tmp_path, ["deepseek-v4.1-flash-sg"], code=6004)
    assert info["states"]["intl"]["hold_reason"] != "account_credits_exhausted"

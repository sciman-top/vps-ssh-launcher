from __future__ import annotations

import json
import errno
import os
import tempfile
import http.client
import hashlib
import logging
import queue
import runpy
import socket
import socketserver
import struct
import threading
import time
import sys
import types
import pytest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from cpa_catalog_expectations import ADMISSION_MODEL_LANES


MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "remote" / "cpa-admission.py")
)
LaneState = cast(Any, MODULE["LaneState"])
AdmissionProxy = cast(Any, MODULE["AdmissionProxy"])
AdmissionServer = cast(Any, MODULE["Server"])
is_capacity_response = cast(Any, MODULE["is_capacity_response"])
capacity_scope = cast(Any, MODULE["capacity_scope"])
load_config = cast(Any, MODULE["load_config"])
parse_retry_after = cast(Any, MODULE["parse_retry_after"])
requested_lane = cast(Any, MODULE["requested_lane"])
requested_model = cast(Any, MODULE["requested_model"])
StreamErrorProbe = cast(Any, MODULE["StreamErrorProbe"])
read_sse_upstream = cast(Any, MODULE["_read_sse_upstream"])
antigravity_risk_signal = cast(Any, MODULE["antigravity_risk_signal"])


def test_request_guard_holds_independent_descriptors_and_releases_on_error(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    active: set[int] = set()
    exclusive = [False]

    def flock(handle: Any, operation: int) -> None:
        if operation == 3:
            active.remove(handle.fileno())
        elif exclusive[0]:
            raise BlockingIOError()
        else:
            active.add(handle.fileno())

    monkeypatch.setitem(
        sys.modules,
        "fcntl",
        types.SimpleNamespace(flock=flock, LOCK_SH=1, LOCK_NB=4, LOCK_UN=3),
    )
    proxy = AdmissionProxy(config(), traffic_lock_path=tmp_path / "requests.lock")
    with proxy.request_guard() as first:
        assert first
        with proxy.request_guard() as second:
            assert second and len(active) == 2
        assert len(active) == 1
    assert not active
    exclusive[0] = True
    with proxy.request_guard() as admitted:
        assert not admitted
    exclusive[0] = False
    with pytest.raises(RuntimeError), proxy.request_guard() as admitted:
        assert admitted
        raise RuntimeError("disconnected")
    assert not active
    proxy.traffic_lock_path = tmp_path / "missing" / "requests.lock"
    with proxy.request_guard() as admitted:
        assert not admitted


@pytest.mark.parametrize(
    ("stage", "error_number", "reason"),
    [
        ("open", errno.EACCES, "lock_unavailable"),
        ("flock", errno.EAGAIN, "maintenance_busy"),
        ("flock", errno.EACCES, "maintenance_busy"),
        ("flock", errno.EIO, "lock_error"),
    ],
)
def test_request_guard_logs_failure_without_sensitive_exception(
    tmp_path: Path,
    monkeypatch: Any,
    caplog: Any,
    stage: str,
    error_number: int,
    reason: str,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError(error_number, "sensitive exception text")

    monkeypatch.setitem(
        sys.modules,
        "fcntl",
        types.SimpleNamespace(flock=fail, LOCK_SH=1, LOCK_NB=4, LOCK_UN=3),
    )
    proxy = AdmissionProxy(config(), traffic_lock_path=tmp_path / "requests.lock")
    if stage == "open":
        monkeypatch.setattr(Path, "open", fail)
    with caplog.at_level(logging.WARNING), proxy.request_guard() as admitted:
        assert not admitted
    assert f"stage={stage} reason={reason} errno={error_number}" in caplog.text
    assert "sensitive exception text" not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_proxy_wiring_holds_request_guard_around_every_turn() -> None:
    """The lock only protects production if the handler wiring routes every
    non-healthz request through request_guard and refuses held requests with
    503 maintenance_in_progress; the primitive test above proves neither. A
    regression here would silently re-introduce mid-update image replacement
    while every test stays green."""
    source = (Path(__file__).parents[1] / "scripts/remote/cpa-admission.py").read_text(
        encoding="utf-8"
    )
    trace = source.index("self._start_request_trace()")
    healthz = source.index('"/healthz"', trace)
    guard = source.index("with proxy.request_guard() as admitted:", trace)
    refusal = source.index("maintenance_in_progress", guard)
    unlocked_call = source.index("self._proxy_unlocked()", refusal)
    unlocked_def = source.index("def _proxy_unlocked", unlocked_call)
    # /healthz bypasses the guard; everything else enters it before any body
    # work, is refused with 503 while the updater holds the exclusive side,
    # and only the unlocked body runs inside the guard.
    assert trace < healthz < guard < refusal < unlocked_call < unlocked_def
    assert "close_connection = True" in source[guard:unlocked_call]


def risk_state_path(tmp_path: Path) -> Path:
    path = tmp_path / "antigravity-risk.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "not_before": 0,
                "hold_reason": None,
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("lane_name", ["antigravity-oauth", "claude-oauth"])
def test_antigravity_waits_full_backoff_and_preserves_long_wait_across_restart(
    lane_name: str,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    clock = [100.0]
    wall = [2000000000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "time", lambda: wall[0])
    loaded = dict(lane(claude_lane_config(), lane_name))
    loaded["queue_timeout_seconds"] = 0.01
    path = risk_state_path(tmp_path)
    state = LaneState(loaded, path)
    state.release(state.acquire(), capacity_error=True, retry_after=None)
    clock[0] += 11
    wall[0] += 11
    assert not state.acquire().admitted  # no ten-second early probe
    clock[0] += 50
    wall[0] += 50
    probe = state.acquire()
    assert probe.admitted and probe.probe
    state.release(probe, capacity_error=True, retry_after=100000)
    assert state.snapshot()["server_retry_after_remaining"] == 100000
    clock[0] += 86401
    wall[0] += 86401
    restarted = LaneState(loaded, path)
    assert not restarted.acquire().admitted
    assert restarted.snapshot()["server_retry_after_remaining"] == 13599
    clock[0] += 13600
    wall[0] += 13600
    resumed = restarted.acquire()
    assert resumed.admitted
    restarted.release(resumed, capacity_error=False, retry_after=None)
    assert json.loads(path.read_text())["not_before"] == 0


@pytest.mark.parametrize("lane_name", ["antigravity-oauth", "claude-oauth"])
def test_antigravity_manual_hold_survives_restart_and_stale_success(
    lane_name: str,
    tmp_path: Path,
) -> None:
    loaded = lane(claude_lane_config(), lane_name)
    path = risk_state_path(tmp_path)
    state = LaneState(loaded, path)
    rejected, outstanding = state.acquire(), state.acquire()
    state.release(
        rejected,
        capacity_error=False,
        retry_after=None,
        successful=False,
        hold_reason="authentication_required",
    )
    state.release(outstanding, capacity_error=False, retry_after=None)
    assert not state.acquire().admitted
    restarted = LaneState(loaded, path)
    assert restarted.acquire().reason == "operator_review_required"
    assert restarted.snapshot()["hold_reason"] == "authentication_required"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        LaneState(loaded, path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        LaneState(loaded, path)


@pytest.mark.parametrize("lane_name", ["antigravity-oauth", "claude-oauth"])
def test_antigravity_state_write_failure_stops_admission(
    lane_name: str,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    path = risk_state_path(tmp_path)
    state = LaneState(lane(claude_lane_config(), lane_name), path)
    outstanding = state.acquire()
    assert json.loads(path.read_text())["hold_reason"] == "unverified_request"

    def fail_replace(*args: Any) -> None:
        raise OSError("fixture: storage unavailable")

    monkeypatch.setattr(MODULE["os"], "replace", fail_replace)
    state.release(outstanding, capacity_error=True, retry_after=3600)
    assert state.acquire().reason == "operator_review_required"
    assert state.snapshot()["state_write_failed"]
    restarted = LaneState(lane(claude_lane_config(), lane_name), path)
    assert restarted.acquire().reason == "operator_review_required"


@pytest.mark.parametrize("lane_name", ["antigravity-oauth", "claude-oauth"])
def test_antigravity_refuses_upstream_when_write_ahead_cannot_be_saved(
    lane_name: str,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    path = risk_state_path(tmp_path)
    state = LaneState(lane(claude_lane_config(), lane_name), path)

    def fail_replace(*args: Any) -> None:
        raise OSError("fixture: storage unavailable")

    monkeypatch.setattr(MODULE["os"], "replace", fail_replace)
    refused = state.acquire()
    assert not refused.admitted and refused.reason == "operator_review_required"
    assert state.snapshot()["inflight"] == 0
    assert json.loads(path.read_text())["hold_reason"] is None


def test_google_retry_details_use_longest_window_and_ignore_generated_text() -> None:
    details = [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "1.1s"},
        {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "metadata": {"quotaResetDelay": "27h47m"},
        },
    ]
    error = json.dumps({"error": {"details": details}}).encode()
    assert antigravity_risk_signal(429, error) == (None, 100020)
    assert antigravity_risk_signal(200, b'{"text":"account suspended"}') == (None, None)
    assert antigravity_risk_signal(403, b"not json") == (
        "authentication_required",
        None,
    )
    probe = StreamErrorProbe(262144, lane(config(), "antigravity-oauth"))
    probe.feed(b'data: {"text":"hello"}\n\n' + b"data: " + error + b"\n\n")
    probe.finish()
    assert probe.retry_after == 100020
    assert probe.has_error
    restricted = json.dumps(
        {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "SERVICE_DISABLED",
                    }
                ]
            }
        }
    ).encode()
    assert antigravity_risk_signal(200, restricted) == ("account_restricted", None)


@pytest.mark.parametrize(
    "lane_name",
    ["antigravity-oauth", "claude-oauth", "chatgpt-oauth", "workbuddy-oauth"],
)
def test_antigravity_latches_sse_risk_before_eof_and_client_disconnect(
    tmp_path: Path,
    lane_name: str,
) -> None:
    finish = threading.Event()
    received: list[bytes] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"error":{"message":"account suspended"}}\n\n')
            self.wfile.flush()
            finish.wait(timeout=5)

        def log_message(self, *args: Any) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    loaded = claude_lane_config()
    loaded["upstream_port"] = upstream.server_port
    path = risk_state_path(tmp_path)
    proxy = AdmissionProxy(
        loaded,
        **{
            {
                "antigravity-oauth": "state_path",
                "claude-oauth": "claude_state_path",
                "chatgpt-oauth": "chatgpt_state_path",
                "workbuddy-oauth": "workbuddy_state_path",
            }[lane_name]: path
        },
    )
    server = AdmissionServer(("127.0.0.1", 0), proxy)
    threads = [threading.Thread(target=s.serve_forever) for s in (upstream, server)]
    for thread in threads:
        thread.start()
    first = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    second = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    model = {
        "antigravity-oauth": "gemini-3.8-flash-high",
        "claude-oauth": "claude-sonnet-5-5",
        "chatgpt-oauth": "gpt-6-luna",
        "workbuddy-oauth": "deepseek-v4.1-flash",
    }[lane_name]
    body = json.dumps({"model": model, "input": "hello"}).encode()
    try:
        first.request("POST", "/v1/responses", body)
        response = first.getresponse()
        assert response.status == 200
        deadline = time.monotonic() + 2
        while proxy.lanes[lane_name].snapshot()["hold_reason"] is None:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert not finish.is_set()  # upstream has not ended the response
        second.request("POST", "/v1/responses", body)
        rejected = second.getresponse()
        assert rejected.status == 403
        rejected.read()
        response.close()
        first.close()
        assert received == [body]
        assert json.loads(path.read_text())["hold_reason"] == "account_restricted"
    finally:
        finish.set()
        first.close()
        second.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for thread in threads:
            thread.join(timeout=2)


@pytest.mark.parametrize(
    "route,body",
    [
        ("/v1/messages", b'{"model":"gemini-3.8-flash-high"}'),
        ("/v1/completions", b'{"model":"gemini-3.8-flash-high"}'),
        ("/v1beta/interactions", b'{"model":"gemini-3.8-flash-high"}'),
        ("/v1beta/interactions", b'{"model":"models/gemini-3.8-flash-high(high)"}'),
        ("/v1/responses/compact", b'{"model":"gemini-3.8-flash-high"}'),
        ("/v1beta/models/gemini-3.8-flash-high:generateContent", b"{}"),
        (
            "/v1/models/gemini-3.8-flash-high%28high%29:streamGenerateContent?alt=sse",
            b"{}",
        ),
    ],
)
def test_all_generation_protocols_share_antigravity_admission(
    route: str, body: bytes
) -> None:
    assert requested_lane(route, body, config()) == (
        "antigravity-oauth",
        "gemini-3.8-flash-high",
    )


@pytest.mark.parametrize(
    "status,payload,content_type,expected",
    [
        (403, b"{}", "application/json", "operator_review_required"),
        (429, b"{}", "application/json", "cooldown"),
        (
            200,
            b'{"error":{"code":429,"status":"RESOURCE_EXHAUSTED"}}',
            "application/json",
            "cooldown",
        ),
        (
            429,
            b'{"error":{"details":[{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"100000s"}]}}',
            "application/json",
            "cooldown",
        ),
        (
            200,
            b'data: {"error":{"message":"service has been disabled for violation of terms"}}\n\n',
            "text/event-stream",
            "operator_review_required",
        ),
        (
            200,
            b'data: {"error":{"details":[{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"100000s"}]}}\n\n',
            "text/event-stream",
            "cooldown",
        ),
    ],
)
def test_antigravity_proxy_stops_after_risk_response_without_replaying(
    tmp_path: Path,
    status: int,
    payload: bytes,
    content_type: str,
    expected: str,
) -> None:
    received: list[bytes] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if content_type != "text/event-stream":
                self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_port
    lane(loaded, "antigravity-oauth")["queue_timeout_seconds"] = 0.01
    proxy = AdmissionProxy(loaded, state_path=risk_state_path(tmp_path))
    server = AdmissionServer(("127.0.0.1", 0), proxy)
    threads = [threading.Thread(target=s.serve_forever) for s in (upstream, server)]
    for thread in threads:
        thread.start()
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    body = b'{"model":"gemini-3.8-flash-high","messages":[]}'
    try:
        client.request("POST", "/v1/messages", body)
        response = client.getresponse()
        assert response.status == status
        assert response.read() == payload
        deadline = time.monotonic() + 2
        while proxy.lanes["antigravity-oauth"].snapshot()["inflight"]:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        client.request("POST", "/v1/messages", body)
        response = client.getresponse()
        assert response.status == (
            403 if expected == "operator_review_required" else 429
        )
        assert response.getheader("X-CPA-Admission-Reason") == expected
        if expected == "operator_review_required":
            assert response.getheader("Retry-After") is None
        else:
            assert int(response.getheader("Retry-After") or "0") >= (
                59 if b"100000" not in payload else 99999
            )
        response.read()
        assert received == [body]
    finally:
        client.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for thread in threads:
            thread.join(timeout=2)


def test_sse_reader_backpressures_slow_consumer_and_cancels_full_queue() -> None:
    class FastResponse:
        def __init__(self) -> None:
            self.reads = 0
            self.blocked = threading.Event()

        def read1(self, size: int) -> bytes:
            self.reads += 1
            if self.reads == MODULE["SSE_QUEUE_CHUNKS"] + 1:
                self.blocked.set()
            return b"x" * size

    response = FastResponse()
    events: queue.Queue[tuple[bytes, BaseException | None]] = queue.Queue(
        maxsize=MODULE["SSE_QUEUE_CHUNKS"]
    )
    stop = threading.Event()
    reader = threading.Thread(
        target=read_sse_upstream, args=(response, events, stop), daemon=True
    )
    reader.start()
    try:
        assert response.blocked.wait(2)
        assert events.qsize() == events.maxsize
        # The next read cannot run until a consumer makes space.
        assert response.reads == events.maxsize + 1
        first, error = events.get(timeout=2)
        assert first == b"x" * 65536
        assert error is None
    finally:
        stop.set()
        reader.join(timeout=2)
    assert not reader.is_alive()


@pytest.mark.parametrize("failure", [None, OSError("upstream reset")])
def test_sse_reader_preserves_chunks_eof_and_errors(
    failure: BaseException | None,
) -> None:
    class Response:
        def __init__(self) -> None:
            self.chunks = iter([b"data: first\n\n", b"data: second\n\n"])

        def read1(self, size: int) -> bytes:
            chunk = next(self.chunks, b"")
            if not chunk and failure is not None:
                raise failure
            return chunk

    events: queue.Queue[tuple[bytes, BaseException | None]] = queue.Queue(maxsize=1)
    stop = threading.Event()
    reader = threading.Thread(
        target=read_sse_upstream, args=(Response(), events, stop), daemon=True
    )
    reader.start()
    try:
        assert events.get(timeout=2) == (b"data: first\n\n", None)
        assert events.get(timeout=2) == (b"data: second\n\n", None)
        if failure is not None:
            assert events.get(timeout=2) == (b"", failure)
        assert events.get(timeout=2) == (b"", None)
    finally:
        stop.set()
        reader.join(timeout=2)
    assert not reader.is_alive()


def config() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        load_config(
            Path(__file__).parents[1] / "scripts" / "remote" / "cpa-admission.json"
        ),
    )


def claude_lane_config() -> dict[str, Any]:
    """The production claude-oauth lane retired 2026-10-20 (account
    suspended); these tests keep the risk/backoff machinery covered through
    a synthetic lane identical to the retired one, decoupled from the real
    admission.json."""
    loaded = json.loads(
        (
            Path(__file__).parents[1] / "scripts" / "remote" / "cpa-admission.json"
        ).read_text(encoding="utf-8")
    )
    retired = {
        "name": "claude-oauth",
        "models": [
            "claude-opus-5-5",
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
        ],
        "max_inflight": 2,
        "max_pending": 4,
        "queue_timeout_seconds": 120,
        "cooldown_schedule_seconds": [60, 120, 240, 480, 900],
        "cooldown_cap_seconds": 900,
        "capacity_statuses": [429, 503, 529],
        "model_capacity_markers": [],
        "capacity_markers": [
            "server_is_overloaded",
            "overloaded_error",
            "rate limit",
            "rate_limit",
            "too many requests",
            "resource exhausted",
            "resource_exhausted",
            "quota exceeded",
            "usage limit",
            "usage_limit_reached",
        ],
    }
    # Re-run load_config over the retired lane so the top-level merges
    # (retry_after_max_seconds etc.) apply exactly as production did.
    synthetic = {**loaded, "lanes": list(loaded["lanes"]) + [retired]}
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    try:
        json.dump(synthetic, handle)
        handle.close()
        return cast(dict[str, Any], load_config(Path(handle.name)))
    finally:
        os.unlink(handle.name)


def lane(loaded: dict[str, Any], name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        next(item for item in loaded["lanes"] if item["name"] == name),
    )


def test_config_freezes_shared_official_account_lanes() -> None:
    loaded = config()
    assert loaded["listen_host"] == "127.0.0.1"
    assert loaded["listen_port"] == 8318
    assert loaded["upstream_port"] == 8317
    assert loaded["retry_after_max_seconds"] == 86400
    assert set(loaded["model_lanes"]) == ADMISSION_MODEL_LANES
    # The claude-oauth lane retired 2026-10-20 (account suspended); the
    # synthetic lane used by the risk-machinery tests is not part of the
    # real file contract asserted here.
    assert [item["name"] for item in loaded["lanes"]] == [
        "chatgpt-oauth",
        "zhipu-coding-plan",
        "antigravity-oauth",
        "workbuddy-oauth",
    ]
    expected_max_inflight = {
        "chatgpt-oauth": 2,
        "zhipu-coding-plan": 3,
        "antigravity-oauth": 2,
        "workbuddy-oauth": 1,
    }
    for loaded_lane in loaded["lanes"]:
        # The OAuth lane is one shared subscription account, so its bound is
        # deliberately small -- but not one: a turn runs 39-216s upstream, and
        # single-flight made every concurrent request wait out the whole turn
        # and then fail at the queue budget. API-key lanes have independent
        # consumption and keep their own bound.
        assert loaded_lane["max_inflight"] == expected_max_inflight[loaded_lane["name"]]
        assert loaded_lane["max_pending"] == 4
        # The queue budget must outlast a typical upstream turn, not just its
        # first few seconds.
        assert loaded_lane["queue_timeout_seconds"] == 120
        assert loaded_lane["cooldown_schedule_seconds"] == (
            60,
            120,
            240,
            480,
            900,
        )
        assert loaded_lane["early_probe_interval_seconds"] == 10
    health = AdmissionProxy(loaded).health()
    assert health["retry_after_max_seconds"] == 86400
    assert health["retired_readers"] == 0
    assert health["retired_readers_total"] == 0
    assert health["lanes"]["chatgpt-oauth"]["max_inflight"] == 2
    assert health["lanes"]["zhipu-coding-plan"]["max_inflight"] == 3


def test_oauth_lane_queues_a_concurrent_turn_instead_of_opening_parallel_upstream_calls() -> (
    None
):
    """The bound, not one, is what must queue: a turn takes 39-216s upstream.

    Single-flight starved every concurrent request for a whole turn and then
    rejected it at the 120s queue budget, which is why the bound is now the
    lane's declared `max_inflight` rather than a hardcoded 1.
    """

    loaded = config()
    state = LaneState(lane(loaded, "chatgpt-oauth"))
    bound = lane(loaded, "chatgpt-oauth")["max_inflight"]
    assert bound == 2

    held = [state.acquire() for _ in range(bound)]
    assert all(lease.admitted for lease in held)
    assert state.inflight == bound

    result: list[Any] = []

    def wait_for_slot() -> None:
        result.append(state.acquire())

    waiter = threading.Thread(target=wait_for_slot)
    waiter.start()
    deadline = time.monotonic() + 2
    while state.pending != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.pending == 1
    assert state.inflight == bound
    assert not result

    state.release(held.pop(), capacity_error=False, retry_after=None)
    waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert len(result) == 1
    assert result[0].admitted
    assert state.inflight == bound
    for lease in held + result:
        state.release(lease, capacity_error=False, retry_after=None)


def test_policy_accepts_admission_contract_and_rejects_lane_drift() -> None:
    config_path = (
        Path(__file__).parents[1] / "scripts" / "remote" / "cpa-admission.json"
    )
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    manifest = json.loads(
        (
            Path(__file__).parents[1]
            / "scripts"
            / "remote"
            / "cpa_provider_routes.json"
        ).read_text(encoding="utf-8")
    )
    policy = runpy.run_path(
        str(Path(__file__).parents[1] / "scripts" / "remote" / "cpa_policy.py")
    )
    assert policy["_admission_config_issues"](manifest, raw_config) == []

    drifted = json.loads(json.dumps(raw_config))
    drifted["lanes"][0]["models"].append("gpt-6-astra")
    issues = policy["_admission_config_issues"](manifest, drifted)
    assert any("admission lane 'chatgpt-oauth' models=" in issue for issue in issues)

    drifted_interval = json.loads(json.dumps(raw_config))
    drifted_interval["early_probe_interval_seconds"] = 0
    issues = policy["_admission_config_issues"](manifest, drifted_interval)
    assert any("early_probe_interval_seconds" in issue for issue in issues)

    drifted_interval["early_probe_interval_seconds"] = "60"
    issues = policy["_admission_config_issues"](manifest, drifted_interval)
    assert any("early_probe_interval_seconds" in issue for issue in issues)


def test_requested_lane_only_admits_shared_generation_routes() -> None:
    loaded = config()
    assert requested_lane(
        "/v1/responses",
        b'{"model":"gpt-6-luna","input":"hello"}',
        loaded,
    ) == ("chatgpt-oauth", "gpt-6-luna")
    assert requested_lane(
        "/v1/chat/completions",
        b'{"model":"GLM-5.3-FLASH","messages":[]}',
        loaded,
    ) == ("zhipu-coding-plan", "glm-5.3-flash")
    assert requested_lane(
        "/v1/responses",
        b'{"model":"deepseek-v4.1-flash","input":"hello"}',
        loaded,
    ) == ("workbuddy-oauth", "deepseek-v4.1-flash")
    assert (
        requested_lane(
            "/v1/responses",
            b'{"model":"muse-spark-1.3-contributor","input":"hello"}',
            loaded,
        )
        is None
    )
    assert requested_lane("/v1/models", b'{"model":"gpt-6-luna"}', loaded) is None
    assert (
        requested_lane(
            "/v1/responses",
            b'{"model":"gpt-6.1-sol-91","input":"hello"}',
            loaded,
        )
        is None
    )
    assert requested_lane("/v1/responses", b"not-json", loaded) is None


@pytest.mark.parametrize("route", ["/v1/responses", "/v1/chat/completions"])
@pytest.mark.parametrize(
    ("model", "lane", "base"),
    [
        ("gpt-6-luna(high)", "chatgpt-oauth", "gpt-6-luna"),
        ("gpt-6.1-sol(low)", "chatgpt-oauth", "gpt-6.1-sol"),
        ("gpt-5.6-luna(8192)", "chatgpt-oauth", "gpt-5.6-luna"),
        ("GLM-5.3-FLASH (HIGH)", "zhipu-coding-plan", "glm-5.3-flash"),
        ("gpt-6-luna()", "chatgpt-oauth", "gpt-6-luna"),
    ],
)
def test_thinking_suffix_cannot_bypass_shared_lane(
    route: str, model: str, lane: str, base: str
) -> None:
    body = json.dumps({"model": model, "input": "hello"}).encode()
    assert requested_lane(route, body, config()) == (lane, base)
    assert json.loads(body)["model"] == model


def test_suffix_does_not_capture_unrelated_or_unresolved_models() -> None:
    for model in ("gpt-6.1-sol-91(high)", "gpt-6-luna(high", "gpt-6-luna(high)(low)"):
        body = json.dumps({"model": model}).encode()
        assert requested_lane("/v1/responses", body, config()) is None


def test_requested_model_names_pass_through_models_for_attribution() -> None:
    """A model outside every lane must still be attributable in the journal.

    `requested_lane` deliberately returns None for pass-through traffic, so the
    log lines used to collapse every such request into `model=other`. That hid
    which model a burst of pass-through 5xx belonged to, which is the only
    question such a burst raises.
    """

    assert (
        requested_model("/v1/responses", b'{"model":"gpt-6-sol-91","input":"hello"}')
        == "gpt-6-sol-91"
    )
    # Normalisation is shared with lane matching, so casing cannot fork the two.
    assert (
        requested_model(
            "/v1/chat/completions", b'{"model":"GLM-5.3-FLASH","messages":[]}'
        )
        == "glm-5.3-flash"
    )
    # Unparseable and unnameable bodies stay unattributable rather than guessed.
    assert requested_model("/v1/responses", b"not-json") is None
    assert requested_model("/v1/responses", b'{"model":"   "}') is None
    assert requested_model("/v1/responses", b'{"model":42}') is None
    assert requested_model("/v1/models", b'{"model":"gpt-6-luna"}') is None
    assert requested_model("/v1/responses", b"") is None


def test_capacity_classifier_uses_status_and_markers_without_rewriting() -> None:
    loaded = config()
    loaded_lane = lane(loaded, "zhipu-coding-plan")
    assert is_capacity_response(503, b"", None, loaded_lane)
    chatgpt_lane = lane(loaded, "chatgpt-oauth")
    assert is_capacity_response(
        200, b'{"error":"Selected model is at capacity"}', None, chatgpt_lane
    )
    assert is_capacity_response(429, b"upstream", "7", loaded_lane)
    assert not is_capacity_response(
        500, b'{"error":"invalid request"}', None, loaded_lane
    )


def test_stream_probe_detects_late_errors_and_preserves_scope() -> None:
    probe = StreamErrorProbe(262144, lane(config(), "chatgpt-oauth"))
    benign = b'data: {"type":"response.output_text.delta","delta":"rate limit"}\r\n\r\n'
    for _ in range(5000):
        probe.feed(benign)
    assert not probe.has_error
    assert probe.scope is None
    error = (
        b'data: {"type":"response.failed",\r\n'
        b'data: "response":{"error":{"code":"model_at_capacity"}}}\r\n\r\n'
    )
    for byte in error:
        probe.feed(bytes([byte]))
    assert probe.scope == "model"
    probe.feed(b'data: {"error":{"code":"usage_limit_reached"}}')
    probe.finish()
    assert probe.scope == "lane"
    assert probe.has_error and probe.complete


def test_stream_probe_bounds_oversized_event_and_resumes_at_next_event() -> None:
    probe = StreamErrorProbe(128, lane(config(), "chatgpt-oauth"))
    probe.feed(b"data: " + b"x" * 10000)
    assert len(probe.buffer) <= 128
    assert len(probe.data) <= 128
    probe.feed(b'\n\ndata: {"error":"server_is_overloaded"}\n\n')
    probe.finish()
    assert probe.scope == "lane"
    assert probe.has_error
    assert not probe.complete


@pytest.mark.parametrize("code", ["model_not_found", "authentication_error"])
def test_stream_probe_does_not_treat_known_noncapacity_error_as_overload(
    code: str,
) -> None:
    probe = StreamErrorProbe(262144, lane(config(), "chatgpt-oauth"), 503)
    probe.feed(b"data: " + json.dumps({"error": {"code": code}}).encode() + b"\n\n")
    probe.finish()
    assert probe.has_error and probe.scope is None


def test_stream_probe_records_bounded_sse_event_metadata() -> None:
    probe = StreamErrorProbe(
        4096, lane(config(), "chatgpt-oauth"), path="/v1/responses"
    )
    first = b'data: {"type":"response.output_text.delta"}\n\n'
    opaque = b'data: {"type":"vendor-private-event-91f5","secret":"hidden"}\n\n'
    terminal = (
        b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
    )

    probe.feed(first)
    assert probe.last_event_type == "response.output_text.delta"
    probe.feed(opaque)
    assert probe.last_event_type == "other"
    assert "vendor-private-event-91f5" not in probe.last_event_type
    probe.feed(terminal)
    probe.finish()

    assert probe.bytes_received == len(first) + len(opaque) + len(terminal)
    assert probe.event_count == 3
    assert probe.last_event_type == "response.completed"
    assert probe.terminal_success


def test_protocol_result_reason_distinguishes_terminal_and_safe_failure_classes() -> (
    None
):
    loaded = config()
    chat_lane = lane(loaded, "chatgpt-oauth")
    helper = MODULE["protocol_result_reason"]

    completed = StreamErrorProbe(1024, chat_lane, path="/v1/responses")
    completed.feed(
        b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
    )
    completed.finish()
    assert (
        helper(
            status=200,
            successful=True,
            capacity_error=False,
            client_lost=False,
            stream_probe=completed,
            body_prefix=b"",
            lane_name="chatgpt-oauth",
        )
        == "completed"
    )

    missing = StreamErrorProbe(1024, chat_lane, path="/v1/responses")
    missing.feed(b'data: {"type":"response.output_text.delta"}\n\n')
    missing.finish()
    assert (
        helper(
            status=200,
            successful=False,
            capacity_error=False,
            client_lost=False,
            stream_probe=missing,
            body_prefix=b"",
            lane_name="chatgpt-oauth",
        )
        == "missing_terminal"
    )

    malformed = StreamErrorProbe(1024, chat_lane, path="/v1/responses")
    malformed.feed(b"data: {not-json}\n\n")
    malformed.finish()
    assert (
        helper(
            status=200,
            successful=False,
            capacity_error=False,
            client_lost=False,
            stream_probe=malformed,
            body_prefix=b"",
            lane_name="chatgpt-oauth",
        )
        == "malformed_event"
    )

    assert (
        helper(
            status=503,
            successful=False,
            capacity_error=True,
            client_lost=False,
            stream_probe=None,
            body_prefix=b"",
            lane_name="chatgpt-oauth",
        )
        == "capacity"
    )
    assert (
        helper(
            status=200,
            successful=False,
            capacity_error=False,
            client_lost=True,
            stream_probe=None,
            body_prefix=b"",
            lane_name="chatgpt-oauth",
        )
        == "client_disconnect"
    )


def test_lane_early_probe_respects_model_retry_after(monkeypatch: Any) -> None:
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    failed = state.acquire(model="gpt-6-luna")
    state.release(failed, capacity_error=True, retry_after=3600, capacity_scope="model")
    for _ in range(2):
        failed = state.acquire(model="gpt-6.1-sol")
        state.release(failed, capacity_error=True, retry_after=None)
    clock[0] += 11
    blocked = state.acquire(model="gpt-6-luna")
    assert not blocked.admitted and blocked.reason == "model_cooldown"
    assert blocked.retry_after == 3589
    assert state.inflight == 0 and not state.probe_inflight
    sibling = state.acquire(model="gpt-6.1-sol")
    assert sibling.admitted and sibling.reason == "early_probe"
    state.release(sibling, capacity_error=False, retry_after=None)
    assert not state.acquire(model="gpt-6-luna").admitted
    clock[0] += 3590
    recovered = state.acquire(model="gpt-6-luna")
    assert recovered.admitted
    state.release(recovered, capacity_error=False, retry_after=None)


def test_retry_after_accepts_seconds_and_http_date() -> None:
    assert parse_retry_after("12") == 12
    assert parse_retry_after("0") == 1
    now = datetime.now(timezone.utc).timestamp()
    future = datetime.now(timezone.utc) + timedelta(seconds=4)
    value = parse_retry_after(future.strftime("%a, %d %b %Y %H:%M:%S GMT"), now)
    assert value in {3, 4}
    assert parse_retry_after("not-a-date") is None


def test_lane_absorbs_a_lone_blip_and_opens_after_a_proven_streak() -> None:
    # A single upstream blip must not black out the lane. The upstream
    # advertises transient `server_is_overloaded` 503s that clear within
    # seconds, so the breaker waits for a consecutive streak before opening.
    loaded = config()
    # A cooldown shorter than the queue budget is now held rather than bounced,
    # so pin the budget low here to keep exercising the outright-refusal path.
    state = LaneState({**lane(loaded, "chatgpt-oauth"), "queue_timeout_seconds": 5})

    first = state.acquire()
    assert first.admitted
    state.release(first, capacity_error=True, retry_after=None)
    after_blip = state.acquire()
    assert after_blip.admitted
    assert state.snapshot()["cooldown_remaining"] == 0

    # The second consecutive failure is a proven outage: the breaker opens.
    state.release(after_blip, capacity_error=True, retry_after=None)
    blocked = state.acquire()
    assert not blocked.admitted
    assert blocked.reason == "cooldown"
    assert blocked.retry_after >= 1

    state.open_until = time.monotonic() - 1
    probe = state.acquire()
    assert probe.admitted and probe.probe
    queued: list[Any] = []
    waiter = threading.Thread(target=lambda: queued.append(state.acquire()))
    waiter.start()
    deadline = time.monotonic() + 2
    while state.pending != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.pending == 1
    assert not queued
    state.release(probe, capacity_error=False, retry_after=None)
    waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert len(queued) == 1
    recovered = queued[0]
    assert recovered.admitted and not recovered.probe
    state.release(recovered, capacity_error=False, retry_after=None)


def test_lane_resets_the_failure_streak_after_a_success() -> None:
    # A success between two failures means the outage never materialised, so
    # the streak restarts and the breaker stays shut.
    loaded = config()
    state = LaneState(lane(loaded, "chatgpt-oauth"))

    first = state.acquire()
    state.release(first, capacity_error=True, retry_after=None)
    second = state.acquire()
    assert second.admitted
    state.release(second, capacity_error=False, retry_after=None)
    assert state.snapshot()["failure_streak"] == 0

    third = state.acquire()
    state.release(third, capacity_error=True, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 0


def test_upstream_retry_after_opens_the_breaker_on_first_failure(
    monkeypatch: Any,
) -> None:
    # An upstream that advertises Retry-After is explicitly telling us to back
    # off, so honour it immediately instead of waiting for the streak.
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded = config()
    state = LaneState(lane(loaded, "zhipu-coding-plan"))
    lease = state.acquire()
    assert lease.admitted
    state.release(lease, capacity_error=True, retry_after=45)
    snapshot = state.snapshot()
    assert snapshot["failure_streak"] == 1
    assert 1 <= snapshot["cooldown_remaining"] <= 45

    # A cooldown longer than the queue budget cannot be waited out, so the
    # arrival is refused outright with the advertised window.
    refused_state = LaneState(
        {**lane(loaded, "zhipu-coding-plan"), "queue_timeout_seconds": 1}
    )
    refused_state.open_until = clock[0] + 45
    refused_state.failure_streak = 1
    refused_state._next_probe_at = clock[0] + 1e9
    refused = refused_state.acquire()
    assert not refused.admitted
    assert refused.reason == "cooldown"
    assert refused.retry_after == 45

    # A cooldown that fits the budget is held instead of bounced: the arrival
    # waits the advertised backoff out and is then admitted as the recovery
    # probe, so the client never receives a 429 it cannot wait out.
    held = LaneState(lane(loaded, "zhipu-coding-plan"))
    held.open_until = clock[0] + 45
    held.failure_streak = 1
    held._next_probe_at = clock[0] + 1e9
    original_wait = held._condition.wait

    def advance(timeout: float | None = None) -> bool:
        clock[0] = 145.0  # the advertised window has passed
        held._next_probe_at = 0.0
        return True

    held._condition.wait = advance
    try:
        admitted = held.acquire()
    finally:
        held._condition.wait = original_wait
    assert admitted.admitted and admitted.probe
    assert admitted.waited_ms >= 45_000
    assert held.pending == 0


def test_cooldown_holds_a_fitting_arrival_instead_of_bouncing_it(
    monkeypatch: Any,
) -> None:
    """A cooldown shorter than the queue budget must not become a client 429.

    The observed client does not wait out a Retry-After -- it fails the whole
    turn -- so the gateway holds the request and sends it once the window
    opens. That protects the upstream exactly as much as the advertised
    backoff would, without spending the client's retry budget.
    """
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    state.open_until = clock[0] + 40
    state._next_probe_at = clock[0] + 1e9
    state.failure_streak = 1
    original_wait = state._condition.wait

    def advance(timeout: float | None = None) -> bool:
        clock[0] = 140.0  # the advertised window has now elapsed
        state._next_probe_at = 0.0
        return True

    state._condition.wait = advance
    try:
        lease = state.acquire()
    finally:
        state._condition.wait = original_wait
    assert lease.admitted and lease.probe
    assert lease.waited_ms >= 40_000
    assert state.pending == 0


def test_long_local_cooldown_waits_for_eligible_early_probe(monkeypatch: Any) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    state.open_until = 1000.0
    state._next_probe_at = 110.0
    state.failure_streak = 6

    def advance(timeout: float | None = None) -> bool:
        clock[0] = 110.0
        return True

    monkeypatch.setattr(state._condition, "wait", advance)
    admitted = state.acquire()
    assert admitted.admitted and admitted.probe
    assert admitted.waited_ms == 10_000
    assert state.pending == 0
    assert state.inflight == 1
    state.release(admitted, capacity_error=False, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 0


def test_lane_probes_only_a_locally_chosen_cooldown_early() -> None:
    loaded = config()
    loaded_lane = dict(lane(loaded, "chatgpt-oauth"))
    # Keep the next probe beyond the waiter's queue budget so this assertion
    # does not race a legitimate second probe under a busy test runner.
    loaded_lane["early_probe_interval_seconds"] = 1.0
    loaded_lane["queue_timeout_seconds"] = 0.5
    state = LaneState(loaded_lane)

    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=None)
    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] > 55

    # Inside the first interval the client still sees an honest cooldown
    # rejection carrying the advertised window.
    blocked = state.acquire()
    assert not blocked.admitted
    assert blocked.reason == "cooldown"
    assert blocked.retry_after >= 55

    # After one interval the lane verifies recovery with a single probe.
    time.sleep(1.1)
    probe = state.acquire()
    assert probe.admitted and probe.probe
    # Concurrent demand during the probe waits in the bounded queue instead of
    # becoming a fast half_open_probe 429. A failed probe keeps the breaker
    # open and the waiter eventually receives the normal queue-timeout result.
    during: list[Any] = []
    waiter = threading.Thread(target=lambda: during.append(state.acquire()))
    waiter.start()
    deadline = time.monotonic() + 2
    while state.pending != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.pending == 1
    state.release(probe, capacity_error=True, retry_after=None)
    waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert len(during) == 1
    assert not during[0].admitted
    assert during[0].reason == "queue_timeout"
    # The shed must describe the lane's next recheck, not a 1s "retry now"
    # that would only re-queue the client for another full budget.
    assert during[0].retry_after > 1
    assert state.snapshot()["cooldown_remaining"] > 0

    # A successful probe heals the lane immediately.
    time.sleep(1.1)
    healed = state.acquire()
    assert healed.admitted and healed.probe
    state.release(healed, capacity_error=False, retry_after=None)
    normal = state.acquire()
    assert normal.admitted and not normal.probe
    state.release(normal, capacity_error=False, retry_after=None)


def test_explicit_retry_after_is_capped_and_not_bypassed_by_early_probe(
    monkeypatch: Any,
) -> None:
    real_monotonic = time.monotonic
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded = config()
    state = LaneState(lane(loaded, "chatgpt-oauth"))
    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=100000)
    max_retry_after = lane(loaded, "chatgpt-oauth")["retry_after_max_seconds"]
    assert state.snapshot()["cooldown_remaining"] <= max_retry_after

    # A provider value inside the safety bound is still honoured in full; the
    # early probe must not bypass it.
    clock[0] = 86300.0
    refused = state.acquire()
    assert not refused.admitted
    assert refused.reason == "cooldown"

    # Once the bounded window opens, the next arrival becomes the recovery
    # probe.  The old implementation would have kept this blocked for the
    # unbounded 100000-second provider value.
    clock[0] = 86501.0
    held: list[Any] = []
    waiter = threading.Thread(target=lambda: held.append(state.acquire()))
    waiter.start()
    deadline = real_monotonic() + 2
    while not held and real_monotonic() < deadline:
        time.sleep(0.005)
    waiter.join(timeout=3)
    assert not waiter.is_alive()
    assert len(held) == 1
    assert held[0].admitted and held[0].probe


def test_quota_reset_probe_can_heal_before_old_server_deadline(
    monkeypatch: Any,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=3600)
    assert not state.acquire().admitted
    generation = state.snapshot()["failure_generation"]
    assert state.acquire_after_reset(generation - 1).reason == "stale_generation"
    probe = state.acquire_after_reset(generation)
    assert probe.admitted and probe.probe and probe.reset_probe
    assert state.acquire_after_reset(generation).reason == "busy"
    state.release(probe, capacity_error=False, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 0
    assert state.snapshot()["server_retry_after_remaining"] == 0
    normal = state.acquire()
    assert normal.admitted and not normal.probe
    state.release(normal, capacity_error=False, retry_after=None)


def test_failed_reset_probe_preserves_backoff_and_limits_repetition(
    monkeypatch: Any,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=3600)
    generation = state.snapshot()["failure_generation"]
    probe = state.acquire_after_reset(generation)
    state.release(probe, capacity_error=False, retry_after=None, successful=False)
    assert state.snapshot()["cooldown_remaining"] == 3600
    assert state.acquire_after_reset(generation).reason == "reset_probe_throttled"
    clock[0] = 401.0
    probe = state.acquire_after_reset(generation)
    state.release(probe, capacity_error=True, retry_after=7200)
    assert state.snapshot()["cooldown_remaining"] == 7200
    assert state.acquire_after_reset(generation).reason == "stale_generation"
    assert not state.acquire().admitted


def test_reset_probe_success_cannot_clear_a_newer_capacity_event(
    monkeypatch: Any,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=3600)
    probe = state.acquire_after_reset(state.snapshot()["failure_generation"])
    newer = MODULE["Lease"](True, "admitted", 0)
    state.release(newer, capacity_error=True, retry_after=7200)
    state.release(probe, capacity_error=False, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 7200
    assert state.snapshot()["failure_streak"] == 2


def test_reset_probe_refuses_a_lane_with_outstanding_work() -> None:
    state = LaneState(lane(config(), "chatgpt-oauth"))
    first = state.acquire()
    held = state.acquire()
    state.release(first, capacity_error=True, retry_after=3600)
    assert (
        state.acquire_after_reset(state.snapshot()["failure_generation"]).reason
        == "busy"
    )
    state.release(held, capacity_error=False, retry_after=None)
    state.pending = 1
    assert (
        state.acquire_after_reset(state.snapshot()["failure_generation"]).reason
        == "busy"
    )
    state.pending = 0


def test_reset_probe_requires_completed_output_not_just_http_success() -> None:
    completed = MODULE["completed_reset_probe"]
    good = {
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "OK"}]}
        ],
    }
    assert completed(json.dumps(good).encode())
    for bad in (
        {**good, "status": "incomplete"},
        {**good, "error": {"type": "usage_limit_reached"}},
        {**good, "output": []},
        {"output_text": "OK"},
        {"status": "completed", "output": [None]},
        {"status": "completed", "output": None},
    ):
        assert not completed(json.dumps(bad).encode())
    assert not completed(b"data: " + json.dumps(good).encode())


def test_unsuccessful_probe_preserves_cooldown(monkeypatch: Any) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState({**lane(config(), "chatgpt-oauth"), "queue_timeout_seconds": 1})
    for _ in range(2):
        lease = state.acquire()
        state.release(lease, capacity_error=True, retry_after=None)
    clock[0] = 111.0
    probe = state.acquire()
    assert probe.probe
    state.release(probe, capacity_error=False, retry_after=None, successful=False)
    assert state.snapshot()["cooldown_remaining"] == 49
    assert state.snapshot()["failure_streak"] == 2
    # The remaining cooldown exceeds this lane's queue budget, so the arrival
    # is refused outright instead of being held.
    assert not state.acquire().admitted


def test_half_open_probe_respects_inflight_bound() -> None:
    loaded_lane = dict(lane(config(), "chatgpt-oauth"))
    loaded_lane["max_inflight"] = 1
    loaded_lane["queue_timeout_seconds"] = 0.02
    state = LaneState(loaded_lane)
    held = state.acquire()
    state.open_until = time.monotonic() - 1
    rejected = state.acquire()
    assert not rejected.admitted
    assert rejected.reason == "queue_timeout"
    assert state.inflight == 1
    assert not state.probe_inflight
    state.release(held, capacity_error=False, retry_after=None)


def test_successful_probe_does_not_clear_newer_retry_after(monkeypatch: Any) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    # A one-second budget keeps the final arrival on the outright-refusal path
    # instead of holding it for the whole cooldown.
    state = LaneState({**lane(config(), "chatgpt-oauth"), "queue_timeout_seconds": 1})
    first = state.acquire()
    concurrent = state.acquire()
    state.release(first, capacity_error=True, retry_after=None)
    second = state.acquire()
    state.release(second, capacity_error=True, retry_after=None)
    clock[0] = 111.0
    probe = state.acquire()
    assert probe.admitted and probe.probe
    state.release(concurrent, capacity_error=True, retry_after=60)
    state.release(probe, capacity_error=False, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 60
    assert state.snapshot()["failure_streak"] == 3
    assert not state.acquire().admitted


def test_new_arrival_cannot_overtake_a_queued_request() -> None:
    loaded_lane = dict(lane(config(), "chatgpt-oauth"))
    loaded_lane["max_pending"] = 1
    loaded_lane["queue_timeout_seconds"] = 1
    state = LaneState(loaded_lane)
    held = [state.acquire() for _ in range(loaded_lane["max_inflight"])]
    results: list[Any] = []
    waiter = threading.Thread(target=lambda: results.append(state.acquire()))
    newcomer = None
    waiter.start()
    try:
        deadline = time.monotonic() + 2
        while state.pending != 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert state.pending == 1
        with state._condition:
            state.release(held.pop(), capacity_error=False, retry_after=None)
            newcomer = state.acquire()
        assert not newcomer.admitted
        assert newcomer.reason == "busy"
    finally:
        if newcomer is not None and newcomer.admitted:
            state.release(newcomer, capacity_error=False, retry_after=None)
        waiter.join(timeout=2)
        for acquired in held + results:
            if acquired.admitted:
                state.release(acquired, capacity_error=False, retry_after=None)
    assert not waiter.is_alive()
    assert len(results) == 1
    assert results[0].admitted
    assert state.pending == 0


def test_queued_request_is_not_admitted_after_its_deadline(monkeypatch: Any) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded_lane = dict(lane(config(), "chatgpt-oauth"))
    loaded_lane["queue_timeout_seconds"] = 1
    state = LaneState(loaded_lane)
    held = [state.acquire() for _ in range(loaded_lane["max_inflight"])]

    def expire_and_release(timeout: float) -> None:
        clock[0] = 102.0
        state.release(held.pop(), capacity_error=False, retry_after=None)

    monkeypatch.setattr(state._condition, "wait", expire_and_release)
    rejected = state.acquire()
    assert not rejected.admitted
    assert rejected.reason == "queue_timeout"
    assert state.pending == 0
    for acquired in held:
        state.release(acquired, capacity_error=False, retry_after=None)


def test_shed_rejection_advertises_the_next_lane_recheck(monkeypatch: Any) -> None:
    """A shed must not advertise a 1s retry.

    A request that already burned its whole queue budget is only re-queued by
    an immediate retry, so the advertised interval has to describe when the
    lane will actually re-evaluate: the later of the cooldown expiry and the
    next probe slot, with the probe cadence as a floor.
    """
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))

    # Healthy lane, nothing scheduled: the probe cadence is the floor.
    assert state._shed_retry_after(clock[0]) == 10

    # A cooldown longer than the cadence is the honest answer.
    state.open_until = clock[0] + 45
    assert state._shed_retry_after(clock[0]) == 45

    # A pending probe slot beyond the cooldown also delays the next recheck.
    state.open_until = clock[0] + 5
    state._next_probe_at = clock[0] + 30
    assert state._shed_retry_after(clock[0]) == 30


def test_capacity_classifier_ignores_generated_text_and_routing_errors() -> None:
    loaded_lane = lane(config(), "chatgpt-oauth")
    for body in (
        b'{"output_text":"Explain rate limit and server_is_overloaded"}',
        b'data: {"type":"response.output_text.delta","delta":"rate limit"}\n\n',
    ):
        assert not is_capacity_response(200, body, None, loaded_lane)
    assert not is_capacity_response(
        503, b'{"error":{"code":"model_not_found"}}', None, loaded_lane
    )
    assert is_capacity_response(
        200,
        b'data: {"type":"response.failed","response":{"error":{"code":"server_is_overloaded"}}}\n\n',
        None,
        loaded_lane,
    )


def test_capacity_scope_separates_model_signals_from_credential_signals() -> None:
    # Measured 2026-10-10: a `gpt-6-luna` capacity event parked the whole
    # `chatgpt-oauth` lane, so the next `gpt-6.1-sol` request was refused with
    # `retry_after=471` even though the credential had served it seconds
    # earlier. Only a marker that names a model may be model-scoped; everything
    # that describes the credential or the account stays lane-scoped.
    loaded = config()
    chatgpt = lane(loaded, "chatgpt-oauth")
    assert (
        capacity_scope(200, b'{"error":"Selected model is at capacity"}', None, chatgpt)
        == "model"
    )
    assert capacity_scope(429, b'{"error":"model_at_capacity"}', None, chatgpt) == (
        "model"
    )
    assert (
        capacity_scope(503, b'{"error":{"code":"server_is_overloaded"}}', None, chatgpt)
        == "lane"
    )
    # Mixed evidence must fail closed to the credential scope: a model marker
    # cannot override an account-level overload marker in the same response.
    assert (
        capacity_scope(
            503,
            b'{"error":{"code":"server_is_overloaded",'
            b'"message":"Selected model is at capacity"}}',
            None,
            chatgpt,
        )
        == "lane"
    )
    assert capacity_scope(429, b'{"error":"usage_limit_reached"}', None, chatgpt) == (
        "lane"
    )
    assert capacity_scope(429, b"upstream", "7", chatgpt) == "lane"
    assert (
        capacity_scope(400, b'{"error":{"code":"model_not_found"}}', None, chatgpt)
        is None
    )
    assert (
        capacity_scope(401, b'{"error":{"type":"authentication_error"}}', None, chatgpt)
        is None
    )
    # A model-scoped marker must stay a subset of the lane's capacity markers:
    # otherwise a credential-level signal could be smuggled into the narrow
    # scope and silently stop protecting the shared account.
    for loaded_lane in loaded["lanes"]:
        model_markers = set(loaded_lane["model_capacity_markers"])
        assert model_markers <= set(loaded_lane["capacity_markers"]), loaded_lane[
            "name"
        ]
    # A lane with no model-scoped marker keeps the historical lane-wide breaker.
    assert lane(loaded, "zhipu-coding-plan")["model_capacity_markers"] == ()


def test_expired_lane_deadline_is_not_reported_as_active_cooldown(
    monkeypatch: Any,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    lease = state.acquire(model="gpt-6-luna")
    state.release(lease, capacity_error=True, retry_after=60)
    assert state.snapshot()["cooldown_active"] is True

    # The deadline has elapsed, but no request has run the half-open probe yet.
    # Health must describe the elapsed deadline rather than the stale float.
    clock[0] = 160.0
    snapshot = state.snapshot()
    assert snapshot["cooldown_remaining"] == 0
    assert snapshot["cooldown_active"] is False
    assert snapshot["cooldown_scope"] == "none"


def test_config_rejects_blank_model_capacity_marker(tmp_path: Any) -> None:
    raw = json.loads(
        (
            Path(__file__).parents[1] / "scripts" / "remote" / "cpa-admission.json"
        ).read_text(encoding="utf-8")
    )
    raw["lanes"][0]["model_capacity_markers"] = ["  "]
    config_path = tmp_path / "cpa-admission.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="model_capacity_markers"):
        load_config(config_path)


def test_model_cooldown_parks_only_the_named_model(monkeypatch: Any) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded = config()
    state = LaneState({**lane(loaded, "chatgpt-oauth"), "queue_timeout_seconds": 5})

    # The first model-scoped signal is absorbed as a blip, exactly like the
    # lane streak; the second one opens that model's window and nothing else.
    for _ in range(2):
        lease = state.acquire(model="gpt-6-luna")
        assert lease.admitted
        state.release(
            lease, capacity_error=True, retry_after=None, capacity_scope="model"
        )

    snapshot = state.snapshot()
    assert snapshot["cooldown_remaining"] == 0
    assert snapshot["failure_streak"] == 0
    assert snapshot["cooldown_scope"] == "model"
    assert snapshot["model_cooldowns"] == {"gpt-6-luna": 60}

    blocked = state.acquire(model="gpt-6-luna")
    assert not blocked.admitted
    assert blocked.reason == "model_cooldown"
    # The refusal advertises the window the upstream named, not a "retry now"
    # that would only burn the client's retry budget.
    assert blocked.retry_after == 60

    # The sibling on the same credential is admitted, keeping the lane's shared
    # concurrency budget, and a served sibling must not clear the parked model.
    sibling = state.acquire(model="gpt-6.1-sol")
    assert sibling.admitted and not sibling.probe
    state.release(sibling, capacity_error=False, retry_after=None)
    assert state.snapshot()["model_cooldowns"] == {"gpt-6-luna": 60}

    # One probe per interval re-tests the parked model.
    clock[0] = 111.0
    probe = state.acquire(model="gpt-6-luna")
    assert probe.admitted and probe.probe and probe.model_probe
    assert probe.reason == "model_half_open"
    # A failed probe walks the ladder and re-parks the model without touching
    # the lane.
    state.release(probe, capacity_error=True, retry_after=None, capacity_scope="model")
    assert state.snapshot()["model_cooldowns"]["gpt-6-luna"] == 120
    assert state.snapshot()["cooldown_remaining"] == 0

    clock[0] = 122.0
    healed = state.acquire(model="gpt-6-luna")
    assert healed.admitted and healed.model_probe
    state.release(healed, capacity_error=False, retry_after=None)
    assert state.snapshot()["model_cooldowns"] == {}
    assert state.snapshot()["cooldown_scope"] == "none"
    plain = state.acquire(model="gpt-6-luna")
    assert plain.admitted and not plain.probe
    state.release(plain, capacity_error=False, retry_after=None)


def test_model_probe_success_cannot_clear_a_newer_model_failure(
    monkeypatch: Any,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded = config()
    state = LaneState({**lane(loaded, "chatgpt-oauth"), "queue_timeout_seconds": 5})
    for _ in range(2):
        lease = state.acquire(model="gpt-6-luna")
        state.release(
            lease, capacity_error=True, retry_after=None, capacity_scope="model"
        )

    clock[0] = 111.0
    probe = state.acquire(model="gpt-6-luna")
    assert probe.model_probe
    # A newer failure for the same model lands while the probe is in flight, so
    # the probe's success describes the older generation and must not clear the
    # window the newer failure just opened.
    state._record_model_capacity("gpt-6-luna", None, clock[0])
    state.release(probe, capacity_error=False, retry_after=None)
    assert state.snapshot()["model_cooldowns"]["gpt-6-luna"] > 0


def test_lane_probe_answered_with_a_model_marker_releases_the_lane(
    monkeypatch: Any,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded = config()
    state = LaneState({**lane(loaded, "chatgpt-oauth"), "queue_timeout_seconds": 5})
    for _ in range(2):
        lease = state.acquire(model="gpt-6-luna")
        state.release(lease, capacity_error=True, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] > 0

    clock[0] = 111.0
    probe = state.acquire(model="gpt-6-luna")
    assert probe.admitted and probe.probe and not probe.model_probe
    # The probe was answered with a model marker: the account is reachable, so
    # the lane must reopen instead of holding its siblings behind a lane-level
    # refusal for the whole backoff window.
    state.release(probe, capacity_error=True, retry_after=None, capacity_scope="model")
    snapshot = state.snapshot()
    assert snapshot["cooldown_remaining"] == 0
    assert snapshot["failure_streak"] == 0
    sibling = state.acquire(model="gpt-6.1-sol")
    assert sibling.admitted and not sibling.probe
    state.release(sibling, capacity_error=False, retry_after=None)


def test_model_scope_without_an_attributed_model_stays_lane_wide() -> None:
    # If no model could be attributed to the lease, a model-scoped marker must
    # not be silently dropped: the lane keeps the protection it had before.
    loaded = config()
    state = LaneState({**lane(loaded, "chatgpt-oauth"), "queue_timeout_seconds": 5})
    for _ in range(2):
        lease = state.acquire()
        state.release(
            lease, capacity_error=True, retry_after=None, capacity_scope="model"
        )
    snapshot = state.snapshot()
    assert snapshot["cooldown_remaining"] > 0
    assert snapshot["model_cooldowns"] == {}


def test_lane_scoped_failures_build_a_per_model_streak(monkeypatch: Any) -> None:
    # Mixed traffic with an account-level signal text: upstream sheds are
    # model-correlated (2026-10-10 -- every gpt-6-luna request refused while
    # gpt-6.1-sol kept succeeding), but each sibling success resets the lane
    # streak, so the lane breaker alone never parks the refused model and its
    # clients burn a full upstream round trip on every attempt. The per-model
    # streak must survive the sibling's successes and park the refused model
    # after the same two-failure threshold, without touching the lane.
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded = config()
    state = LaneState({**lane(loaded, "chatgpt-oauth"), "queue_timeout_seconds": 5})

    failed = state.acquire(model="gpt-6-luna")
    assert failed.admitted
    state.release(failed, capacity_error=True, retry_after=None)
    served = state.acquire(model="gpt-6.1-sol")
    assert served.admitted
    state.release(served, capacity_error=False, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 0
    assert state.snapshot()["model_cooldowns"] == {}

    failed_again = state.acquire(model="gpt-6-luna")
    state.release(failed_again, capacity_error=True, retry_after=None)
    snapshot = state.snapshot()
    # The lane streak was reset by the served sibling, so the lane stays open;
    # the refused model's own streak hit the threshold and parks it alone.
    assert snapshot["cooldown_remaining"] == 0
    assert snapshot["cooldown_scope"] == "model"
    assert snapshot["model_cooldowns"] == {"gpt-6-luna": 60}

    # Inside the cooldown window the refused model is answered locally in
    # milliseconds instead of riding another full upstream round trip.
    clock[0] = 101.0
    blocked = state.acquire(model="gpt-6-luna")
    assert not blocked.admitted
    assert blocked.reason == "model_cooldown"
    assert blocked.retry_after == 59

    # The healthy sibling is unaffected and keeps the lane's concurrency
    # budget; a served sibling must not clear the parked model.
    sibling = state.acquire(model="gpt-6.1-sol")
    assert sibling.admitted and not sibling.probe
    state.release(sibling, capacity_error=False, retry_after=None)
    assert state.snapshot()["model_cooldowns"] == {"gpt-6-luna": 59}

    # One probe per interval re-tests the parked model; success clears it.
    clock[0] = 111.0
    probe = state.acquire(model="gpt-6-luna")
    assert probe.admitted and probe.probe and probe.model_probe
    state.release(probe, capacity_error=False, retry_after=None)
    assert state.snapshot()["model_cooldowns"] == {}
    assert state.snapshot()["cooldown_scope"] == "none"


def test_lane_probe_success_clears_the_probing_models_streak(
    monkeypatch: Any,
) -> None:
    # A half-open probe rides a real model request, so its success must clear
    # that model's per-model streak too -- otherwise a model that recovered
    # during a lane cooldown would be re-parked after a single fresh blip.
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded = config()
    state = LaneState({**lane(loaded, "chatgpt-oauth"), "queue_timeout_seconds": 5})
    for _ in range(2):
        lease = state.acquire(model="gpt-6.1-sol")
        state.release(lease, capacity_error=True, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] > 0
    assert state.snapshot()["model_cooldowns"] == {"gpt-6.1-sol": 60}

    clock[0] = 111.0
    probe = state.acquire(model="gpt-6-luna")
    assert probe.admitted and probe.probe and not probe.model_probe
    state.release(probe, capacity_error=False, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 0

    # One fresh blip is absorbed: the cleared streak restarts from zero.
    blip = state.acquire(model="gpt-6-luna")
    assert blip.admitted
    state.release(blip, capacity_error=True, retry_after=None)
    assert "gpt-6-luna" not in state.snapshot()["model_cooldowns"]


def test_config_rejects_non_positive_early_probe_interval(tmp_path: Any) -> None:
    raw = json.loads(
        (
            Path(__file__).parents[1] / "scripts" / "remote" / "cpa-admission.json"
        ).read_text(encoding="utf-8")
    )
    raw["early_probe_interval_seconds"] = 0
    config_path = tmp_path / "cpa-admission.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    try:
        load_config(config_path)
    except ValueError as exc:
        assert "early_probe_interval_seconds" in str(exc)
    else:
        raise AssertionError(
            "non-positive early_probe_interval_seconds must be rejected"
        )


def test_lane_respects_retry_after_beyond_local_backoff_schedule(
    monkeypatch: Any,
) -> None:
    # Same-tick Windows monotonic reads expose float error for a 7200s deadline.
    clock = 6419.828
    monkeypatch.setattr(time, "monotonic", lambda: clock)
    loaded = config()
    state = LaneState(lane(loaded, "zhipu-coding-plan"))
    lease = state.acquire()
    assert lease.admitted
    state.release(lease, capacity_error=True, retry_after=7200)
    snapshot = state.snapshot()
    assert snapshot["failure_streak"] == 1
    assert snapshot["cooldown_remaining"] >= 7199
    assert snapshot["cooldown_remaining"] <= 7200


def test_lane_does_not_shorten_an_existing_longer_cooldown() -> None:
    loaded = config()
    state = LaneState(lane(loaded, "zhipu-coding-plan"))
    first = state.acquire()
    concurrent = state.acquire()
    state.release(first, capacity_error=True, retry_after=60)

    # Model the next demand-driven early probe without waiting for the
    # production interval. A shorter concurrent Retry-After must not replace
    # the already advertised longer window.
    assert concurrent.admitted
    state.release(concurrent, capacity_error=True, retry_after=1)
    assert state.snapshot()["cooldown_remaining"] >= 55


def test_health_reaps_finished_retired_readers_but_keeps_total() -> None:
    loaded = config()
    proxy = AdmissionProxy(loaded)
    finished = threading.Thread(target=lambda: None)
    finished.start()
    finished.join(timeout=2)
    assert not finished.is_alive()
    assert proxy.note_retired_reader(finished) == 0

    release = threading.Event()
    resident = threading.Thread(target=release.wait)
    resident.start()
    deadline = time.monotonic() + 2
    while not resident.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert resident.is_alive()
    assert proxy.note_retired_reader(resident) == 1
    health = proxy.health()
    assert health["retired_readers"] == 1
    assert health["retired_readers_total"] == 2

    release.set()
    resident.join(timeout=2)
    assert not resident.is_alive()
    health = proxy.health()
    assert health["retired_readers"] == 0
    assert health["retired_readers_total"] == 2


def test_lane_pending_slots_bound_concurrency_and_reject_the_rest() -> None:
    # The bound is the contract: at most `max_inflight` upstream calls run at
    # once, at most `max_pending` waiters queue, and anything beyond that is
    # rejected immediately as busy rather than piling up.
    # Every assertion below runs against a one-second queue budget clone, so a
    # regression in the bound shows up as a wrong reason code rather than as a
    # test that blocks for the production queue timeout.
    loaded = config()
    loaded_lane = lane(loaded, "zhipu-coding-plan")
    inflight = loaded_lane["max_inflight"]
    max_pending = loaded_lane["max_pending"]

    short_lane = dict(loaded_lane)
    short_lane["queue_timeout_seconds"] = 1
    state = LaneState(short_lane)

    held = [state.acquire() for _ in range(inflight)]
    assert all(lease.admitted for lease in held)
    assert state.inflight == inflight

    results: list[Any] = []
    lock = threading.Lock()

    def wait_for_slot() -> None:
        lease = state.acquire()
        with lock:
            results.append(lease)

    waiters = [threading.Thread(target=wait_for_slot) for _ in range(max_pending)]
    for waiter in waiters:
        waiter.start()
    deadline = time.monotonic() + 3
    while state.pending != max_pending and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.pending == max_pending
    assert state.inflight == inflight

    # One more than the bound is rejected immediately rather than queued.
    rejected = state.acquire()
    assert not rejected.admitted
    assert rejected.reason == "busy"
    assert state.pending == max_pending

    # A waiter served after a release is admitted without ever exceeding the
    # concurrency bound.
    state.release(held.pop(), capacity_error=False, retry_after=None)
    served_deadline = time.monotonic() + 3
    while not results and time.monotonic() < served_deadline:
        time.sleep(0.005)
    assert len(results) == 1
    assert results[0].admitted
    assert state.inflight == inflight
    assert state.pending == max_pending - 1

    # The waiters that never got a slot time out rather than blocking forever.
    for waiter in waiters:
        waiter.join(timeout=5)
    assert not any(waiter.is_alive() for waiter in waiters)
    assert len(results) == max_pending
    assert all(
        not lease.admitted and lease.reason == "queue_timeout" for lease in results[1:]
    )
    assert state.inflight <= inflight


def test_queued_request_hands_its_slot_back_when_the_client_is_gone() -> None:
    """An abandoned request must not hold a pending slot for the whole budget.

    A queued request that nobody is listening for would otherwise occupy one
    of four pending slots for the 120s budget, long after its client left.
    """

    loaded = config()
    state = LaneState(lane(loaded, "chatgpt-oauth"))
    held = [
        state.acquire() for _ in range(lane(loaded, "chatgpt-oauth")["max_inflight"])
    ]
    assert all(lease.admitted for lease in held)
    # An immediate admit never queued, so its wait is stamped zero.
    assert all(lease.waited_ms == 0 for lease in held)

    gone = state.acquire(alive=lambda: False)
    assert not gone.admitted
    assert gone.reason == "downstream_gone"
    # The slot is returned, not leaked, and the request gave up after a poll
    # slice rather than sleeping out the 120s budget.
    assert state.pending == 0
    assert 0 <= gone.waited_ms < 30000

    # A live waiter still takes the lane the moment a holder releases it, and
    # its own queue wait is visible in the lease.
    results: list[Any] = []

    def wait_for_slot() -> None:
        results.append(state.acquire(alive=lambda: True))

    waiter = threading.Thread(target=wait_for_slot)
    waiter.start()
    deadline = time.monotonic() + 3
    while state.pending != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.pending == 1

    state.release(held.pop(), capacity_error=False, retry_after=None)
    waiter.join(timeout=5)
    assert not waiter.is_alive()
    assert len(results) == 1
    assert results[0].admitted
    assert state.pending == 0
    for lease in held + results:
        state.release(lease, capacity_error=False, retry_after=None)


def test_lease_waited_ms_reports_a_timeout_against_the_queue_budget() -> None:
    """A queue timeout must be attributable to the wait it actually served."""

    loaded = config()
    lane_config = dict(lane(loaded, "chatgpt-oauth"))
    lane_config["queue_timeout_seconds"] = 1
    state = LaneState(lane_config)
    held = [state.acquire() for _ in range(lane_config["max_inflight"])]
    assert all(lease.admitted for lease in held)

    started = time.monotonic()
    timed_out = state.acquire()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    assert not timed_out.admitted
    assert timed_out.reason == "queue_timeout"
    assert elapsed_ms - 200 <= timed_out.waited_ms <= elapsed_ms + 500
    for lease in held:
        state.release(lease, capacity_error=False, retry_after=None)


def test_downstream_alive_tracks_a_client_that_leaves_while_queued() -> None:
    """The queue-reaping probe must read a closed socket as gone.

    Semantics under test, and why they are asymmetric: reaping only happens when
    the socket buffer is EMPTY and the peer has closed. A pipelined next request
    looks identical to a body followed by a FIN at the socket layer, so a
    non-empty buffer is always reported alive. That is the fail-safe direction --
    a missed reap falls back to the pre-existing full-budget wait, while a false
    reap would reject a live request.
    """

    handler_type = cast(Any, MODULE["Handler"])

    def handler_for(connection: socket.socket) -> Any:
        handler = object.__new__(handler_type)
        handler.connection = connection
        return handler

    def wait_gone(handler: Any) -> bool:
        deadline = time.monotonic() + 2
        while handler._downstream_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        return handler._downstream_alive() is False

    # An idle but connected client is alive.
    server_side, client_side = socket.socketpair()
    handler = handler_for(server_side)
    try:
        assert handler._downstream_alive() is True
        # A clean FIN with nothing buffered is a client that gave up.
        client_side.close()
        assert wait_gone(handler) is True
    finally:
        server_side.close()

    # A hard RST with nothing buffered is the desktop-abort shape.
    server_side, client_side = socket.socketpair()
    handler = handler_for(server_side)
    try:
        client_side.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        client_side.close()
        assert wait_gone(handler) is True
    finally:
        server_side.close()

    # Buffered bytes keep the client "alive" on purpose, so a pipelined request
    # is never mistaken for an abandoned one.
    server_side, client_side = socket.socketpair()
    handler = handler_for(server_side)
    try:
        client_side.sendall(b"GET /healthz HTTP/1.1\r\n")
        client_side.close()
        assert handler._downstream_alive() is True
    finally:
        server_side.close()


def test_proxy_preserves_chunked_stream_framing() -> None:
    class UpstreamHandler(BaseHTTPRequestHandler):
        # Deliberately HTTP/1.0 close-delimited, matching the CPA upstream
        # the admission talks to.
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            self.connection.sendall(b"data: one\n\n")
            self.connection.sendall(b"data: two\n\n")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever)
    upstream_thread.start()
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(loaded))
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=3
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"unregistered-model","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        assert response.getheader("Transfer-Encoding") == "chunked"
        assert response.read() == b"data: one\n\ndata: two\n\n"
        client.close()
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def _read_raw_response(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, ConnectionError):
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def test_rejects_truncated_fixed_length_request_body() -> None:
    loaded = config()
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(loaded))
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    sock = socket.create_connection(admission.server_address, timeout=3)
    sock.settimeout(3)
    try:
        sock.sendall(
            b"POST /v1/responses HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Length: 5\r\n\r\n"
            b"abc"
        )
        sock.shutdown(socket.SHUT_WR)
        response = _read_raw_response(sock)
        assert b"400 Bad Request" in response
        assert b"request body ended before" in response
    finally:
        sock.close()
        admission.shutdown()
        admission.server_close()
        admission_thread.join(timeout=2)


def test_rejects_truncated_chunked_request_body() -> None:
    loaded = config()
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(loaded))
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    sock = socket.create_connection(admission.server_address, timeout=3)
    sock.settimeout(3)
    try:
        sock.sendall(
            b"POST /v1/responses HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
            b"5\r\n"
            b"abc"
        )
        sock.shutdown(socket.SHUT_WR)
        response = _read_raw_response(sock)
        assert b"400 Bad Request" in response
        assert b"request body ended before" in response
    finally:
        sock.close()
        admission.shutdown()
        admission.server_close()
        admission_thread.join(timeout=2)


@pytest.mark.parametrize(
    "framing,body",
    [
        (b"Content-Length: 999999999\r\n", b""),
        (b"Content-Length: 0\r\nContent-Length: 1\r\n", b""),
        (b"Content-Length: 0\r\nTransfer-Encoding: chunked\r\n", b"0\r\n\r\n"),
        (b"Transfer-Encoding: chunked\r\n", b"+1\r\nx\r\n0\r\n\r\n"),
        (b"Transfer-Encoding: chunked\r\n", b"0\r\n" + b"X-Trailer: x\r\n" * 6000),
    ],
    ids=[
        "oversized-length",
        "duplicate-length",
        "conflicting-framing",
        "invalid-chunk-size",
        "oversized-trailers",
    ],
)
def test_malformed_body_closes_connection_before_pipelined_request(
    framing: bytes,
    body: bytes,
) -> None:
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(config()))
    thread = threading.Thread(target=admission.serve_forever)
    thread.start()
    sock = socket.create_connection(admission.server_address, timeout=3)
    try:
        sock.sendall(
            b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n"
            + framing
            + b"\r\n"
            + body
            + b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        response = _read_raw_response(sock)
        assert b"400 Bad Request" in response
        assert b"Connection: close" in response
        assert b"200 OK" not in response
    finally:
        sock.close()
        admission.shutdown()
        admission.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "framing,body",
    [
        (b"Content-Length: 1 \t\r\n", b"x"),
        (b"Transfer-Encoding: chunked\r\n", b"1 \t;name=value\r\nx\r\n0\r\n\r\n"),
    ],
    ids=["length-ows", "chunk-extension-bws"],
)
def test_valid_body_framing_whitespace_remains_compatible(
    framing: bytes,
    body: bytes,
) -> None:
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(config()))
    thread = threading.Thread(target=admission.serve_forever)
    thread.start()
    try:
        with socket.create_connection(admission.server_address, timeout=3) as sock:
            sock.sendall(
                b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
                + framing
                + b"\r\n"
                + body
            )
            response = _read_raw_response(sock)
            assert b"200 OK" in response
            assert b"400 Bad Request" not in response
    finally:
        admission.shutdown()
        admission.server_close()
        thread.join(timeout=2)


def test_zero_chunk_without_trailer_terminator_is_rejected() -> None:
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(config()))
    thread = threading.Thread(target=admission.serve_forever)
    thread.start()
    sock = socket.create_connection(admission.server_address, timeout=3)
    try:
        sock.sendall(
            b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n0\r\n"
        )
        sock.shutdown(socket.SHUT_WR)
        response = _read_raw_response(sock)
        assert b"400 Bad Request" in response
        assert b"invalid chunked trailer" in response
    finally:
        sock.close()
        admission.shutdown()
        admission.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("fixed_length", [False, True])
@pytest.mark.parametrize("terminal", [False, True])
def test_only_completed_sse_can_heal_half_open_lane(
    fixed_length: bool, terminal: bool
) -> None:
    payload = b'data: {"type":"response.created"}\n\ndata: {"type":"response.output_text.delta","delta":"ok"}\n\n'
    if terminal:
        payload += (
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
        )

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if fixed_length:
                self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    upstream, worker = _serve_local(Upstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_port
    proxy = AdmissionProxy(loaded)
    state = proxy.lanes["chatgpt-oauth"]
    state.failure_streak = 2
    state.open_until = time.monotonic() - 1
    server, thread = _serve_local(cast(Any, MODULE["Handler"]))
    server.proxy = proxy
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        client.request("POST", "/v1/responses", b'{"model":"gpt-6-luna","stream":true}')
        response = client.getresponse()
        assert response.status == 200 and response.read() == payload
        deadline = time.monotonic() + 2
        while state.inflight:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert state.failure_streak == (0 if terminal else 2)
        assert (state.open_until == 0) is terminal
    finally:
        client.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for running in (thread, worker):
            running.join(timeout=2)


@pytest.mark.parametrize(
    ("event_kind", "expected_reason"),
    [("malformed", "malformed_event"), ("oversized", "inspection_limit")],
)
def test_untrusted_sse_event_before_terminal_cannot_heal_half_open_lane(
    event_kind: str,
    expected_reason: str,
    caplog: Any,
) -> None:
    loaded = config()
    if event_kind == "malformed":
        preceding_event = b"data: {not-json}\n\n"
    else:
        limit = loaded["probe_bytes"]
        oversized_json = (
            b'{"type":"response.output_text.delta","delta":"' + b"x" * limit + b'"}'
        )
        preceding_event = b"data: " + oversized_json + b"\n\n"
    terminal = (
        b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
    )
    payload = preceding_event + terminal

    class UntrustedSSEUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    upstream, worker = _serve_local(UntrustedSSEUpstream)
    loaded["upstream_port"] = upstream.server_port
    proxy = AdmissionProxy(loaded)
    state = proxy.lanes["chatgpt-oauth"]
    state.failure_streak = 2
    state.open_until = time.monotonic() - 1
    server, thread = _serve_local(cast(Any, MODULE["Handler"]))
    server.proxy = proxy
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    caplog.set_level(logging.INFO)
    try:
        client.request("POST", "/v1/responses", b'{"model":"gpt-6-luna","stream":true}')
        response = client.getresponse()
        assert response.status == 200
        assert response.read() == payload
        deadline = time.monotonic() + 3
        while state.inflight:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert state.failure_streak == 2
        assert state.open_until != 0
        assert f"successful=false protocol_result={expected_reason}" in caplog.text
        assert "sse_terminal_seen=true" in caplog.text
        assert (
            "sse_inspection_complete=" + str(event_kind != "oversized").lower()
            in caplog.text
        )
        assert (
            "sse_malformed_json=" + str(event_kind == "malformed").lower()
            in caplog.text
        )
    finally:
        client.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for running in (thread, worker):
            running.join(timeout=2)


@pytest.mark.parametrize(
    "route,payload",
    [
        (
            "/v1/responses",
            {"type": "response.completed", "response": {"status": "completed"}},
        ),
        ("/v1/chat/completions", {"choices": [{"finish_reason": "stop"}]}),
        ("/v1/messages", {"type": "message_stop"}),
        (
            "/v1beta/models/gemini-3.8-flash-high:streamGenerateContent",
            {"candidates": [{"finishReason": "STOP"}]},
        ),
        (
            "/v1beta/models/gemini-3.8-flash-high:streamGenerateContent",
            {"candidates": [{"finishReason": "MAX_TOKENS"}]},
        ),
        (
            "/v1beta/interactions",
            {
                "event_type": "interaction.completed",
                "interaction": {"status": "completed"},
            },
        ),
        (
            "/v1beta/interactions",
            {"type": "interaction.done", "interaction": {"status": "completed"}},
        ),
    ],
)
def test_sse_recovery_requires_own_protocol_terminal(
    route: str, payload: dict[str, Any]
) -> None:
    probe = StreamErrorProbe(1024, lane(config(), "chatgpt-oauth"), path=route)
    # Cross-protocol sentinels and generated strings cannot prove recovery.
    if not route.endswith("/completions"):
        probe.feed(b"data: [DONE]\n\n")
    probe.feed(b'data: {"delta":"response.completed message_stop STOP"}\n\n')
    assert not probe.terminal_success
    event = b"data: " + json.dumps(payload).encode() + b"\r\n\r\n"
    for byte in event:
        probe.feed(bytes([byte]))
    if route.endswith("/completions"):
        event = b"data: [DONE]\r\n\r\n"
        for byte in event:
            probe.feed(bytes([byte]))
    probe.finish()
    assert probe.complete and probe.terminal_success and not probe.has_error
    probe.feed(b'data: {"error":{"code":"usage_limit_reached"}}\n\n')
    assert probe.has_error  # A later failure wins even after a terminal event.


def test_chat_stream_requires_stream_terminator_not_any_choice_finish() -> None:
    probe = StreamErrorProbe(
        1024, lane(config(), "chatgpt-oauth"), path="/v1/chat/completions"
    )
    probe.feed(
        b'data: {"choices":[{"index":0,"finish_reason":null},'
        b'{"index":1,"finish_reason":null}]}\n\n'
    )
    probe.feed(b'data: {"choices":[{"index":0,"finish_reason":"stop"}]}\n\n')
    probe.finish()
    assert not probe.terminal_success
    probe.feed(b"data: [DONE]\n\n")
    assert probe.terminal_success


@pytest.mark.parametrize("tail", [b"", b"\n", b"\n\n"])
def test_sse_unframed_terminal_and_late_empty_failure_cannot_heal(tail: bytes) -> None:
    probe = StreamErrorProbe(
        1024, lane(config(), "chatgpt-oauth"), path="/v1/responses"
    )
    done = b'data: {"type":"response.completed","response":{"status":"completed"}}'
    probe.feed(done + tail)
    probe.finish()
    assert probe.terminal_success is (tail == b"\n\n")
    probe.feed(b'data: {"type":"response.failed"}')
    probe.finish()
    assert probe.has_error


def test_auto_and_interactions_cannot_bypass_held_lane(tmp_path: Path) -> None:
    received: list[bytes] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args: Any) -> None:
            pass

    upstream, worker = _serve_local(Upstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_port
    proxy = AdmissionProxy(loaded, state_path=risk_state_path(tmp_path))
    proxy.lanes["antigravity-oauth"].record_risk("account_restricted", None)
    server, thread = _serve_local(cast(Any, MODULE["Handler"]))
    server.proxy = proxy
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        for route, model, status in (
            ("/v1/responses", "auto", 400),
            ("/v1/chat/completions", "auto(high)", 400),
            ("/v1beta/interactions", "models/auto(4096)", 400),
            ("/v1beta/models/auto:generateContent", "", 400),
            ("/v1beta/interactions", "models/gemini-3.8-flash-high(high)", 403),
            ("/v1/responses", "relay-unmanaged", 200),
        ):
            client.request("POST", route, json.dumps({"model": model}).encode())
            response = client.getresponse()
            assert response.status == status
            data = response.read()
            if status == 400:
                assert json.loads(data)["error"]["code"] == "explicit_model_required"
        assert received == [b'{"model": "relay-unmanaged"}']
    finally:
        client.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for running in (thread, worker):
            running.join(timeout=2)


@pytest.mark.parametrize(
    "model,encrypted,status",
    [
        ("claude-haiku-5-5", True, 422),
        ("glm-5.3-flash(xhigh)", True, 422),
        ("gemini-3.8-flash-high", True, 422),
        ("gpt-6-luna", True, 200),
        ("claude-haiku-5-5", False, 200),
    ],
)
def test_encrypted_agent_payload_rejected_before_upstream(
    model: str, encrypted: bool, status: int, caplog: Any
) -> None:
    received: list[bytes] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            data = b'{"status":"completed","output":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: Any) -> None:
            pass

    content = [{"type": "input_text", "text": "bounded task header"}]
    if encrypted:
        content.append(
            {"type": "encrypted_content", "encrypted_content": "opaque-task-envelope"}
        )
    body = json.dumps(
        {"model": model, "input": [{"type": "agent_message", "content": content}]}
    ).encode()
    upstream, worker = _serve_local(Upstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_port
    proxy = AdmissionProxy(loaded)
    before = {name: state.failure_streak for name, state in proxy.lanes.items()}
    server, thread = _serve_local(cast(Any, MODULE["Handler"]))
    server.proxy = proxy
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        with caplog.at_level(logging.INFO):
            client.request("POST", "/v1/responses", body)
            response = client.getresponse()
            data = json.loads(response.read())
        assert response.status == status
        if status == 422:
            assert data["error"]["code"] == "encrypted_agent_payload_unsupported"
            assert data["upstream_contacted"] is False
            assert data["task_mode"] == "encrypted_agent_message"
            assert data["route_capability"] == "unsupported"
            assert response.getheader("Retry-After") is None
            assert response.getheader("X-CPA-Admission-Reason") == data["error"]["code"]
            assert received == []
            assert "opaque-task-envelope" not in caplog.text
            assert before == {
                name: state.failure_streak for name, state in proxy.lanes.items()
            }
        else:
            assert received == [body]  # no decoding, stripping, or rewriting
    finally:
        client.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for running in (thread, worker):
            running.join(timeout=2)


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/v1/responses", {"model": "claude-haiku-5-5", "input": "plain task"}),
        (
            "/v1/responses",
            {
                "model": "glm-5.3-flash",
                "input": [{"type": "reasoning", "encrypted_content": "signature"}],
            },
        ),
        (
            "/v1/responses",
            {
                "model": "gemini-3.8-flash-high",
                "input": [None, {"type": "agent_message", "content": "not an array"}],
            },
        ),
        (
            "/v1/chat/completions",
            {
                "model": "claude-haiku-5-5",
                "input": [
                    {
                        "type": "agent_message",
                        "content": [
                            {"type": "encrypted_content", "encrypted_content": "opaque"}
                        ],
                    }
                ],
            },
        ),
    ],
)
def test_encrypted_agent_guard_does_not_expand_to_other_payloads(
    path: str, payload: dict[str, Any]
) -> None:
    assert not MODULE["unsupported_encrypted_agent_payload"](
        path, json.dumps(payload).encode()
    )


@pytest.mark.parametrize(
    ("path", "body", "expected"),
    [
        (
            "/v1/responses",
            json.dumps(
                {
                    "model": "glm-5.3",
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image_url": "data:image/png;base64,image-a",
                                }
                            ],
                        }
                    ],
                }
            ).encode(),
            True,
        ),
        (
            "/v1/responses",
            json.dumps(
                {
                    "model": "GLM-5.3(high)",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_image", "image_url": "image-b"}
                            ],
                        }
                    ],
                }
            ).encode(),
            True,
        ),
        (
            "/v1/chat/completions",
            json.dumps(
                {
                    "model": "glm-5.3(low)",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": "image-c"}}
                            ],
                        }
                    ],
                }
            ).encode(),
            True,
        ),
        (
            "/v1/responses",
            json.dumps(
                {
                    "model": "glm-5.3",
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": "plain"}],
                        }
                    ],
                }
            ).encode(),
            False,
        ),
        (
            "/v1/responses",
            json.dumps(
                {
                    "model": "glm-5.3",
                    "input": [
                        {
                            "role": "assistant",
                            "content": [
                                {"type": "input_image", "image_url": "image-d"}
                            ],
                        }
                    ],
                }
            ).encode(),
            False,
        ),
        (
            "/v1/responses",
            json.dumps(
                {
                    "model": "glm-5.3",
                    "input": [
                        {
                            "type": "function_call_output",
                            "call_id": "call-1",
                            "output": [{"type": "input_image", "image_url": "image-e"}],
                        }
                    ],
                }
            ).encode(),
            False,
        ),
        (
            "/v1/responses",
            json.dumps(
                {
                    "model": "glm-5.3",
                    "input": [
                        {
                            "role": "user",
                            "type": "function_call_output",
                            "content": [
                                {"type": "input_image", "image_url": "image-f"}
                            ],
                        }
                    ],
                }
            ).encode(),
            False,
        ),
        (
            "/v1/responses",
            json.dumps(
                {
                    "model": "glm-5.3-flash",
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_image", "image_url": "image-g"}
                            ],
                        }
                    ],
                }
            ).encode(),
            False,
        ),
        (
            "/v1/chat/completions",
            json.dumps(
                {
                    "model": "gpt-6-luna",
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "image_url", "image_url": "image-h"}],
                        }
                    ],
                }
            ).encode(),
            False,
        ),
        (
            "/v1/responses",
            b'{"model":"glm-5.3","input":[',
            False,
        ),
        ("/v1/responses", b"not-json", False),
        (
            "/v1/other",
            json.dumps(
                {
                    "model": "glm-5.3",
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_image", "image_url": "image-i"}
                            ],
                        }
                    ],
                }
            ).encode(),
            False,
        ),
    ],
)
def test_unsupported_user_image_classifier(
    path: str, body: bytes, expected: bool
) -> None:
    assert (
        MODULE["unsupported_user_image"](path, body, frozenset({"glm-5.3"})) is expected
    )


def test_proxy_rejects_text_only_user_images_before_upstream(
    caplog: Any,
) -> None:
    received: list[bytes] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            data = b'{"output_text":"unexpected"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: Any) -> None:
            pass

    upstream, upstream_thread = _serve_local(Upstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_port
    proxy = AdmissionProxy(loaded)
    assert proxy.text_only_models == frozenset({"glm-5.3"})
    before = {name: state.snapshot() for name, state in proxy.lanes.items()}
    server, thread = _serve_local(cast(Any, MODULE["Handler"]))
    server.proxy = proxy
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    response_image = "PRIVATE_IMAGE_RESPONSE_59e63e"
    chat_image = "PRIVATE_IMAGE_CHAT_7ad821"
    requests = (
        (
            "/v1/responses",
            json.dumps(
                {
                    "model": "glm-5.3",
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "describe"},
                                {"type": "input_image", "image_url": response_image},
                            ],
                        }
                    ],
                }
            ).encode(),
            response_image,
        ),
        (
            "/v1/chat/completions",
            json.dumps(
                {
                    "model": "glm-5.3(high)",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "describe"},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": chat_image},
                                },
                            ],
                        }
                    ],
                }
            ).encode(),
            chat_image,
        ),
    )
    try:
        with caplog.at_level(logging.INFO):
            for path, body, private_image in requests:
                client.request("POST", path, body=body)
                response = client.getresponse()
                data = json.loads(response.read())
                assert response.status == 422
                assert data["error"]["code"] == "user_image_input_unsupported"
                assert data["upstream_contacted"] is False
                assert data["task_mode"] == "user_image_input"
                assert data["route_capability"] == "text_only"
                assert response.getheader("Retry-After") is None
                assert (
                    response.getheader("X-CPA-Admission-Reason")
                    == "user_image_input_unsupported"
                )
                assert private_image not in caplog.text
                assert {
                    name: state.snapshot() for name, state in proxy.lanes.items()
                } == before
        assert received == []
    finally:
        client.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for running in (thread, upstream_thread):
            running.join(timeout=2)


def test_text_only_guard_preserves_allowed_bodies_byte_for_byte() -> None:
    received: list[bytes] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            data = b'{"output_text":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: Any) -> None:
            pass

    upstream, upstream_thread = _serve_local(Upstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_port
    proxy = AdmissionProxy(loaded)
    server, thread = _serve_local(cast(Any, MODULE["Handler"]))
    server.proxy = proxy
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    allowed_bodies = [
        (
            "/v1/responses",
            b'{"model":"glm-5.3-flash","input":[{"role":"user","content":[{"type":"input_image","image_url":"flash-image"}]}]}',
        ),
        (
            "/v1/chat/completions",
            b'{"model":"gpt-6-luna","messages":[{"role":"user","content":[{"type":"image_url","image_url":{"url":"gpt-image"}}]}]}',
        ),
        (
            "/v1/responses",
            b'{"model":"glm-5.3","input":[{"role":"user","content":[{"type":"input_text","text":"plain text"}]}]}',
        ),
        (
            "/v1/responses",
            b'{"model":"glm-5.3","input":[{"type":"function_call_output","call_id":"call-2","output":[{"type":"input_image","image_url":"tool-result-image"}]}]}',
        ),
    ]
    try:
        for path, body in allowed_bodies:
            client.request("POST", path, body=body)
            response = client.getresponse()
            assert response.status == 200
            response.read()
        assert received == [body for _, body in allowed_bodies]
    finally:
        client.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for running in (thread, upstream_thread):
            running.join(timeout=2)


def _serve_local(handler: Any) -> tuple[Any, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread


@pytest.mark.parametrize("route", ["/v1/responses", "/v1/chat/completions"])
def test_proxy_gates_suffixed_model_without_rewriting_body(route: str) -> None:
    received: list[bytes] = []

    class SuffixUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            body = b'{"output_text":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(SuffixUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    client = http.client.HTTPConnection(
        "127.0.0.1", admission.server_address[1], timeout=5
    )
    payload = b'{"model":"gpt-6-luna(high)","input":"hello","stream":false}'
    try:
        client.request("POST", route, body=payload)
        response = client.getresponse()
        assert response.status == 200
        response.read()
        assert received == [payload]
        state = proxy.lanes["chatgpt-oauth"]
        deadline = time.monotonic() + 3
        while state.snapshot()["inflight"]:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        lease = state.acquire()
        state.release(lease, capacity_error=True, retry_after=3600)
        client.request("POST", route, body=payload)
        response = client.getresponse()
        assert response.status == 429
        assert response.getheader("X-CPA-Admission-Reason") == "cooldown"
        response.read()
        assert received == [payload]
    finally:
        client.close()
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_admin_reset_probe_checks_access_completion_and_repeat_budget() -> None:
    good = {
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "OK"}]}
        ],
    }
    for upstream_payload, should_heal in (
        (good, True),
        ({**good, "status": "incomplete"}, False),
        ({"error": {"type": "usage_limit_reached"}}, False),
    ):
        received: list[dict[str, Any]] = []

        class RecoveryUpstream(BaseHTTPRequestHandler):
            def do_POST(
                self,
                received_requests: list[dict[str, Any]] = received,
                response_payload: dict[str, Any] = upstream_payload,
            ) -> None:
                received_requests.append(
                    {
                        "path": self.path,
                        "body": json.loads(
                            self.rfile.read(int(self.headers["Content-Length"]))
                        ),
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                encoded = json.dumps(response_payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        upstream, upstream_thread = _serve_local(RecoveryUpstream)
        loaded = config()
        loaded["upstream_port"] = upstream.server_address[1]
        proxy = AdmissionProxy(loaded)
        state = proxy.lanes["chatgpt-oauth"]
        failed = state.acquire()
        state.release(failed, capacity_error=True, retry_after=3600)
        payload = json.dumps(
            {
                "model": "gpt-6.1-sol",
                "reason": "quota_reset",
                "expected_generation": state.snapshot()["failure_generation"],
            }
        ).encode()
        admission = AdmissionServer(("127.0.0.1", 0), proxy)
        admission_thread = threading.Thread(target=admission.serve_forever)
        admission_thread.start()
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=5
        )
        try:
            for method, body, headers, expected_status in (
                ("GET", b"", {}, 404),
                ("POST", payload, {"X-Forwarded-For": "192.0.2.1"}, 404),
                ("POST", b'{"model":"gpt-6.1-sol"}', {}, 400),
                (
                    "POST",
                    payload.replace(b'"gpt-6.1-sol"', b'"glm-5.3"'),
                    {},
                    400,
                ),
                (
                    "POST",
                    payload.replace(b'"quota_reset"', b'"automatic_timer"'),
                    {},
                    400,
                ),
            ):
                client.request(
                    method, "/admin/recover-after-reset", body=body, headers=headers
                )
                response = client.getresponse()
                assert response.status == expected_status
                response.read()
            assert not received
            client.request(
                "POST",
                "/v1/responses",
                body=b'{"model":"gpt-6.1-sol","input":"normal"}',
            )
            response = client.getresponse()
            assert response.status == 429
            assert response.getheader("X-CPA-Admission-Reason") == "cooldown"
            response.read()
            assert not received
            client.request(
                "POST",
                "/admin/recover-after-reset",
                body=payload,
                headers={"Authorization": "Bearer test-client-key"},
            )
            response = client.getresponse()
            assert response.status == 200
            response.read()
            deadline = time.monotonic() + 2
            while state.snapshot()["inflight"] and time.monotonic() < deadline:
                time.sleep(0.005)
            assert not state.snapshot()["inflight"]
            assert (state.snapshot()["cooldown_remaining"] == 0) == should_heal
            assert len(received) == 1
            assert received[0]["path"] == "/v1/responses"
            assert received[0]["body"]["model"] == "gpt-6.1-sol"
            assert received[0]["body"]["input"] == "Reply with exactly: OK"
            assert received[0]["body"]["stream"] is False
            assert received[0]["authorization"] == "Bearer test-client-key"
            client.request("POST", "/admin/recover-after-reset", body=payload)
            response = client.getresponse()
            assert response.status == 409
            response.read()
            assert len(received) == 1
        finally:
            client.close()
            admission.shutdown()
            upstream.shutdown()
            admission.server_close()
            upstream.server_close()
            admission_thread.join(timeout=2)
            upstream_thread.join(timeout=2)


def test_request_trace_distinguishes_upstream_and_local_rejections(caplog: Any) -> None:
    caplog.set_level(logging.INFO)
    received: list[dict[str, str]] = []
    incoming_request_id = "private-client-request-identifier"
    incoming_instance_id = "private-cockpit-instance-identifier"
    payload = b'{"model":"gpt-6-luna","input":"private-request-content"}'

    class TraceUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            received.append(dict(self.headers.items()))
            status = 200 if len(received) == 1 else 429
            response_body = (
                b'{"output_text":"ok"}'
                if status == 200
                else b'{"error":{"code":"rate_limit_exceeded"}}'
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("X-CPA-Request-Id", "spoofed-upstream-identifier")
            self.send_header("X-CPA-Admission-Reason", "spoofed-upstream-reason")
            if status == 429:
                self.send_header("Retry-After", "30")
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(TraceUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    # Keep the upstream's advertised cooldown longer than the queue budget so
    # this test still exercises the outright-refusal headers; a cooldown that
    # fits the budget is now held in the queue instead of being refused.
    for lane_config in loaded["lanes"]:
        lane_config["queue_timeout_seconds"] = 1
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    client = http.client.HTTPConnection(
        "127.0.0.1", admission.server_address[1], timeout=5
    )
    request_ids: list[str] = []
    try:
        for route, expected_status in (
            ("/v1/responses?private-route-content=1", 200),
            ("/v1/chat/completions", 429),
        ):
            client.request(
                "POST",
                route,
                body=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Client-Request-Id": incoming_request_id,
                    "X-Cockpit-Instance-Id": incoming_instance_id,
                    "X-CPA-Request-Id": "spoofed-client-identifier",
                },
            )
            response = client.getresponse()
            assert response.status == expected_status
            request_id = response.getheader("X-CPA-Request-Id")
            assert request_id is not None and len(request_id) == 32
            assert int(request_id, 16) >= 0
            assert response.getheader("X-CPA-Admission-Reason") is None
            if expected_status == 429:
                assert response.getheader("Retry-After") == "30"
            assert response.read()
            request_ids.append(request_id)
            deadline = time.monotonic() + 2
            while proxy.lanes["chatgpt-oauth"].snapshot()["inflight"]:
                assert time.monotonic() < deadline
                time.sleep(0.005)
        assert len(received) == 2
        assert [headers.get("X-CPA-Request-Id") for headers in received] == request_ids
        client.request(
            "POST",
            "/v1/responses",
            body=payload,
            headers={
                "Content-Type": "application/json",
                "X-Client-Request-Id": incoming_request_id,
                "X-Cockpit-Instance-Id": incoming_instance_id,
            },
        )
        rejected = client.getresponse()
        assert rejected.status == 429
        assert rejected.getheader("X-CPA-Admission-Reason") == "cooldown"
        local_request_id = rejected.getheader("X-CPA-Request-Id")
        assert local_request_id is not None and len(local_request_id) == 32
        request_ids.append(local_request_id)
        retry_after_header = rejected.getheader("Retry-After")
        assert retry_after_header is not None
        assert 28 <= int(retry_after_header) <= 30
        assert len(rejected.read()) == 203
        assert len(received) == 2
        assert len(set(request_ids)) == 3
    finally:
        client.close()
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)
    log_text = caplog.text
    for request_id in request_ids:
        assert f"request_id={request_id}" in log_text
    assert "route=responses" in log_text
    assert "route=chat" in log_text
    assert (
        "client_request_hash="
        + hashlib.sha256(incoming_request_id.encode()).hexdigest()[:12]
    ) in log_text
    assert (
        "instance_hash="
        + hashlib.sha256(incoming_instance_id.encode()).hexdigest()[:12]
    ) in log_text
    for sensitive_value in (
        incoming_request_id,
        incoming_instance_id,
        "private-request-content",
        "private-route-content",
    ):
        assert sensitive_value not in log_text


def test_sse_stream_emits_heartbeat_comments_during_upstream_silence() -> None:
    class SlowSSEUpstream(BaseHTTPRequestHandler):
        # Deliberately HTTP/1.0 close-delimited, matching the CPA upstream
        # the admission talks to.
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            # Frames go out on the raw socket on purpose: a buffered wfile
            # would batch the deliberate 1.5s silence away.
            self.connection.sendall(b"data: one\n\n")
            time.sleep(1.5)
            self.connection.sendall(b"data: two\n\n")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(SlowSSEUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    admission = AdmissionServer(
        ("127.0.0.1", 0), AdmissionProxy(loaded, heartbeat_interval=0.3)
    )
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=10
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"unregistered-model","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        payload = response.read()
        client.close()
        assert b"data: one\n\n" in payload
        assert b"data: two\n\n" in payload
        assert payload.count(b": keepalive\n\n") >= 1
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


@pytest.mark.parametrize(
    ("upstream_event", "last_event"),
    [
        (
            b'data: {"type":"response.output_text.delta"}\n\n',
            "response.output_text.delta",
        ),
        (
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n',
            "response.completed",
        ),
    ],
)
def test_client_disconnect_releases_lane_lease_during_upstream_silence(
    monkeypatch: Any,
    caplog: Any,
    upstream_event: bytes,
    last_event: str,
) -> None:
    class SilentSSEUpstream(BaseHTTPRequestHandler):
        # Deliberately HTTP/1.0 close-delimited, matching the CPA upstream
        # the admission talks to.
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            self.connection.sendall(upstream_event)
            time.sleep(30)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(SilentSSEUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    # Hold the heartbeat out of this test so the forwarding loop's socket peek
    # is the path that observes and records the disconnect.
    real_thread = threading.Thread

    def without_heartbeat(*args: Any, **kwargs: Any) -> threading.Thread:
        target = kwargs.get("target")
        if getattr(target, "__name__", None) == "_heartbeat_loop":
            kwargs["target"] = lambda: None
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(MODULE["threading"], "Thread", without_heartbeat)
    proxy = AdmissionProxy(loaded, heartbeat_interval=0.3)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=5
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        caplog.set_level(logging.INFO)
        response = client.getresponse()
        assert response.status == 200
        assert response.read(len(upstream_event)) == upstream_event
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 1
        client.sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        client.close()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0:
                break
            time.sleep(0.1)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0
        assert proxy.lanes["chatgpt-oauth"].snapshot()["failure_streak"] == 0
        assert proxy.lanes["chatgpt-oauth"].snapshot()["cooldown_remaining"] == 0
        assert "successful=false protocol_result=client_disconnect" in caplog.text
        assert f"sse_bytes={len(upstream_event)} sse_events=1" in caplog.text
        assert f"sse_last_event={last_event}" in caplog.text
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_lane_sse_lease_released_when_client_leaves_before_upstream_speaks() -> None:
    # The disconnect must be honoured structurally, not by luck of timing: a
    # silent upstream must never let the forwarding thread park inside a read
    # while the client is gone. The reader thread owns every blocking read,
    # so the lane lease is released on the very next heartbeat slice no matter
    # how long the upstream stays quiet (production heartbeat is 15s, so one
    # slice is the worst case a client can pin a lane).
    class MutedSSEUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            self.connection.sendall(b"data: one\n\n")
            time.sleep(30)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(MutedSSEUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    proxy = AdmissionProxy(loaded, heartbeat_interval=0.3)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=5
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        assert response.read(1)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 1
        client.sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        client.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0:
                break
            time.sleep(0.05)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0
        assert proxy.lanes["chatgpt-oauth"].snapshot()["failure_streak"] == 0
        assert proxy.lanes["chatgpt-oauth"].snapshot()["cooldown_remaining"] == 0
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_lane_sse_stream_completes_when_upstream_closes_connection() -> None:
    # http.client detaches the socket (conn.sock becomes None) as soon as the
    # upstream signals close. A lane SSE stream must still be forwarded in
    # full: any readiness check that treats the detached socket as "nothing to
    # read" leaves the downstream waiting for data that is already buffered,
    # and check-then-read races on the same HTTPResponse cause IncompleteRead.
    # The client must receive both frames and the terminating chunk.
    class ClosingSSEUpstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            self.connection.sendall(
                b"B\r\ndata: one\n\n\r\nB\r\ndata: two\n\n\r\n0\r\n\r\n"
            )
            self.close_connection = True

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(ClosingSSEUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(loaded))
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=5
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        assert response.getheader("Transfer-Encoding") == "chunked"
        assert response.read() == b"data: one\n\ndata: two\n\n"
        client.close()
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_lane_sse_truncated_upstream_chunk_is_not_reported_as_success(
    caplog: Any,
) -> None:
    first_event = b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
    incomplete_event = b'data: {"type":"response.completed"'

    class TruncatedSSEUpstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(f"{len(first_event):X}\r\n".encode("ascii"))
            self.wfile.write(first_event + b"\r\n")
            # Announce a second chunk larger than the fragment sent below, then
            # close the upstream write side before the SSE terminal event.
            self.wfile.write(f"{len(incomplete_event) + 8:X}\r\n".encode("ascii"))
            self.wfile.write(incomplete_event)
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            self.close_connection = True

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(TruncatedSSEUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    caplog.set_level(logging.WARNING)
    client = http.client.HTTPConnection(
        "127.0.0.1", admission.server_address[1], timeout=1.5
    )
    try:
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        assert response.getheader("Transfer-Encoding") == "chunked"
        assert response.read(len(first_event)) == first_event
        read_started = time.monotonic()
        with pytest.raises(
            (
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                ConnectionResetError,
            )
        ):
            # The proxy must leave the downstream response incomplete when the
            # upstream chunk is truncated; it cannot synthesize a successful
            # terminal chunk for an SSE turn that never completed. A timeout
            # does not count: the proxy must promptly close the broken stream.
            response.read()
        assert time.monotonic() - read_started < 1.0

        state = proxy.lanes["chatgpt-oauth"].snapshot()
        assert state["failure_streak"] == 0, state
        assert state["cooldown_remaining"] == 0, state
        assert "upstream_error lane=chatgpt-oauth" in caplog.text
        assert "upstream_result lane=chatgpt-oauth" not in caplog.text

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0:
                break
            time.sleep(0.05)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0
    finally:
        client.close()
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_non_sse_client_disconnect_is_not_reported_as_upstream_error(
    caplog: Any,
) -> None:
    body_sent = threading.Event()

    class SlowJSONUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = b'{"output_text":"complete"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.flush()
            assert body_sent.wait(3)
            try:
                self.connection.sendall(body)
            except OSError:
                return

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(SlowJSONUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    caplog.set_level(logging.INFO)
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=5
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        # Headers alone are insufficient evidence for this race; the test
        # targets a blocking body read after the downstream socket resets.
        time.sleep(0.05)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 1
        client.sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        client.close()
        body_sent.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0:
                break
            time.sleep(0.05)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)
    assert "downstream_disconnect lane=chatgpt-oauth" in caplog.text
    assert "upstream_error lane=chatgpt-oauth" not in caplog.text


def test_non_sse_read_error_coinciding_with_client_reset_is_not_upstream_error(
    monkeypatch: Any,
    caplog: Any,
) -> None:
    body_sent = threading.Event()
    body_partial = threading.Event()
    original_read1 = cast(Any, http.client.HTTPResponse).read1

    class SlowJSONUpstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # A truncated HTTP chunk forces a real read1 IncompleteRead.
            # The downstream reset must win attribution.
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            body = b'{"output_text":"partial"}'
            self.wfile.write(f"{len(body):X}\r\n".encode() + body + b"\r\n")
            self.wfile.flush()
            assert body_sent.wait(3)
            try:
                self.wfile.write(b"20\r\npartial")
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_WR)
                self.close_connection = True
            except OSError:
                return

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    def pause_partial_read(response: Any, size: int) -> bytes:
        chunk = original_read1(response, size)
        if b"partial" in chunk:
            body_partial.set()
            assert body_sent.wait(3)
            # Read the following malformed chunk before forwarding the first
            # bytes so this test exercises read1(), not a downstream write.
            for _ in range(3):
                original_read1(response, size)
            raise AssertionError("truncated chunk did not raise during read1")
        return cast(bytes, chunk)

    upstream, upstream_thread = _serve_local(SlowJSONUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(http.client.HTTPResponse, "read1", pause_partial_read)
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=5
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        assert body_partial.wait(3)
        client.sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        client.close()
        body_sent.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0:
                break
            time.sleep(0.05)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0
    finally:
        body_sent.set()
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)
    assert "downstream_disconnect lane=chatgpt-oauth" in caplog.text
    assert "upstream_error lane=chatgpt-oauth" not in caplog.text


def test_lane_reject_survives_a_client_that_left_before_the_429_landed() -> None:
    # A retry-storming client closes the socket the moment it has decided to
    # retry; the admission then writes the 429 into a dead pipe. That race
    # must be absorbed quietly -- it is not a handler crash and must not reach
    # the journal as a traceback -- while the lane bookkeeping stays intact.
    class OverloadedUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = b'{"error":{"code":"server_is_overloaded"}}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(OverloadedUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    loaded_lane = next(
        item for item in loaded["lanes"] if item["name"] == "chatgpt-oauth"
    )
    # A short queue timeout keeps the two failures that open the breaker from
    # stalling the test; the production budget is untouched.
    loaded_lane["queue_timeout_seconds"] = 1
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()

    payload = b'{"model":"gpt-6-luna","input":"hello"}'

    def raw_call(read_response: bool = True) -> None:
        # A full request/response cycle so the upstream 503 is classified as a
        # capacity blip (the client leaving early would be a disconnect, which
        # is deliberately not capacity evidence).
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect(("127.0.0.1", admission.server_address[1]))
            sock.sendall(
                b"POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            if read_response:
                while sock.recv(4096):
                    pass
        finally:
            sock.close()

    try:
        # Two consecutive capacity failures open the breaker (threshold 2).
        for _ in range(2):
            raw_call()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if proxy.lanes["chatgpt-oauth"].snapshot()["failure_streak"] >= 2:
                break
            time.sleep(0.05)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["failure_streak"] >= 2

        # The next request is rejected with a 429; the socket is torn down with
        # SO_LINGER 0 so the server sees an RST and its write fails. Swallowing
        # that failure is the behaviour under test: the request thread survives
        # and the lane keeps its own bookkeeping.
        victim = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        victim.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        try:
            victim.connect(("127.0.0.1", admission.server_address[1]))
            victim.sendall(
                b"POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
        finally:
            victim.close()
        time.sleep(0.5)

        assert proxy.lanes["chatgpt-oauth"].snapshot()["cooldown_remaining"] > 0
        assert admission_thread.is_alive()
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


@pytest.mark.parametrize("late_stream", [False, True])
@pytest.mark.parametrize("fixed_length", [False, True])
def test_proxy_parks_only_the_capacity_limited_model(
    late_stream: bool, fixed_length: bool
) -> None:
    # End-to-end form of the 2026-10-10 measurement: with a model-scoped
    # capacity marker the old code opened a lane-wide cooldown, so a sibling
    # model on the same credential was refused with a 429 the client could only
    # read as a retry-limit failure. The marker must park the named model and
    # leave the sibling admitted.
    class ModelCapacityUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            model = json.loads(raw)["model"]
            body = (
                b'{"error":{"type":"capacity_error",'
                b'"message":"Selected model is at capacity"}}'
                if model == "gpt-6-luna"
                else b'{"output_text":"ok"}'
            )
            if late_stream:
                body = (
                    b'data: {"delta":"ordinary output"}\n\n' * 10000
                    + b"data: "
                    + body
                    + b"\n\n"
                )
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/event-stream" if late_stream else "application/json",
            )
            if not late_stream or fixed_length:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(ModelCapacityUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    loaded_lane = next(
        item for item in loaded["lanes"] if item["name"] == "chatgpt-oauth"
    )
    loaded_lane["queue_timeout_seconds"] = 2
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()

    def call(model: str) -> tuple[int, str | None, bytes]:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=10
        )
        try:
            body = json.dumps({"model": model, "input": "hello"}).encode()
            client.request(
                "POST",
                "/v1/responses",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = client.getresponse()
            return (
                response.status,
                response.getheader("X-CPA-Admission-Reason"),
                response.read(),
            )
        finally:
            client.close()

    try:
        for _ in range(2):
            status, _, payload = call("gpt-6-luna")
            assert status == 200
            assert b"at capacity" in payload

        # The response body can reach the client before the handler's finally
        # block releases the lease, so wait for the bookkeeping rather than
        # reading it mid-teardown.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = proxy.lanes["chatgpt-oauth"].snapshot()
            if state["inflight"] == 0 and state["cooldown_scope"] == "model":
                break
            time.sleep(0.02)
        state = proxy.lanes["chatgpt-oauth"].snapshot()
        assert state["cooldown_remaining"] == 0, state
        assert state["failure_streak"] == 0, state
        assert state["cooldown_scope"] == "model", state
        assert state["model_cooldowns"]["gpt-6-luna"] > 0, state

        status, reason, payload = call("gpt-6-luna")
        assert status == 429
        assert reason == "model_cooldown"
        assert payload

        status, _, payload = call("gpt-6.1-sol")
        assert status == 200
        if late_stream:
            assert payload == (
                b'data: {"delta":"ordinary output"}\n\n' * 10000
                + b'data: {"output_text":"ok"}\n\n'
            )
        else:
            assert payload == b'{"output_text":"ok"}'
        cooldowns = proxy.lanes["chatgpt-oauth"].snapshot()["model_cooldowns"]
        assert set(cooldowns) == {"gpt-6-luna"}
        assert 0 < cooldowns["gpt-6-luna"] <= 60
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_upstream_transport_failure_does_not_open_the_breaker() -> None:
    # A connection refused / reset / read timeout proves the network hiccupped,
    # not that the shared account is out of capacity. Two such failures in a
    # row must leave the lane open for business: the request is still served a
    # bounded 503, but no cooldown opens and a later request is still admitted
    # rather than being cooldown-rejected.
    loaded = config()
    # A closed port makes every upstream call fail at the transport layer
    # without any provider ever answering.
    loaded["upstream_port"] = 1
    loaded_lane = next(
        item for item in loaded["lanes"] if item["name"] == "chatgpt-oauth"
    )
    loaded_lane["queue_timeout_seconds"] = 1
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=10
        )
        for _ in range(2):
            client.request(
                "POST",
                "/v1/responses",
                body=b'{"model":"gpt-6-luna","input":"hello"}',
                headers={"Content-Type": "application/json"},
            )
            response = client.getresponse()
            assert response.status == 503
            assert response.read()
        state = proxy.lanes["chatgpt-oauth"].snapshot()
        assert state["failure_streak"] == 0, state
        assert state["cooldown_remaining"] == 0, state
        # Still admitted, not cooldown-rejected.
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        assert client.getresponse().status == 503
        client.close()
    finally:
        admission.shutdown()
        admission.server_close()
        admission_thread.join(timeout=2)


def test_keep_alive_connection_reset_after_a_response_is_absorbed() -> None:
    # BaseHTTPRequestHandler.handle() re-enters handle_one_request() while the
    # connection is kept alive, and http.server guards only TimeoutError around
    # the next request-line read. A client that RSTs after a completed response
    # therefore raises ConnectionResetError out of the stdlib loop and
    # socketserver logs a full handler traceback -- indistinguishable in the
    # journal from a real crash. The handler must absorb it and close quietly.
    loaded = config()
    # A dead upstream keeps this test about the reader loop, not about proxying.
    loaded["upstream_port"] = 1
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()

    # socketserver calls _handle_request_noblock; intercept the traceback that
    # its process_request_thread prints for an uncaught handler exception.
    logged: list[str] = []
    original_print_exc = socketserver.BaseServer.handle_error

    def capture(
        self: Any,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: Any,
    ) -> None:
        logged.append("handle_error")

    socketserver.BaseServer.handle_error = capture  # type: ignore[method-assign]
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(("127.0.0.1", admission.server_address[1]))
        # A completed request first, then an RST before the next request line.
        # The connection stays open after the response (that is the point), so
        # read exactly one framed response rather than draining to EOF.
        sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            head += sock.recv(4096)
        length = int(
            next(
                line.split(b":", 1)[1]
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
        )
        body = head.split(b"\r\n\r\n", 1)[1]
        while len(body) < length:
            body += sock.recv(4096)
        assert head.startswith(b"HTTP/1.1 200")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()
        time.sleep(0.5)

        assert logged == [], f"handler logged a traceback: {logged}"
        assert admission_thread.is_alive()
        # The server is still healthy for the next request.
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=10
        )
        client.request("GET", "/healthz")
        assert client.getresponse().status == 200
        client.close()
    finally:
        socketserver.BaseServer.handle_error = original_print_exc  # type: ignore[method-assign]
        admission.shutdown()
        admission.server_close()
        admission_thread.join(timeout=2)


@pytest.mark.parametrize(
    "status,payload,headers,expected,minimum",
    [
        (403, b"{}", {}, "operator_review_required", 0),
        (
            400,
            b'{"error":{"message":"This credential is only authorized for use with Claude Code"}}',
            {},
            "operator_review_required",
            0,
        ),
        (
            429,
            b'{"error":{"type":"rate_limit_error"}}',
            {},
            "operator_review_required",
            0,
        ),
        (
            429,
            b'{"error":{"type":"rate_limit_error"}}',
            {"Retry-After": "100000", "Retry-After-Ms": "100001000"},
            "cooldown",
            100000,
        ),
        *[
            (
                200,
                body,
                {
                    "Retry-After": "1",
                    "anthropic-ratelimit-unified-5h-status": "rejected",
                    "anthropic-ratelimit-unified-5h-reset": str(
                        int(time.time()) + 3600
                    ),
                },
                "cooldown",
                3500,
            )
            for body in (
                b'{"error":{"type":"rate_limit_error"}}',
                b'data: {"type":"error","error":{"type":"rate_limit_error"}}\n\n',
            )
        ],
        (529, b'{"error":{"type":"overloaded_error"}}', {}, "cooldown", 59),
        (
            200,
            b'{"error":{"type":"rate_limit_error"}}',
            {},
            "operator_review_required",
            0,
        ),
        (
            200,
            b'data: {"type":"error","error":{"type":"rate_limit_error"}}\n\n',
            {},
            "operator_review_required",
            0,
        ),
    ],
)
def test_claude_risk_proxy_preserves_response_and_blocks_next_request(
    tmp_path: Path,
    status: int,
    payload: bytes,
    headers: dict[str, str],
    expected: str,
    minimum: int,
) -> None:
    received: list[bytes] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(status)
            self.send_header(
                "Content-Type",
                "text/event-stream"
                if payload.startswith(b"data:")
                else "application/json",
            )
            self.send_header("Content-Length", str(len(payload)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    loaded = claude_lane_config()
    loaded["upstream_port"] = upstream.server_port
    lane(loaded, "claude-oauth")["queue_timeout_seconds"] = 0.01
    path = risk_state_path(tmp_path)
    proxy = AdmissionProxy(loaded, claude_state_path=path)
    server = AdmissionServer(("127.0.0.1", 0), proxy)
    threads = [threading.Thread(target=s.serve_forever) for s in (upstream, server)]
    for thread in threads:
        thread.start()
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    body = b'{"model":"claude-sonnet-5-5","messages":[]}'
    try:
        client.request("POST", "/v1/messages", body)
        response = client.getresponse()
        assert response.status == status and response.read() == payload
        if minimum == 100000:
            assert response.getheader("Retry-After") == "100001"
            assert response.getheader("Retry-After-Ms") is None
        deadline = time.monotonic() + 2
        while proxy.lanes["claude-oauth"].snapshot()["inflight"]:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        client.request("POST", "/v1/messages", body)
        rejected = client.getresponse()
        assert rejected.status == (
            403 if expected == "operator_review_required" else 429
        )
        assert rejected.getheader("X-CPA-Admission-Reason") == expected
        if minimum:
            assert int(rejected.getheader("Retry-After") or "0") >= minimum
        else:
            assert rejected.getheader("Retry-After") is None
        rejected.read()
        assert received == [body]
        restarted = LaneState(lane(loaded, "claude-oauth"), path)
        assert not restarted.acquire().admitted
    finally:
        client.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for thread in threads:
            thread.join(timeout=2)


def test_claude_deadlines_use_rejected_windows_and_longest_valid_header() -> None:
    from email.message import Message

    headers = Message()
    headers["Retry-After"] = "60"
    headers["Retry-After-Ms"] = "65001"
    headers["Anthropic-Ratelimit-Unified-5h-Status"] = "rejected"
    headers["Anthropic-Ratelimit-Unified-5h-Reset"] = "100100"
    headers["Anthropic-Ratelimit-Unified-7d-Status"] = "allowed"
    headers["Anthropic-Ratelimit-Unified-7d-Reset"] = "999999"
    parse = MODULE["claude_retry_after"]
    assert parse(headers, now=100000) == 100
    signal = MODULE["oauth_risk_signal"]
    assert signal("claude-oauth", 200, b'{"text":"account suspended"}') == (None, None)
    assert signal(
        "claude-oauth", 429, b'{"error":{"type":"rate_limit_error"}}', True
    ) == (None, None)
    del headers["Retry-After"]
    del headers["Retry-After-Ms"]
    headers.replace_header("Anthropic-Ratelimit-Unified-5h-Reset", "NaN")
    assert parse(headers, now=100000) is None
    headers["Retry-After"] = "-30"
    assert parse(headers, now=100000) is None
    headers.replace_header("Retry-After", "+30")
    assert parse(headers, now=100000) is None


@pytest.mark.parametrize("lane_name", ["chatgpt-oauth", "workbuddy-oauth"])
def test_additional_oauth_hold_survives_restart_and_reset(
    lane_name: str,
    tmp_path: Path,
) -> None:
    path = risk_state_path(tmp_path)
    loaded = lane(config(), lane_name)
    state = LaneState(loaded, path)
    lease = state.acquire()
    assert lease.admitted
    assert json.loads(path.read_text())["hold_reason"] == "unverified_request"
    state.record_risk("account_restricted", None)
    state.release(lease, capacity_error=False, retry_after=None)
    assert not state.acquire_after_reset(
        state.snapshot()["failure_generation"]
    ).admitted
    restarted = LaneState(loaded, path)
    assert not restarted.acquire().admitted
    assert restarted.snapshot()["hold_reason"] == "account_restricted"


@pytest.mark.parametrize("lane_name", ["chatgpt-oauth", "workbuddy-oauth"])
def test_additional_oauth_only_error_envelopes_trigger_restriction(
    lane_name: str,
) -> None:
    signal = MODULE["oauth_risk_signal"]
    body = json.dumps({"error": {"code": "account_deactivated"}}).encode()
    assert signal(lane_name, 400, body)[0] == "account_restricted"
    body = json.dumps(
        {
            "choices": [
                {"message": {"content": "account has been suspended; code 11140"}}
            ]
        }
    ).encode()
    assert signal(lane_name, 200, body) == (None, None)
    assert signal(lane_name, 429, b'{"error":{"code":"rate_limit"}}') == (None, None)
    assert signal(lane_name, 401, b"{}")[0] == "authentication_required"


def test_workbuddy_business_error_requires_review_without_guessing_code_meaning() -> (
    None
):
    signal = MODULE["oauth_risk_signal"]
    assert (
        signal("workbuddy-oauth", 200, b'{"code":11140,"msg":"request illegal"}')[0]
        == "account_restricted"
    )
    assert signal(
        "workbuddy-oauth",
        400,
        b'{"code":11128,"msg":"first message is not system prompt"}',
    ) == (None, None)


def test_workbuddy_unknown_rate_limit_window_requires_review() -> None:
    signal = MODULE["oauth_risk_signal"]
    body = b'{"error":{"type":"rate_limit_error"}}'
    assert signal("workbuddy-oauth", 200, body)[0] == "retry_window_review_required"
    assert signal("workbuddy-oauth", 200, body, True) == (None, None)


def test_workbuddy_model_busy_wave_cools_without_operator_hold(tmp_path: Path) -> None:
    """14003 "Model busy" is officially model-side congestion that self-clears
    (2026-10-20 wave: ~31 minutes). The signal layer must not hold it, and the
    header-site ladder rung must persist a plain cooldown that reopens by
    itself -- never an operator-review hold (the pre-2026-10-20 policy turned
    every peak-hour wave into a manual release)."""
    body = json.dumps({"code": 14003, "msg": "too many requests"}).encode()
    assert MODULE["oauth_risk_signal"]("workbuddy-oauth", 429, body) == (None, None)
    path = risk_state_path(tmp_path)
    state = LaneState(lane(claude_lane_config(), "workbuddy-oauth"), path)
    state.record_risk(None, 1260)
    restored = LaneState(lane(claude_lane_config(), "workbuddy-oauth"), path)
    snapshot = restored.snapshot()
    assert snapshot["hold_reason"] is None
    assert snapshot["cooldown_active"] is True


def test_workbuddy_model_busy_cpa_wrapper_cools_without_hold() -> None:
    """Production shape (2026-10-21 dump): CPA wraps the upstream 14003
    envelope inside error.message and labels the wrapper type
    rate_limit_error / code rate_limit_exceeded. The quoted provider code is
    the upstream's own classification, so the wrapper label must not re-arm
    the durable hold that the header-site ladder replaced (otherwise the
    first post-deploy wave becomes another manual release)."""
    inner = json.dumps(
        {
            "code": 14003,
            "msg": "too many requests",
            "requestId": "e8a29bc6",
            "displayMsg": {
                "en": "Model busy. Please switch models or retry later",
                "zh": "模型繁忙，请换模型或稍后重试",
            },
        },
        ensure_ascii=False,
    )
    body = json.dumps(
        {
            "error": {
                "message": f"upstream 429: {inner}",
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")
    signal = MODULE["oauth_risk_signal"]
    assert signal("workbuddy-oauth", 429, body) == (None, None)
    assert signal("workbuddy-oauth", 429, body, True) == (None, None)
    probe = StreamErrorProbe(4096, lane(config(), "workbuddy-oauth"), 429)
    probe.feed(b"data: " + body + b"\n\n")
    assert probe.has_error and probe.hold_reason is None


def test_workbuddy_wrapped_account_rate_limit_still_holds() -> None:
    signal = MODULE["oauth_risk_signal"]
    bare = json.dumps(
        {
            "error": {
                "message": "rate limited",
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
            }
        }
    ).encode()
    assert signal("workbuddy-oauth", 429, bare) == (
        "retry_window_review_required",
        None,
    )
    for quoted in (6003, 6004):
        inner = json.dumps({"code": quoted, "msg": "frequency limited"})
        wrapped = json.dumps(
            {
                "error": {
                    "message": f"upstream 429: {inner}",
                    "type": "rate_limit_error",
                    "code": "rate_limit_exceeded",
                }
            }
        ).encode()
        assert signal("workbuddy-oauth", 429, wrapped) == (
            "retry_window_review_required",
            None,
        )


@pytest.mark.parametrize("code", [6003, 6004])
def test_workbuddy_business_rate_limit_is_an_error_in_json_and_sse(code: int) -> None:
    body = json.dumps({"code": code, "msg": "frequency limited"}).encode()
    signal = MODULE["oauth_risk_signal"]
    assert signal("workbuddy-oauth", 200, body) == (
        "retry_window_review_required",
        None,
    )
    assert signal("workbuddy-oauth", 200, body, True) == (None, None)
    probe = StreamErrorProbe(4096, lane(config(), "workbuddy-oauth"), 200)
    probe.feed(b"data: " + body + b"\n\n")
    assert probe.has_error
    assert probe.hold_reason == "retry_window_review_required"
    assert probe.scope == "lane"
    known = StreamErrorProbe(
        4096, lane(config(), "workbuddy-oauth"), 200, retry_after=600
    )
    known.feed(b"data: " + body + b"\n\n")
    assert known.has_error and known.scope == "lane" and known.hold_reason is None
    assert signal("chatgpt-oauth", 200, body) == (None, None)
    other = StreamErrorProbe(4096, lane(config(), "chatgpt-oauth"), 200)
    other.feed(b"data: " + body + b"\n\n")
    assert not other.has_error


@pytest.mark.parametrize(
    "lane_name", ["chatgpt-oauth", "claude-oauth", "workbuddy-oauth"]
)
@pytest.mark.parametrize("code", [401, 403])
def test_embedded_auth_error_cannot_hide_behind_http_success(
    lane_name: str,
    code: int,
) -> None:
    payload = json.dumps({"error": {"code": code, "message": "denied"}}).encode()
    probe = StreamErrorProbe(4096, lane(claude_lane_config(), lane_name), 200)
    probe.feed(b"data: " + payload + b"\n\n")
    assert probe.has_error
    assert probe.hold_reason == "authentication_required"
    prose = json.dumps({"choices": [{"delta": {"content": payload.decode()}}]}).encode()
    assert MODULE["oauth_risk_signal"](lane_name, 200, prose) == (None, None)


@pytest.mark.parametrize(
    "lane_name,model,state_arg,payload",
    [
        (
            "chatgpt-oauth",
            "gpt-6-luna",
            "chatgpt_state_path",
            b'{"error":{"code":403,"message":"denied"}}',
        ),
        (
            "claude-oauth",
            "claude-sonnet-5-5",
            "claude_state_path",
            b'{"error":{"details":{"error_code":"enforced_spend_limit_reached"}}}',
        ),
        (
            "workbuddy-oauth",
            "deepseek-v4.1-flash",
            "workbuddy_state_path",
            b'{"code":6004,"msg":"frequency limited"}',
        ),
    ],
)
def test_json_risk_latches_before_response_eof(
    lane_name: str,
    model: str,
    state_arg: str,
    payload: bytes,
    tmp_path: Path,
) -> None:
    finish = threading.Event()

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload) + 1))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            finish.wait(5)
            self.wfile.write(b" ")

        def log_message(self, *args: Any) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    loaded = claude_lane_config()
    loaded["upstream_port"] = upstream.server_port
    path = risk_state_path(tmp_path)
    proxy = AdmissionProxy(loaded, **{state_arg: path})
    server = AdmissionServer(("127.0.0.1", 0), proxy)
    threads = [threading.Thread(target=s.serve_forever) for s in (upstream, server)]
    for thread in threads:
        thread.start()
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        client.request("POST", "/v1/chat/completions", json.dumps({"model": model}))
        response = client.getresponse()
        assert response.status == 200
        deadline = time.monotonic() + 2
        while proxy.lanes[lane_name].snapshot()["hold_reason"] is None:
            assert time.monotonic() < deadline, "risk not latched before upstream EOF"
            time.sleep(0.005)
        assert proxy.lanes[lane_name].snapshot()["inflight"] == 1
        assert json.loads(path.read_text())["hold_reason"] is not None
        finish.set()
        assert response.read() == payload + b" "
    finally:
        finish.set()
        client.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for thread in threads:
            thread.join(timeout=2)


@pytest.mark.parametrize(
    "lane_name", ["chatgpt-oauth", "claude-oauth", "workbuddy-oauth"]
)
@pytest.mark.parametrize(
    "error",
    [
        {"code": "insufficient_quota"},
        {"code": "billing_hard_limit_reached"},
        {
            "type": "rate_limit_error",
            "details": {"error_code": "enforced_spend_limit_reached"},
        },
    ],
)
def test_explicit_spend_denial_persists_without_automatic_recovery(
    lane_name: str,
    error: dict[str, Any],
    tmp_path: Path,
) -> None:
    payload = json.dumps({"error": error}).encode()
    hold, delay = MODULE["oauth_risk_signal"](lane_name, 200, payload, True)
    assert hold == "retry_window_review_required" and delay is None
    path = risk_state_path(tmp_path)
    state = LaneState(lane(claude_lane_config(), lane_name), path)
    lease = state.acquire()
    state.record_risk(hold, delay)
    state.release(lease, capacity_error=False, retry_after=None)
    restored = LaneState(lane(claude_lane_config(), lane_name), path)
    assert restored.snapshot()["hold_reason"] == hold
    assert not restored.acquire_after_reset(
        restored.snapshot()["failure_generation"]
    ).admitted
    prose = json.dumps(
        {"choices": [{"message": {"content": payload.decode()}}]}
    ).encode()
    assert MODULE["oauth_risk_signal"](lane_name, 200, prose) == (None, None)


@pytest.mark.parametrize(
    "lane_name,model,state_arg",
    [
        ("chatgpt-oauth", "gpt-6-luna", "chatgpt_state_path"),
        ("workbuddy-oauth", "deepseek-v4.1-flash", "workbuddy_state_path"),
        ("claude-oauth", "claude-sonnet-5-5", "claude_state_path"),
        ("antigravity-oauth", "gemini-3.8-flash-high", "state_path"),
    ],
)
def test_invalid_gateway_key_cannot_latch_oauth_account(
    lane_name: str,
    model: str,
    state_arg: str,
    tmp_path: Path,
) -> None:
    payload = b'{"error":"Invalid API key"}'

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    loaded = claude_lane_config()
    loaded["upstream_port"] = upstream.server_port
    state_path = risk_state_path(tmp_path)
    proxy = AdmissionProxy(loaded, **{state_arg: state_path})
    server = AdmissionServer(("127.0.0.1", 0), proxy)
    threads = [threading.Thread(target=s.serve_forever) for s in (upstream, server)]
    for thread in threads:
        thread.start()
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        for _ in range(2):
            client.request("POST", "/v1/responses", json.dumps({"model": model}))
            response = client.getresponse()
            assert response.status == 401 and response.read() == payload
            deadline = time.monotonic() + 2
            while proxy.lanes[lane_name].snapshot()["inflight"]:
                assert time.monotonic() < deadline
                time.sleep(0.005)
            assert proxy.lanes[lane_name].snapshot()["hold_reason"] is None
            assert json.loads(state_path.read_text())["hold_reason"] is None
    finally:
        client.close()
        for service in (server, upstream):
            service.shutdown()
            service.server_close()
        for thread in threads:
            thread.join(timeout=2)

from __future__ import annotations

import http.client
import json
import runpy
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest


ROOT = Path(__file__).parents[1]
MODULE = runpy.run_path(str(ROOT / "scripts" / "remote" / "cpa-admission.py"))
AdmissionProxy = cast(Any, MODULE["AdmissionProxy"])
AdmissionServer = cast(Any, MODULE["Server"])
load_config = cast(Any, MODULE["load_config"])


def _serve_local(handler: Any) -> tuple[Any, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread


@pytest.mark.parametrize(
    ("provider_code", "message", "retry_after"),
    [
        ("1302", "您的账户已达到速率限制，请您控制请求频率", None),
        ("1302", "您的账户已达到速率限制，请您控制请求频率", "300"),
        (
            "1308",
            "已达到 5 小时的使用上限。您的限额将在 2026-10-25 02:57:50 重置。",
            None,
        ),
        (
            "1308",
            "已达到 5 小时的使用上限。您的限额将在 2026-10-25 02:57:50 重置。",
            "300",
        ),
    ],
)
def test_glm_provider_errors_forward_status_body_and_retry_after_unchanged(
    provider_code: str, message: str, retry_after: str | None
) -> None:
    upstream_calls: list[bytes] = []
    body = json.dumps(
        {"error": {"code": provider_code, "message": message}},
        ensure_ascii=False,
    ).encode("utf-8")

    class BigModelStub(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            upstream_calls.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if retry_after is not None:
                self.send_header("Retry-After", retry_after)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    upstream, upstream_thread = _serve_local(BigModelStub)
    loaded = load_config(ROOT / "scripts" / "remote" / "cpa-admission.json")
    loaded["upstream_host"] = "127.0.0.1"
    loaded["upstream_port"] = upstream.server_port
    proxy = AdmissionProxy(loaded)
    state = proxy.lanes["zhipu-coding-plan"]
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    client = http.client.HTTPConnection(
        "127.0.0.1", admission.server_address[1], timeout=5
    )
    request_body = b'{"model":"glm-5.3","input":"hello"}'
    try:
        client.request("POST", "/v1/responses", body=request_body)
        response = client.getresponse()
        forwarded_body = response.read()

        assert response.status == 429
        assert forwarded_body == body
        assert response.getheader("Retry-After") == retry_after
        assert response.getheader("X-CPA-Admission-Reason") is None
        assert response.getheader("Retry-After-Ms") is None
        assert response.getheader("X-RateLimit-Reset") is None
        assert upstream_calls == [request_body]

        deadline = time.monotonic() + 2
        while state.snapshot()["inflight"] and time.monotonic() < deadline:
            time.sleep(0.005)
        assert state.snapshot()["inflight"] == 0
        assert state.snapshot()["failure_streak"] == 1
    finally:
        client.close()
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)

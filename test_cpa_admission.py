from __future__ import annotations

import json
import http.client
import runpy
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast


MODULE = runpy.run_path(
    str(Path(__file__).parent / "scripts" / "remote" / "cpa-admission.py")
)
LaneState = cast(Any, MODULE["LaneState"])
AdmissionProxy = cast(Any, MODULE["AdmissionProxy"])
AdmissionServer = cast(Any, MODULE["Server"])
is_capacity_response = cast(Any, MODULE["is_capacity_response"])
load_config = cast(Any, MODULE["load_config"])
parse_retry_after = cast(Any, MODULE["parse_retry_after"])
requested_lane = cast(Any, MODULE["requested_lane"])


def config() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        load_config(
            Path(__file__).parent / "scripts" / "remote" / "cpa-admission.json"
        ),
    )


def lane(loaded: dict[str, Any], name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        next(item for item in loaded["lanes"] if item["name"] == name),
    )


def test_config_freezes_three_shared_official_account_lanes() -> None:
    loaded = config()
    assert loaded["listen_host"] == "127.0.0.1"
    assert loaded["listen_port"] == 8318
    assert loaded["upstream_port"] == 8317
    assert loaded["retry_after_max_seconds"] == 86400
    assert set(loaded["model_lanes"]) == {
        "gpt-6-luna",
        "gpt-5.6-luna",
        "glm-5.3",
        "glm-5.3-flash",
        "deepseek-flash",
        "deepseek-v4-pro",
    }
    assert [item["name"] for item in loaded["lanes"]] == [
        "chatgpt-oauth",
        "zhipu-coding-plan",
        "deepseek-official",
    ]
    for loaded_lane in loaded["lanes"]:
        # A serial lane cannot serve the desktop's concurrent turn: the
        # upstream takes 8-140s per `responses` call, so a 1-slot lane with an
        # 8s queue budget rejected every second in-flight request.
        assert loaded_lane["max_inflight"] == 3
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
    health = AdmissionProxy(loaded).health()
    assert health["retry_after_max_seconds"] == 86400


def test_policy_accepts_admission_contract_and_rejects_lane_drift() -> None:
    config_path = Path(__file__).parent / "scripts" / "remote" / "cpa-admission.json"
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    manifest = json.loads(
        (
            Path(__file__).parent / "scripts" / "remote" / "cpa_provider_routes.json"
        ).read_text(encoding="utf-8")
    )
    policy = runpy.run_path(
        str(Path(__file__).parent / "scripts" / "remote" / "cpa_policy.py")
    )
    assert policy["_admission_config_issues"](manifest, raw_config) == []

    drifted = json.loads(json.dumps(raw_config))
    drifted["lanes"][0]["models"].append("gpt-6-astra")
    issues = policy["_admission_config_issues"](manifest, drifted)
    assert any("admission lane 'chatgpt-oauth' models=" in issue for issue in issues)


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
        b'{"model":"deepseek-v4-pro","input":"hello"}',
        loaded,
    ) == ("deepseek-official", "deepseek-v4-pro")
    assert requested_lane("/v1/models", b'{"model":"gpt-6-luna"}', loaded) is None
    assert (
        requested_lane(
            "/v1/responses",
            b'{"model":"gpt-5.6-terra","input":"hello"}',
            loaded,
        )
        is None
    )
    assert requested_lane("/v1/responses", b"not-json", loaded) is None


def test_capacity_classifier_uses_status_and_markers_without_rewriting() -> None:
    loaded = config()
    loaded_lane = lane(loaded, "deepseek-official")
    assert is_capacity_response(503, b"", None, loaded_lane)
    chatgpt_lane = lane(loaded, "chatgpt-oauth")
    assert is_capacity_response(
        200, b'{"error":"Selected model is at capacity"}', None, chatgpt_lane
    )
    assert is_capacity_response(429, b"upstream", "7", loaded_lane)
    assert not is_capacity_response(
        500, b'{"error":"invalid request"}', None, loaded_lane
    )


def test_retry_after_accepts_seconds_and_http_date() -> None:
    assert parse_retry_after("12") == 12
    assert parse_retry_after("0") == 1
    now = datetime.now(timezone.utc).timestamp()
    future = datetime.now(timezone.utc) + timedelta(seconds=4)
    value = parse_retry_after(future.strftime("%a, %d %b %Y %H:%M:%S GMT"), now)
    assert value in {3, 4}
    assert parse_retry_after("not-a-date") is None


def test_lane_opens_after_capacity_and_allows_one_half_open_probe() -> None:
    loaded = config()
    state = LaneState(lane(loaded, "chatgpt-oauth"))
    first = state.acquire()
    assert first.admitted
    state.release(first, capacity_error=True, retry_after=None)
    blocked = state.acquire()
    assert not blocked.admitted
    assert blocked.reason == "cooldown"
    assert blocked.retry_after >= 1

    state.open_until = time.monotonic() - 1
    probe = state.acquire()
    assert probe.admitted and probe.probe
    blocked_probe = state.acquire()
    assert not blocked_probe.admitted
    assert blocked_probe.reason == "half_open_probe"
    state.release(probe, capacity_error=False, retry_after=None)
    recovered = state.acquire()
    assert recovered.admitted and not recovered.probe
    state.release(recovered, capacity_error=False, retry_after=None)


def test_upstream_retry_after_opens_the_breaker_on_first_failure() -> None:
    # An upstream that advertises Retry-After is explicitly telling us to back
    # off, so honour it immediately instead of waiting for the streak.
    loaded = config()
    state = LaneState(lane(loaded, "deepseek-official"))
    lease = state.acquire()
    assert lease.admitted
    state.release(lease, capacity_error=True, retry_after=45)
    snapshot = state.snapshot()
    assert snapshot["failure_streak"] == 1
    assert 1 <= snapshot["cooldown_remaining"] <= 45
    blocked = state.acquire()
    assert not blocked.admitted
    assert blocked.reason == "cooldown"


def test_lane_respects_retry_after_beyond_local_backoff_schedule() -> None:
    loaded = config()
    state = LaneState(lane(loaded, "deepseek-official"))
    lease = state.acquire()
    assert lease.admitted
    state.release(lease, capacity_error=True, retry_after=7200)
    snapshot = state.snapshot()
    assert snapshot["failure_streak"] == 1
    assert snapshot["cooldown_remaining"] >= 7199
    assert snapshot["cooldown_remaining"] <= 7200


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


def _serve_local(handler: Any) -> tuple[Any, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread


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


def test_client_disconnect_releases_lane_lease_during_upstream_silence() -> None:
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
            self.connection.sendall(b"data: one\n\n")
            time.sleep(30)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(SilentSSEUpstream)
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
        assert response.read(10)
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

#!/usr/bin/env python3
"""Bounded admission and circuit breaking for shared official-account lanes.

The process is deliberately a one-shot proxy: it never retries an upstream
request and it never rewrites a requested model. It serializes only models
declared as sharing one reviewed provider account, while unrelated providers
and unparsed request shapes pass through CPA unchanged.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import math
import queue
import select
import socket
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

# SSE responses may sit silent between events longer than any downstream idle
# timer (codex defaults to 300s, nginx proxy_read_timeout is 300s here). A
# heartbeat comment line keeps those timers fed and doubles as liveness
# detection: a failed heartbeat write means the client is gone, so the lane
# lease is released promptly instead of lingering until the read timeout.
SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0
SSE_READ_TIMEOUT_SECONDS = 1800.0

# Lane admission bounds. The upstream serves a single `responses` turn in
# 8-140s (measured), so a strictly serial lane (max_inflight=1) with an 8s
# queue budget rejects every concurrent turn the desktop sends -- its main
# response plus its title/summary call -- and the rejections then feed the
# cooldown breaker. Three in-flight requests and four pending requests are a
# bounded increase for that observed request shape; the parser keeps the
# deployed contract exact instead of accepting arbitrary tuning.
ADMISSION_MAX_INFLIGHT = 3
ADMISSION_MAX_PENDING = 4
ADMISSION_QUEUE_TIMEOUT_SECONDS = 120

# How many consecutive capacity failures a lane tolerates before the breaker
# opens. Measured over a 6h window the upstream returned 53 successes against
# 2 transient 503s, yet a threshold of 1 turned those 2 blips into 14 client
# rejections (9 cooldown + 5 half-open): one blip locked the whole lane for
# the full 60s first rung while the upstream was already serving again. The
# breaker must describe a real outage, so it now requires a proven streak --
# a single hiccup is absorbed and the next request proceeds normally.
ADMISSION_COOLDOWN_FAILURE_THRESHOLD = 2

# While a cooldown is open the lane still verifies recovery instead of
# serving the advertised window blindly. Upstream Retry-After values (60s
# here) describe a worst case, but measured blips heal within seconds (the
# 2026-09-26 15:28 UTC window served 200 six seconds into a 60s cooldown),
# so during a cooldown one demand-driven probe per interval is admitted as
# a real request. Probes fire only when a client actually asks -- no
# background timer, no idle traffic -- a failed probe keeps the breaker
# open, and the first probe waits a full interval so the upstream's
# backoff is honoured in substance.
ADMISSION_EARLY_PROBE_INTERVAL_SECONDS = 10.0


def _retire_reader(reader: threading.Thread, proxy: AdmissionProxy) -> None:
    """Abandon a reader thread that may be parked on an unusable socket.

    A close-delimited (HTTP/1.0) response hands its socket to the response
    object, so releasing the upstream connection cannot borrow that socket
    back to unblock the reader -- and on Windows neither shutdown() nor
    close() wakes a recv parked in another thread anyway.  Abandoning the
    thread is safe because the upstream connection is single-use and torn
    down with the request: no data can ever be lost to a later request.
    The thread is counted so a regression is visible in /healthz.
    """
    proxy.note_retired_reader()
    logging.warning("retired_upstream_reader resident=%d", proxy.resident_readers)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return float(value)


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("admission config must be a version 1 mapping")
    for key in ("max_body_bytes", "probe_bytes", "retry_after_max_seconds"):
        _positive_int(config.get(key), key)
    early_probe_interval = _positive_number(
        config.get(
            "early_probe_interval_seconds", ADMISSION_EARLY_PROBE_INTERVAL_SECONDS
        ),
        "early_probe_interval_seconds",
    )
    if config.get("listen_host") != "127.0.0.1":
        raise ValueError("listen_host must remain 127.0.0.1")
    if config.get("upstream_host") != "127.0.0.1":
        raise ValueError("upstream_host must remain 127.0.0.1")
    if config.get("listen_port") != 8318:
        raise ValueError("listen_port must remain 8318")
    if config.get("upstream_port") != 8317:
        raise ValueError("upstream_port must remain 8317")
    if config["probe_bytes"] > config["max_body_bytes"]:
        raise ValueError("probe_bytes must not exceed max_body_bytes")

    lanes = config.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise ValueError("lanes must be a non-empty list")
    model_lanes: dict[str, str] = {}
    normalized_lanes: list[dict[str, Any]] = []
    for index, raw_lane in enumerate(lanes):
        if not isinstance(raw_lane, dict):
            raise ValueError(f"lanes[{index}] must be a mapping")
        name = raw_lane.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"lanes[{index}].name must be non-empty")
        name = name.strip()
        models = raw_lane.get("models")
        if (
            not isinstance(models, list)
            or not models
            or not all(isinstance(item, str) and item.strip() for item in models)
        ):
            raise ValueError(f"lanes[{index}].models must be a non-empty list")
        normalized_models = tuple(
            dict.fromkeys(item.strip().lower() for item in models)
        )
        if len(normalized_models) != len(models):
            raise ValueError(f"lanes[{index}].models must not contain duplicates")
        for model in normalized_models:
            if model in model_lanes:
                raise ValueError(f"model {model!r} is assigned to multiple lanes")
            model_lanes[model] = name
        max_inflight = _positive_int(
            raw_lane.get("max_inflight"), f"lanes[{index}].max_inflight"
        )
        if max_inflight != ADMISSION_MAX_INFLIGHT:
            raise ValueError(
                f"lanes[{index}].max_inflight must remain {ADMISSION_MAX_INFLIGHT}"
            )
        max_pending = raw_lane.get("max_pending")
        if isinstance(max_pending, bool) or not isinstance(max_pending, int):
            raise ValueError(f"lanes[{index}].max_pending must be an integer")
        if max_pending != ADMISSION_MAX_PENDING:
            raise ValueError(
                f"lanes[{index}].max_pending must remain {ADMISSION_MAX_PENDING}"
            )
        queue_timeout = _positive_int(
            raw_lane.get("queue_timeout_seconds"),
            f"lanes[{index}].queue_timeout_seconds",
        )
        if queue_timeout != ADMISSION_QUEUE_TIMEOUT_SECONDS:
            raise ValueError(
                f"lanes[{index}].queue_timeout_seconds must remain "
                f"{ADMISSION_QUEUE_TIMEOUT_SECONDS}"
            )
        schedule = raw_lane.get("cooldown_schedule_seconds")
        if (
            not isinstance(schedule, list)
            or not schedule
            or not all(type(item) is int and item > 0 for item in schedule)
        ):
            raise ValueError(
                f"lanes[{index}].cooldown_schedule_seconds must be positive"
            )
        schedule_cap = _positive_int(
            raw_lane.get("cooldown_cap_seconds"),
            f"lanes[{index}].cooldown_cap_seconds",
        )
        if schedule[-1] != schedule_cap:
            raise ValueError(
                f"lanes[{index}] cooldown schedule must end at cooldown_cap_seconds"
            )
        if schedule_cap > config["retry_after_max_seconds"]:
            raise ValueError(
                f"lanes[{index}].cooldown_cap_seconds must not exceed "
                "retry_after_max_seconds"
            )
        statuses = raw_lane.get("capacity_statuses")
        if (
            not isinstance(statuses, list)
            or not statuses
            or not all(type(item) is int and 100 <= item <= 599 for item in statuses)
        ):
            raise ValueError(
                f"lanes[{index}].capacity_statuses must contain HTTP statuses"
            )
        if 429 not in statuses:
            raise ValueError(f"lanes[{index}].capacity_statuses must include 429")
        markers = raw_lane.get("capacity_markers")
        if (
            not isinstance(markers, list)
            or not markers
            or not all(isinstance(item, str) and item.strip() for item in markers)
        ):
            raise ValueError(
                f"lanes[{index}].capacity_markers must be non-empty strings"
            )
        normalized_lanes.append(
            {
                "name": name,
                "models": normalized_models,
                "max_inflight": max_inflight,
                "max_pending": max_pending,
                "queue_timeout_seconds": queue_timeout,
                "cooldown_schedule_seconds": tuple(schedule),
                "cooldown_cap_seconds": schedule_cap,
                "retry_after_max_seconds": config["retry_after_max_seconds"],
                "early_probe_interval_seconds": early_probe_interval,
                "capacity_statuses": frozenset(statuses),
                "capacity_markers": tuple(item.strip().lower() for item in markers),
            }
        )
    config["lanes"] = tuple(normalized_lanes)
    config["model_lanes"] = model_lanes
    return config


def parse_retry_after(value: str | None, now: float | None = None) -> int | None:
    """Return a bounded positive Retry-After duration, or None if invalid."""

    if not value:
        return None
    value = value.strip()
    try:
        seconds = int(value)
        return max(1, seconds)
    except ValueError:
        pass
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        current = time.time() if now is None else now
        return max(1, math.ceil(date.timestamp() - current))
    except (TypeError, ValueError, OverflowError):
        return None


def is_capacity_response(
    status: int,
    body_prefix: bytes,
    retry_after: str | None,
    config: dict[str, Any],
) -> bool:
    if status in config["capacity_statuses"]:
        return True
    text = body_prefix.decode("utf-8", errors="ignore").lower()
    return any(marker in text for marker in config["capacity_markers"])


def requested_lane(
    path: str, body: bytes, config: dict[str, Any]
) -> tuple[str, str] | None:
    route = urlsplit(path).path.rstrip("/").lower()
    if not (route.endswith("/chat/completions") or route.endswith("/responses")):
        return None
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    model = payload.get("model") if isinstance(payload, dict) else None
    if not isinstance(model, str):
        return None
    normalized = model.strip().lower()
    lane = config["model_lanes"].get(normalized)
    return (lane, normalized) if lane is not None else None


@dataclass(frozen=True)
class Lease:
    admitted: bool
    reason: str
    retry_after: int
    probe: bool = False


class LaneState:
    """Thread-safe single-flight lane with bounded pending work."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._condition = threading.Condition()
        self._max_inflight = config["max_inflight"]
        self._max_pending = config["max_pending"]
        self._queue_timeout = float(config["queue_timeout_seconds"])
        self._schedule = config["cooldown_schedule_seconds"]
        self._retry_after_max = config["retry_after_max_seconds"]
        # Hand-built dicts (tests) may omit the key; load_config always sets it.
        self._early_probe_interval = float(
            config.get(
                "early_probe_interval_seconds", ADMISSION_EARLY_PROBE_INTERVAL_SECONDS
            )
        )
        self.inflight = 0
        self.pending = 0
        self.failure_streak = 0
        self.open_until = 0.0
        self.probe_inflight = False
        # Next monotonic deadline at which a cooldown may be verified by an
        # early probe. Always rewritten when a cooldown opens, and advanced
        # every time a probe is admitted, so a stale value is unreachable.
        self._next_probe_at = 0.0

    def _open_retry_after(self, now: float) -> int:
        return max(1, math.ceil(self.open_until - now))

    def acquire(self) -> Lease:
        deadline = time.monotonic() + self._queue_timeout
        with self._condition:
            while True:
                now = time.monotonic()
                if self.open_until > now:
                    if now >= self._next_probe_at and not self.probe_inflight:
                        self.probe_inflight = True
                        self.inflight += 1
                        self._next_probe_at = now + self._early_probe_interval
                        return Lease(True, "early_probe", 0, probe=True)
                    return Lease(False, "cooldown", self._open_retry_after(now))
                if self.probe_inflight:
                    return Lease(False, "half_open_probe", 1)
                if self.open_until and self.open_until <= now:
                    self.probe_inflight = True
                    self.inflight += 1
                    return Lease(True, "half_open", 0, probe=True)
                if self.inflight < self._max_inflight:
                    self.inflight += 1
                    return Lease(True, "admitted", 0)
                if self.pending >= self._max_pending:
                    return Lease(False, "busy", 1)
                self.pending += 1
                remaining = deadline - now
                if remaining <= 0:
                    self.pending -= 1
                    return Lease(False, "queue_timeout", 1)
                self._condition.wait(timeout=remaining)
                self.pending -= 1

    def release(
        self,
        lease: Lease,
        *,
        capacity_error: bool,
        retry_after: int | None,
    ) -> None:
        now = time.monotonic()
        with self._condition:
            self.inflight = max(0, self.inflight - 1)
            if lease.probe:
                self.probe_inflight = False
            if capacity_error:
                self.failure_streak += 1
                if retry_after is not None:
                    # An upstream-advertised Retry-After is the upstream itself
                    # telling us to back off, so honour it immediately rather
                    # than waiting for the streak to build.
                    delay = max(1, retry_after)
                elif self.failure_streak >= ADMISSION_COOLDOWN_FAILURE_THRESHOLD:
                    # Otherwise a lone blip must not black out the lane: open
                    # only once the streak proves a real outage, and walk the
                    # backoff schedule from its first rung.
                    index = min(
                        self.failure_streak - ADMISSION_COOLDOWN_FAILURE_THRESHOLD,
                        len(self._schedule) - 1,
                    )
                    delay = self._schedule[index]
                else:
                    delay = None
                if delay is not None:
                    self.open_until = now + min(delay, self._retry_after_max)
                    # The first early probe waits a full interval so a fresh
                    # backoff still gets its window before we test it again.
                    self._next_probe_at = now + self._early_probe_interval
            elif lease.probe:
                # A successful half-open probe proves the outage is over.
                self.failure_streak = 0
                self.open_until = 0.0
                self._next_probe_at = 0.0
            else:
                # A success on a normal request also breaks the streak: the
                # threshold counts *consecutive* failures, so two blips
                # separated by a served request must not open the breaker.
                self.failure_streak = 0
            self._condition.notify_all()

    def snapshot(self) -> dict[str, int | float | bool]:
        now = time.monotonic()
        with self._condition:
            return {
                "inflight": self.inflight,
                "pending": self.pending,
                "failure_streak": self.failure_streak,
                "cooldown_remaining": max(0, math.ceil(self.open_until - now)),
                "early_probe_in": max(0, math.ceil(self._next_probe_at - now)),
                "half_open_probe": self.probe_inflight,
            }


class AdmissionProxy:
    def __init__(
        self,
        config: dict[str, Any],
        heartbeat_interval: float = SSE_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self.config = config
        self.heartbeat_interval = heartbeat_interval
        self.lanes = {lane["name"]: LaneState(lane) for lane in config["lanes"]}
        self.lane_config = {lane["name"]: lane for lane in config["lanes"]}
        # Reader threads that could not be joined (see _retire_reader) are
        # parked on a socket that is already being torn down. They are
        # counted rather than forgotten so a stuck-reader regression shows up
        # in /healthz instead of only in the process thread count.
        self.resident_readers = 0
        self._resident_lock = threading.Lock()

    def note_retired_reader(self) -> None:
        with self._resident_lock:
            self.resident_readers += 1

    def health(self) -> dict[str, Any]:
        with self._resident_lock:
            resident_readers = self.resident_readers
        return {
            "status": "ok",
            "listen": (f"{self.config['listen_host']}:{self.config['listen_port']}"),
            "upstream": f"{self.config['upstream_host']}:{self.config['upstream_port']}",
            "retry_after_max_seconds": self.config["retry_after_max_seconds"],
            "retired_readers": resident_readers,
            "lanes": {
                name: {
                    "models": list(self.lane_config[name]["models"]),
                    "state": lane.snapshot(),
                }
                for name, lane in self.lanes.items()
            },
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self) -> None:
        proxy: AdmissionProxy = self.server.proxy  # type: ignore[attr-defined]
        config = proxy.config
        if self.command == "GET" and urlsplit(self.path).path == "/healthz":
            self._send_json(200, proxy.health())
            return
        body = self._read_body(config["max_body_bytes"])
        selection = requested_lane(self.path, body, config)
        lane_name = selection[0] if selection is not None else None
        model = selection[1] if selection is not None else None
        lane_config = proxy.lane_config[lane_name] if lane_name is not None else None
        lease: Lease | None = None
        if lane_name is not None and lane_config is not None:
            lease = proxy.lanes[lane_name].acquire()
            if not lease.admitted:
                self._send_json(
                    429,
                    {
                        "error": {
                            "message": (
                                f"Shared upstream lane {lane_name} is cooling "
                                "down or busy; retry after the advertised interval."
                            ),
                            "type": "official_account_admission_circuit_open",
                            "code": "shared_upstream_capacity",
                        }
                    },
                    retry_after=lease.retry_after,
                )
                logging.info(
                    "lane_reject lane=%s model=%s reason=%s retry_after=%s",
                    lane_name,
                    model,
                    lease.reason,
                    lease.retry_after,
                )
                return
            if lease.probe:
                # An early half-open probe or an expired-cooldown probe is a
                # real client request verifying recovery; mark it so the
                # journal can pair the probe with its upstream_result line.
                logging.info("lane_probe lane=%s model=%s", lane_name, model)
        capacity_error = False
        retry_after: int | None = None
        response_started = False
        conn: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            conn = http.client.HTTPConnection(
                config["upstream_host"], config["upstream_port"], timeout=300
            )
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
            }
            headers["Host"] = f"{config['upstream_host']}:{config['upstream_port']}"
            headers["Content-Length"] = str(len(body))
            # Speak HTTP/1.0 to CPA. An HTTP/1.1 response would use chunked
            # encoding, and http.client's chunked reader must read ahead to
            # the next chunk-size line before returning, parking the loop at
            # every chunk boundary; on Windows neither a client RST nor
            # shutdown() wakes that parked recv, which pins the lane lease. A
            # 1.0 response is close-delimited: read1() is then one raw read
            # with no look-ahead, so the forwarding loop stays bounded. The
            # connection is single-use anyway and closed in the finally block.
            conn._http_vsn = 10
            conn._http_vsn_str = "HTTP/1.0"
            conn.request(self.command, self.path, body=body, headers=headers)
            response = conn.getresponse()
            retry_after_header = response.getheader("Retry-After")
            retry_after = parse_retry_after(retry_after_header)
            transfer_encoding = response.getheader("Transfer-Encoding", "") or ""
            body_allowed = self.command != "HEAD" and response.status not in {
                *range(100, 200),
                204,
                304,
            }
            response_is_chunked = body_allowed and any(
                item.strip().lower() == "chunked"
                for item in transfer_encoding.split(",")
            )
            content_type = (response.getheader("Content-Type") or "").lower()
            response_is_sse = "text/event-stream" in content_type and (
                response_is_chunked or response.getheader("Content-Length") is None
            )
            downstream_chunked = response_is_chunked or response_is_sse
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() in HOP_BY_HOP_HEADERS:
                    continue
                self.send_header(key, value)
            if downstream_chunked:
                # http.client hands over decoded body bytes without framing.
                # Recreate chunked framing for the downstream HTTP/1.1 client
                # - nginx must never wait for a connection close on a live
                # stream - including for SSE responses re-framed from a
                # close-delimited (HTTP/1.0) upstream.
                self.send_header("Transfer-Encoding", "chunked")
            elif body_allowed and response.getheader("Content-Length") is None:
                # Close-delimited upstream responses need an explicit framing
                # signal on the sidecar connection. BaseHTTPRequestHandler
                # otherwise keeps the HTTP/1.1 socket alive after the handler
                # returns and the caller can wait until its read timeout.
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            response_started = True
            probe = bytearray()
            last_forward = time.monotonic()
            write_lock = threading.Lock()
            # While the upstream is silent between SSE events, a heartbeat
            # thread writes SSE comment lines to the downstream client so its
            # idle timer stays fed (codex defaults to 300s, nginx
            # proxy_read_timeout is 300s here). The same thread detects a
            # vanished client; the forwarding loop waits on the upstream
            # socket in bounded slices so it can honour that signal instead of
            # pinning the lane lease until the read timeout.
            heartbeat_stop = threading.Event()
            client_lost = threading.Event()
            heartbeat_interval = proxy.heartbeat_interval if response_is_sse else None

            def _write_chunk(payload: bytes) -> None:
                # Forwarded data and heartbeat comments share one lock so the
                # two writers can never interleave inside one chunked frame.
                with write_lock:
                    if downstream_chunked:
                        self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
                        self.wfile.write(payload)
                        self.wfile.write(b"\r\n")
                    else:
                        self.wfile.write(payload)
                    self.wfile.flush()

            def _client_gone() -> bool:
                # Windows may accept sends into a reset connection without
                # raising, so poll the client socket instead: a FIN peeks as
                # empty and an RST raises straight out of recv.
                try:
                    readable = select.select([self.connection], [], [], 0)[0]
                    if readable and not self.connection.recv(1, socket.MSG_PEEK):
                        return True
                except (OSError, ValueError):
                    return True
                return False

            def _heartbeat_loop() -> None:
                while not heartbeat_stop.wait(heartbeat_interval):
                    if time.monotonic() - last_forward < heartbeat_interval:
                        continue
                    if _client_gone():
                        client_lost.set()
                        return
                    try:
                        _write_chunk(b": keepalive\n\n")
                    except OSError:
                        client_lost.set()
                        return

            # For a will_close (HTTP/1.0) response http.client moves the
            # socket into the response object (conn.sock becomes None right
            # after getresponse); the raw socket still backs response.fp and
            # is what the timeout must act on.
            upstream_sock = conn.sock
            if upstream_sock is None and response.fp is not None:
                raw = getattr(response.fp, "raw", None)
                upstream_sock = getattr(raw, "_sock", None)
            heartbeat_thread: threading.Thread | None = None
            if response_is_sse and upstream_sock is not None:
                # http.client detaches the socket as soon as the upstream
                # signalled close; only stretch the read timeout while the
                # connection still owns the socket.
                upstream_sock.settimeout(SSE_READ_TIMEOUT_SECONDS)
                heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
                heartbeat_thread.start()

            def _read_upstream(sink: queue.Queue[tuple[bytes, bytes | None]]) -> None:
                # The reader thread owns every potentially blocking read of
                # the upstream response, so the forwarding loop never parks in
                # it: on Windows neither a client RST nor shutdown() wakes a
                # recv parked in another thread, which is precisely how a
                # silent upstream used to pin the lane lease for the whole
                # read timeout. read1() forwards whatever arrived in one
                # underlying read; read(amt) would coalesce chunks until amt
                # bytes or EOF and turn a steady SSE stream into 64 KiB
                # bursts.
                try:
                    while True:
                        text = response.read1(65536)
                        if not text:
                            break
                        sink.put((text, None))
                except (OSError, http.client.HTTPException) as exc:
                    sink.put((b"", exc))
                except AttributeError as exc:
                    # `response.close()` may race a reader that has already
                    # observed EOF: http.client._close_conn() then tries to
                    # close its now-cleared fp. Treat that exact cleanup race
                    # as EOF, but preserve any AttributeError raised while a
                    # live response still owns its file object.
                    if getattr(response, "fp", None) is None:
                        return
                    sink.put((b"", exc))
                finally:
                    sink.put((b"", None))

            def _forward(chunk: bytes) -> None:
                nonlocal last_forward
                last_forward = time.monotonic()
                if len(probe) < config["probe_bytes"]:
                    probe.extend(chunk[: config["probe_bytes"] - len(probe)])
                _write_chunk(chunk)

            reader: threading.Thread | None = None
            try:
                if response_is_sse:
                    events: queue.Queue[tuple[bytes, bytes | None]] = queue.Queue()
                    reader = threading.Thread(
                        target=_read_upstream, args=(events,), daemon=True
                    )
                    reader.start()
                    while True:
                        # Bounded wait on the queue keeps the loop responsive:
                        # every slice re-checks liveness, so a vanished client
                        # releases the lane lease within one heartbeat slice
                        # instead of waiting for the upstream to speak.
                        if client_lost.is_set() or _client_gone():
                            _retire_reader(reader, proxy)
                            break
                        try:
                            chunk, error = events.get(timeout=heartbeat_interval)
                        except queue.Empty:
                            continue
                        # A chunk that landed before the client left has
                        # nowhere to go; dropping it cannot lose anything,
                        # because the client is gone.
                        if client_lost.is_set() or _client_gone():
                            _retire_reader(reader, proxy)
                            break
                        if error is not None:
                            # Re-raise in this thread so the shared handler
                            # reports the upstream failure as usual.
                            raise error
                        if not chunk:
                            break
                        _forward(chunk)
                else:
                    # Non-SSE bodies are bounded: read1() returns data or EOF
                    # once the body ends, so there is nothing to park on.
                    while True:
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        _forward(chunk)
            finally:
                heartbeat_stop.set()
                if heartbeat_thread is not None:
                    heartbeat_thread.join(timeout=2)
            if downstream_chunked:
                with write_lock:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            capacity_error = (
                lease is not None
                and lane_config is not None
                and is_capacity_response(
                    response.status,
                    bytes(probe),
                    retry_after_header,
                    lane_config,
                )
            )
            logging.info(
                "upstream_result lane=%s model=%s status=%s capacity=%s retry_after=%s",
                lane_name or "passthrough",
                model or "other",
                response.status,
                str(capacity_error).lower(),
                "present" if retry_after_header else "absent",
            )
        except (OSError, http.client.HTTPException) as exc:
            capacity_error = lease is not None
            logging.warning(
                "upstream_error lane=%s model=%s type=%s",
                lane_name or "passthrough",
                model or "other",
                type(exc).__name__,
            )
            if not response_started:
                self._send_json(
                    503,
                    {
                        "error": {
                            "message": "CPA upstream is temporarily unavailable.",
                            "type": "upstream_unavailable",
                            "code": "upstream_transport_error",
                        }
                    },
                    retry_after=60 if lease else None,
                )
        finally:
            if conn is not None:
                conn.close()
            if response is not None:
                # A will_close (HTTP/1.0) response owns the socket and closing
                # it is the only place that releases the file descriptor. It
                # is nonetheless best-effort: response.fp.close() can block on
                # a reader that is still parked in that file, and the lease
                # must never sit behind cleanup. A reader abandoned by
                # _retire_reader is exactly that case, so the close is bounded
                # and the lane is released either way.
                closer = threading.Thread(target=response.close, daemon=True)
                closer.start()
                closer.join(timeout=2)
            if lease is not None and lane_name is not None:
                proxy.lanes[lane_name].release(
                    lease, capacity_error=capacity_error, retry_after=retry_after
                )

    def _read_body(self, max_body_bytes: int) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if transfer_encoding:
            if transfer_encoding != "chunked":
                raise ValueError("unsupported transfer encoding")
            body = bytearray()
            while True:
                line = self.rfile.readline(65537)
                if not line or len(line) > 65536:
                    raise ValueError("invalid chunked request body")
                try:
                    size = int(line.split(b";", 1)[0].strip(), 16)
                except ValueError as exc:
                    raise ValueError("invalid chunk size") from exc
                if size < 0:
                    raise ValueError("invalid chunk size")
                if size == 0:
                    while True:
                        trailer = self.rfile.readline(65537)
                        if not trailer or trailer in {b"\r\n", b"\n"}:
                            return bytes(body)
                        if len(trailer) > 65536:
                            raise ValueError("invalid chunked trailer")
                if len(body) + size > max_body_bytes:
                    raise ValueError("request body exceeds configured limit")
                body.extend(self.rfile.read(size))
                if self.rfile.read(2) != b"\r\n":
                    raise ValueError("invalid chunk delimiter")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length < 0 or length > max_body_bytes:
            raise ValueError("request body exceeds configured limit")
        return self.rfile.read(length)

    def _send_json(
        self, status: int, payload: dict[str, Any], retry_after: int | None = None
    ) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        if retry_after is not None:
            self.send_header("Retry-After", str(max(1, retry_after)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._proxy()
        except ValueError as exc:
            self._send_json(
                400, {"error": {"message": str(exc), "type": "invalid_request"}}
            )

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._proxy()
        except ValueError as exc:
            self._send_json(
                400, {"error": {"message": str(exc), "type": "invalid_request"}}
            )

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("http " + fmt, *args)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], proxy: AdmissionProxy) -> None:
        self.proxy = proxy
        super().__init__(address, Handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    proxy = AdmissionProxy(config)
    server = Server((config["listen_host"], config["listen_port"]), proxy)
    lane_summary = ",".join(
        f"{lane['name']}:{len(lane['models'])}" for lane in config["lanes"]
    )
    logging.info(
        "ready listen=%s:%s upstream=%s:%s lanes=%s",
        config["listen_host"],
        config["listen_port"],
        config["upstream_host"],
        config["upstream_port"],
        lane_summary,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Bounded admission and circuit breaking for shared official-account lanes.

The process is deliberately a one-shot proxy: it never retries an upstream
request and it never rewrites a requested model. It bounds concurrency for models
declared as sharing one reviewed provider account, while unrelated providers
and unparsed request shapes pass through CPA unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import logging
import math
import queue
import select
import socket
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
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
CPA_TRACE_HEADERS = {"x-cpa-request-id", "x-cpa-admission-reason"}


def diagnostic_hash(value: str | None) -> str:
    if not value or len(value) > 512:
        return "-"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


# SSE responses may sit silent between events longer than any downstream idle
# timer (codex defaults to 300s, nginx proxy_read_timeout is 300s here). A
# heartbeat comment line keeps those timers fed and doubles as liveness
# detection: a failed heartbeat write means the client is gone, so the lane
# lease is released promptly instead of lingering until the read timeout.
SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0
SSE_READ_TIMEOUT_SECONDS = 1800.0
ADMISSION_REQUEST_BODY_TIMEOUT_SECONDS = 30.0

# Lane admission bounds.
ADMISSION_MAX_INFLIGHT_BY_LANE = {
    "chatgpt-oauth": 2,
    "zhipu-coding-plan": 3,
    "deepseek-official": 3,
}
ADMISSION_MAX_PENDING = 4
ADMISSION_QUEUE_TIMEOUT_SECONDS = 120

# A queued request re-checks whether its client is still connected on this
# cadence instead of sleeping for the whole queue budget.
ADMISSION_QUEUE_POLL_SECONDS = 1.0

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


class DownstreamClientDisconnected(OSError):
    """The client closed the response while the proxy was forwarding it."""


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
    resident = proxy.note_retired_reader(reader)
    logging.warning("retired_upstream_reader resident=%d", resident)


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
        expected_max_inflight = ADMISSION_MAX_INFLIGHT_BY_LANE.get(name)
        if expected_max_inflight is None:
            raise ValueError(
                f"lanes[{index}].name has no reviewed max_inflight contract"
            )
        if max_inflight != expected_max_inflight:
            raise ValueError(
                f"lanes[{index}].max_inflight must remain {expected_max_inflight}"
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


def protocol_errors(body_prefix: bytes) -> list[Any]:
    # Generated prose and tool output can discuss rate limits. Only inspect
    # protocol error fields, never arbitrary text in a successful response.
    text = body_prefix.decode("utf-8", errors="ignore")
    errors: list[Any] = []
    candidates = [text] + [
        line[5:].strip() for line in text.splitlines() if line.startswith("data:")
    ]
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("error"):
            errors.append(payload["error"])
        elif payload.get("type") == "error":
            errors.append({k: payload[k] for k in ("code", "message") if k in payload})
        response = payload.get("response")
        if isinstance(response, dict) and response.get("error"):
            errors.append(response["error"])
    return errors


def is_capacity_response(
    status: int,
    body_prefix: bytes,
    retry_after: str | None,
    config: dict[str, Any],
) -> bool:
    errors = protocol_errors(body_prefix)
    error_text = json.dumps(errors, ensure_ascii=False).lower()
    if any(marker in error_text for marker in config["capacity_markers"]):
        return True
    # Known routing/auth failures do not establish shared capacity exhaustion.
    if any(
        code in error_text
        for code in (
            '"model_not_found"',
            '"invalid_api_key"',
            '"authentication_error"',
        )
    ):
        return False
    return status in config["capacity_statuses"]


def requested_model(path: str, body: bytes) -> str | None:
    """Return the model a request names, whether or not a lane owns it.

    `requested_lane` answers "which shared lane must gate this request", so it
    returns None for every model outside the three lanes and for any body it
    cannot parse. Both cases used to collapse into the journal's `model=other`,
    which hides the one field a burst of pass-through 5xx needs to be attributed
    -- which model the client was actually asking for.
    """

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
    if not isinstance(model, str) or not model.strip():
        return None
    return model.strip().lower()


def requested_lane(
    path: str, body: bytes, config: dict[str, Any]
) -> tuple[str, str] | None:
    model = requested_model(path, body)
    if model is None:
        return None
    lane = config["model_lanes"].get(model)
    return (lane, model) if lane is not None else None


@dataclass(frozen=True)
class Lease:
    admitted: bool
    reason: str
    retry_after: int
    probe: bool = False
    # How long this request sat in the lane queue before it was admitted or
    # rejected. The single-flight OAuth lane makes this the dominant latency
    # term, and until it was journalled it was invisible: `upstream_time` in the
    # nginx log folds queue wait and generation into one number.
    waited_ms: int = 0
    generation: int = 0


class LaneState:
    """Thread-safe bounded lane with FIFO pending work and recovery probes."""

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
        self._server_not_before = 0.0
        self._failure_generation = 0
        self._pending_queue: deque[object] = deque()

    def _open_retry_after(self, now: float) -> int:
        return max(1, math.ceil(self.open_until - now))

    def _shed_retry_after(self, now: float) -> int:
        # A shed request that already burned its whole queue budget must not be
        # told to retry in one second: the lane is either still verifying
        # recovery or already in a fresh cooldown, so an immediate retry only
        # re-queues the client for another full budget. Advertise the next
        # moment the lane will actually re-evaluate -- the later of the
        # cooldown expiry and the next probe slot -- with the probe cadence as
        # a floor. `busy` (queue full) shares this: the lane frees on the same
        # two edges.
        next_look = max(
            self.open_until,
            self._next_probe_at,
            now + self._early_probe_interval,
        )
        return max(1, math.ceil(next_look - now))

    def acquire(self, alive: Callable[[], bool] | None = None) -> Lease:
        started = time.monotonic()
        deadline = started + self._queue_timeout

        def lease(
            admitted: bool, reason: str, retry_after: int, probe: bool = False
        ) -> Lease:
            return Lease(
                admitted,
                reason,
                retry_after,
                probe,
                waited_ms=int((time.monotonic() - started) * 1000),
                generation=self._failure_generation,
            )

        with self._condition:
            ticket = object()
            queued = False
            try:
                while True:
                    now = time.monotonic()
                    if alive is not None and not alive():
                        return lease(False, "downstream_gone", 1)
                    if queued and now >= deadline:
                        return lease(
                            False, "queue_timeout", self._shed_retry_after(now)
                        )
                    queue_turn = not self._pending_queue or (
                        queued and self._pending_queue[0] is ticket
                    )
                    if self.probe_inflight:
                        # A half-open probe is a real upstream request and can
                        # take as long as an ordinary generation turn. Rejecting
                        # every concurrent arrival immediately turns one probe
                        # into a desktop 429 burst. Reuse the bounded FIFO queue
                        # instead; a waiter is admitted after the probe proves
                        # recovery, or times out through the normal queue budget.
                        if not queued:
                            if self.pending >= self._max_pending:
                                return lease(False, "busy", self._shed_retry_after(now))
                            self._pending_queue.append(ticket)
                            self.pending += 1
                            queued = True
                        remaining = deadline - now
                        if remaining <= 0:
                            return lease(
                                False, "queue_timeout", self._shed_retry_after(now)
                            )
                        self._condition.wait(
                            timeout=min(remaining, ADMISSION_QUEUE_POLL_SECONDS)
                        )
                        continue
                    if self.open_until > now:
                        if (
                            now >= max(self._next_probe_at, self._server_not_before)
                            and self.inflight < self._max_inflight
                            and queue_turn
                        ):
                            self.probe_inflight = True
                            self.inflight += 1
                            self._next_probe_at = now + self._early_probe_interval
                            return lease(True, "early_probe", 0, probe=True)
                        if queued:
                            remaining = deadline - now
                            if remaining <= 0:
                                return lease(
                                    False, "queue_timeout", self._shed_retry_after(now)
                                )
                            self._condition.wait(
                                timeout=min(remaining, ADMISSION_QUEUE_POLL_SECONDS)
                            )
                            continue
                        return lease(False, "cooldown", self._open_retry_after(now))
                    if self.inflight < self._max_inflight and queue_turn:
                        if self.open_until:
                            self.probe_inflight = True
                            self.inflight += 1
                            return lease(True, "half_open", 0, probe=True)
                        self.inflight += 1
                        return lease(True, "admitted", 0)
                    if not queued:
                        if self.pending >= self._max_pending:
                            return lease(False, "busy", self._shed_retry_after(now))
                        self._pending_queue.append(ticket)
                        self.pending += 1
                        queued = True
                    remaining = deadline - now
                    if remaining <= 0:
                        return lease(
                            False, "queue_timeout", self._shed_retry_after(now)
                        )
                    self._condition.wait(
                        timeout=min(remaining, ADMISSION_QUEUE_POLL_SECONDS)
                    )
            finally:
                if queued:
                    self._pending_queue.remove(ticket)
                    self.pending -= 1
                    self._condition.notify_all()

    def release(
        self,
        lease: Lease,
        *,
        capacity_error: bool,
        retry_after: int | None,
        successful: bool = True,
    ) -> None:
        with self._condition:
            now = time.monotonic()
            self.inflight = max(0, self.inflight - 1)
            if lease.probe:
                self.probe_inflight = False
            if capacity_error:
                self._failure_generation += 1
                self.failure_streak += 1
                if retry_after is not None:
                    # An upstream-advertised Retry-After is the upstream itself
                    # telling us to back off, so honour it immediately rather
                    # than waiting for the streak to build.
                    delay = max(1, retry_after)
                    self._server_not_before = max(self._server_not_before, now + delay)
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
                    self.open_until = max(
                        self.open_until,
                        now + min(delay, self._retry_after_max),
                        self._server_not_before,
                    )
                    # The first early probe waits a full interval so a fresh
                    # backoff still gets its window before we test it again.
                    self._next_probe_at = now + self._early_probe_interval
            elif (
                lease.probe
                and successful
                and lease.generation == self._failure_generation
                and now >= self._server_not_before
            ):
                # A successful half-open probe proves the outage is over.
                self.failure_streak = 0
                self.open_until = 0.0
                self._next_probe_at = 0.0
                self._server_not_before = 0.0
            elif (
                successful
                and not lease.probe
                and (lease.generation == self._failure_generation)
            ):
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
        # parked on a socket that is already being torn down. Track the live
        # resident set separately from the cumulative retirement count so a
        # temporary disconnect does not look like a permanent thread leak.
        self._retired_readers: dict[int, threading.Thread] = {}
        self.retired_readers_total = 0
        self._resident_lock = threading.Lock()

    def note_retired_reader(self, reader: threading.Thread) -> int:
        with self._resident_lock:
            self.retired_readers_total += 1
            self._retired_readers = {
                key: thread
                for key, thread in self._retired_readers.items()
                if thread.is_alive()
            }
            if reader.is_alive():
                self._retired_readers[id(reader)] = reader
            return len(self._retired_readers)

    def _reader_counts(self) -> tuple[int, int]:
        with self._resident_lock:
            self._retired_readers = {
                key: thread
                for key, thread in self._retired_readers.items()
                if thread.is_alive()
            }
            return len(self._retired_readers), self.retired_readers_total

    def health(self) -> dict[str, Any]:
        resident_readers, retired_readers_total = self._reader_counts()
        return {
            "status": "ok",
            "listen": (f"{self.config['listen_host']}:{self.config['listen_port']}"),
            "upstream": f"{self.config['upstream_host']}:{self.config['upstream_port']}",
            "retry_after_max_seconds": self.config["retry_after_max_seconds"],
            "retired_readers": resident_readers,
            "retired_readers_total": retired_readers_total,
            "lanes": {
                name: {
                    "models": list(self.lane_config[name]["models"]),
                    "max_inflight": self.lane_config[name]["max_inflight"],
                    "max_pending": self.lane_config[name]["max_pending"],
                    "queue_timeout_seconds": self.lane_config[name][
                        "queue_timeout_seconds"
                    ],
                    "state": lane.snapshot(),
                }
                for name, lane in self.lanes.items()
            },
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _start_request_trace(self) -> None:
        self._request_id = uuid.uuid4().hex
        route = {
            "/v1/responses": "responses",
            "/v1/chat/completions": "chat",
        }.get(urlsplit(self.path).path, "other")
        client_request_hash = diagnostic_hash(
            self.headers.get("X-Client-Request-Id") or self.headers.get("X-Request-Id")
        )
        instance_hash = diagnostic_hash(self.headers.get("X-Cockpit-Instance-Id"))
        self._trace_context = (
            f"request_id={self._request_id} route={route} "
            f"client_request_hash={client_request_hash} instance_hash={instance_hash}"
        )

    def _send_trace_headers(self, admission_reason: str | None = None) -> None:
        request_id = getattr(self, "_request_id", None)
        if request_id is not None:
            self.send_header("X-CPA-Request-Id", request_id)
        if admission_reason is not None:
            self.send_header("X-CPA-Admission-Reason", admission_reason)

    def _proxy(self) -> None:
        self._start_request_trace()
        proxy: AdmissionProxy = self.server.proxy  # type: ignore[attr-defined]
        config = proxy.config
        if self.command == "GET" and urlsplit(self.path).path == "/healthz":
            self._send_json(200, proxy.health())
            return
        body = self._read_body(config["max_body_bytes"])
        selection = requested_lane(self.path, body, config)
        lane_name = selection[0] if selection is not None else None
        model = selection[1] if selection is not None else None
        # Journal the model the client actually named even when no lane owns the
        # request. `model` stays lane-scoped because the lease, the probe flag
        # and the breaker must keep reading it that way; only the log lines use
        # the wider view, so a pass-through 5xx can be attributed to a model.
        observed_model = model or requested_model(self.path, body)
        lane_config = proxy.lane_config[lane_name] if lane_name is not None else None
        lease: Lease | None = None
        if lane_name is not None and lane_config is not None:
            lease = proxy.lanes[lane_name].acquire(self._downstream_alive)
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
                    admission_reason=lease.reason,
                )
                logging.info(
                    "lane_reject lane=%s model=%s reason=%s retry_after=%s "
                    "waited_ms=%s %s",
                    lane_name,
                    observed_model or "other",
                    lease.reason,
                    lease.retry_after,
                    lease.waited_ms,
                    self._trace_context,
                )
                return
            if lease.probe:
                # An early half-open probe or an expired-cooldown probe is a
                # real client request verifying recovery; mark it so the
                # journal can pair the probe with its upstream_result line.
                logging.info(
                    "lane_probe lane=%s model=%s %s",
                    lane_name,
                    model,
                    self._trace_context,
                )
        capacity_error = False
        successful = False
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
                if key.lower() not in HOP_BY_HOP_HEADERS
                and key.lower() not in CPA_TRACE_HEADERS
                and key.lower() != "host"
            }
            headers["Host"] = f"{config['upstream_host']}:{config['upstream_port']}"
            headers["Content-Length"] = str(len(body))
            headers["X-CPA-Request-Id"] = self._request_id
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
            try:
                self.send_response(response.status, response.reason)
                for key, value in response.getheaders():
                    if (
                        key.lower() in HOP_BY_HOP_HEADERS
                        or key.lower() in CPA_TRACE_HEADERS
                    ):
                        continue
                    self.send_header(key, value)
                self._send_trace_headers()
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
            except (OSError, ValueError) as exc:
                raise DownstreamClientDisconnected(
                    "downstream client disconnected before response headers"
                ) from exc
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
                try:
                    with write_lock:
                        if downstream_chunked:
                            self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
                            self.wfile.write(payload)
                            self.wfile.write(b"\r\n")
                        else:
                            self.wfile.write(payload)
                        self.wfile.flush()
                except (OSError, ValueError) as exc:
                    raise DownstreamClientDisconnected(
                        "downstream client disconnected during response write"
                    ) from exc

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
            if downstream_chunked and not client_lost.is_set():
                try:
                    with write_lock:
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                except (OSError, ValueError) as exc:
                    raise DownstreamClientDisconnected(
                        "downstream client disconnected before response end"
                    ) from exc
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
            successful = (
                200 <= response.status < 300
                and not capacity_error
                and not protocol_errors(bytes(probe))
                and not client_lost.is_set()
            )
            logging.info(
                "upstream_result lane=%s model=%s status=%s capacity=%s "
                "retry_after=%s waited_ms=%s %s",
                lane_name or "passthrough",
                observed_model or "other",
                response.status,
                str(capacity_error).lower(),
                "present" if retry_after_header else "absent",
                lease.waited_ms if lease is not None else 0,
                self._trace_context,
            )
        except DownstreamClientDisconnected:
            # A client-side RST/BrokenPipe is not evidence that the shared
            # upstream account is overloaded. Do not open or extend a lane
            # cooldown for a request that the caller abandoned.
            capacity_error = False
            logging.info(
                "downstream_disconnect lane=%s model=%s waited_ms=%s %s",
                lane_name or "passthrough",
                observed_model or "other",
                lease.waited_ms if lease is not None else 0,
                self._trace_context,
            )
        except (OSError, http.client.HTTPException) as exc:
            # Distinguish "the upstream transport itself failed" from "the
            # upstream answered and told us it is out of capacity". Only the
            # latter is capacity evidence. A connect/read timeout, a reset, or
            # a truncated response is transport jitter that historically made
            # the breaker advance on network noise alone, so it is reported
            # and served a bounded Retry-After but does not move the streak.
            transport_failure = not response_started
            capacity_error = False
            logging.warning(
                "upstream_error lane=%s model=%s type=%s transport_failure=%s "
                "waited_ms=%s %s",
                lane_name or "passthrough",
                observed_model or "other",
                type(exc).__name__,
                str(transport_failure).lower(),
                lease.waited_ms if lease is not None else 0,
                self._trace_context,
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
                    lease,
                    capacity_error=capacity_error,
                    retry_after=retry_after,
                    successful=successful,
                )

    def _downstream_alive(self) -> bool:
        """Report whether the client is still connected while this request queues.

        A queued request has already had its body read, so the socket is either
        silent (client waiting -- alive), readable with a pipelined next request
        (alive), or readable because the peer closed or reset (gone). Windows may
        accept sends into a reset connection without raising, so this peeks
        instead of writing -- the same reason `_client_gone()` in the forwarding
        loop polls.

        The check is deliberately asymmetric: a non-empty buffer is always
        reported alive, because a pipelined request and a body-then-FIN look
        identical at the socket layer and consuming the byte to tell them apart
        would corrupt the next request line on a keep-alive connection. A missed
        reap falls back to the pre-existing full-budget wait; a false reap would
        reject a live request.
        """
        connection = getattr(self, "connection", None)
        if connection is None:
            return True
        try:
            readable = select.select([connection], [], [], 0)[0]
            if readable:
                return bool(connection.recv(1, socket.MSG_PEEK))
        except (OSError, ValueError):
            return False
        return True

    def _read_exact(self, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = self.rfile.read(remaining)
            if not chunk:
                raise ValueError(
                    "request body ended before the declared length was received"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_body(self, max_body_bytes: int) -> bytes:
        previous_timeout = self.connection.gettimeout()
        self.connection.settimeout(ADMISSION_REQUEST_BODY_TIMEOUT_SECONDS)
        try:
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
                    body.extend(self._read_exact(size))
                    if self._read_exact(2) != b"\r\n":
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
            return self._read_exact(length)
        finally:
            try:
                self.connection.settimeout(previous_timeout)
            except OSError:
                pass

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        retry_after: int | None = None,
        admission_reason: str | None = None,
    ) -> None:
        # A client that has already gone (desktop retry storms close the
        # socket as soon as the 429 lands) turns every write below into
        # BrokenPipeError/ConnectionResetError. That is a benign race, not a
        # server fault: the response has nowhere to go, so drop it quietly
        # instead of letting socketserver log a traceback that pollutes the
        # journal and is indistinguishable from a real handler crash.
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            if retry_after is not None:
                self.send_header("Retry-After", str(max(1, retry_after)))
            self._send_trace_headers(admission_reason)
            self.end_headers()
            self.wfile.write(encoded)
            self.wfile.flush()
        except ConnectionError:
            # ConnectionError covers BrokenPipeError and ConnectionResetError
            # (Linux) plus ConnectionAbortedError (Windows WinError 10053),
            # which is what an already-departed client looks like here. The
            # client is gone, so there is nothing to deliver; close this side
            # and move on instead of letting socketserver log a traceback.
            self.close_connection = True
            logging.info(
                "downstream_gone status=%s %s",
                status,
                getattr(self, "_trace_context", "request_id=- route=other"),
            )

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._proxy()
        except socket.timeout:
            self.close_connection = True
            self._send_json(
                408,
                {
                    "error": {
                        "message": "Request body read timed out.",
                        "type": "request_timeout",
                    }
                },
            )
        except ValueError as exc:
            self._send_json(
                400, {"error": {"message": str(exc), "type": "invalid_request"}}
            )

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._proxy()
        except socket.timeout:
            self.close_connection = True
            self._send_json(
                408,
                {
                    "error": {
                        "message": "Request body read timed out.",
                        "type": "request_timeout",
                    }
                },
            )
        except ValueError as exc:
            self._send_json(
                400, {"error": {"message": str(exc), "type": "invalid_request"}}
            )

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def handle_one_request(self) -> None:
        # BaseHTTPRequestHandler.handle() loops on handle_one_request() while
        # the connection is reusable, and the very first statement of each
        # iteration is an unguarded rfile.readline() for the next request
        # line. http.server only catches TimeoutError there, so a client that
        # RSTs between two requests on the same keep-alive connection raises
        # ConnectionResetError straight out of the loop and socketserver logs
        # a full handler traceback. That is a departed client, not a server
        # fault: the previous response was already sent in full, so close
        # this connection quietly. ConnectionError also covers
        # BrokenPipeError/ConnectionAbortedError, and this override is a
        # strict superset of the stdlib behaviour (it delegates everything it
        # does not swallow back to the parent implementation).
        try:
            super().handle_one_request()
        except ConnectionError:
            self.close_connection = True
            logging.info("downstream_gone stage=request_line")

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info(
            "http %s",
            getattr(self, "_trace_context", "request_id=- route=other"),
        )


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

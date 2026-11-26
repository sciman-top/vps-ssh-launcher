#!/usr/bin/env python3
"""Bounded admission and circuit breaking for shared official-account lanes.

The process is deliberately a one-shot proxy: it never retries an upstream
request and it never rewrites a requested model. It bounds concurrency for models
declared as sharing one reviewed provider account, while unrelated providers
and unparsed request shapes pass through CPA unchanged.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import http.client
import json
import logging
import math
import os
import queue
import re
import select
import socket
import stat
import struct
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit


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
# BaseHTTPRequestHandler.send_response() already emits Server and Date headers.
# Forwarding upstream Date/Server again produces duplicate response headers
# (observed as Nginx "upstream sent duplicate header line: Date" warnings).
GENERATED_RESPONSE_HEADERS = {"date", "server"}
CPA_TRACE_HEADERS = {"x-cpa-request-id", "x-cpa-admission-reason"}

# Compression is negotiated end-to-end, but every hop here (nginx -> admission
# -> CPA) is loopback, so it buys nothing and breaks clients: forwarding a
# client's Accept-Encoding made CPA gzip SSE responses, and the re-framed
# downstream headers below do not reliably carry Content-Encoding, handing
# clients compressed bytes they cannot detect (2026-11-04: DSH/undici, which
# sends Accept-Encoding by default, failed every streaming protocol with
# "Stream ended without finish_reason"/"missing_terminal" while curl and codex
# passed). Strip it before CPA instead of forwarding it.
STRIPPED_REQUEST_HEADERS = {"accept-encoding"}


def diagnostic_hash(value: str | None) -> str:
    if not value or len(value) > 512:
        return "-"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _linux_process_start(pid: int) -> str | None:
    try:
        return (
            Path(f"/proc/{pid}/task/{pid}/stat")
            .read_text(encoding="ascii")
            .rsplit(")", 1)[1]
            .split()[19]
        )
    except (IndexError, OSError, UnicodeError):
        return None


def _linux_process_matches(pid: int, start_time: str, ppid: int | None = None) -> bool:
    try:
        fields = (
            Path(f"/proc/{pid}/task/{pid}/stat")
            .read_text(encoding="ascii")
            .rsplit(")", 1)[1]
            .split()
        )
        return fields[19].casefold() == start_time.casefold() and (
            ppid is None or int(fields[1]) == ppid
        )
    except (IndexError, OSError, UnicodeError, ValueError):
        return False


REQUEST_GUARD_ACQUIRE_TIMEOUT_SECONDS = 2.0
REQUEST_GUARD_HANDSHAKE = "C" + "0" * 15
REQUEST_GUARD_RESPONSE_TIMEOUT_SECONDS = 2.0


def _guard_peer_identity(conn: socket.socket) -> tuple[int, str]:
    # SO_PEERCRED is struct ucred {pid, uid, gid}; Linux SCM_CREDENTIALS adds
    # the start-time field in a different ancillary-data shape. Reading past
    # 12 bytes truncates a variable-length option reply and supplies no start.
    peer_pid, _, _ = struct.unpack(
        "3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    )
    return peer_pid, _linux_process_start(peer_pid) or ""


def _read_exact(conn: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        part = conn.recv(size - len(data))
        if not part:
            raise OSError(errno.ECONNRESET, "request guard broker disconnected")
        data += part
    return data


def _read_guard_reply(sock: socket.socket) -> bytes:
    sock.settimeout(REQUEST_GUARD_RESPONSE_TIMEOUT_SECONDS)
    try:
        return _read_exact(sock, 6)
    finally:
        sock.settimeout(None)


def _receive_guard_lease(sock: socket.socket) -> tuple[bytes, int | None]:
    sock.settimeout(REQUEST_GUARD_RESPONSE_TIMEOUT_SECONDS)
    try:
        reply, ancillary, _, _ = socket.recv_fds(sock, 6, 1, socket.MSG_CMSG_CLOEXEC)
    finally:
        sock.settimeout(None)
    received = [int(item) for item in ancillary]
    if len(received) > 1:
        for item in received:
            os.close(item)
        raise OSError("request guard sent too many lease descriptors")
    return reply, received[0] if received else None


def _checked_launcher_fd(value: int, label: str) -> int:
    if value < 3 or value > 0x7FFFFFFF:
        raise ValueError(f"{label} descriptor is invalid")
    os.set_blocking(value, True)
    os.set_inheritable(value, False)
    return value


def _read_launcher_receipt(path: Path) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, 4096)
            if not chunk:
                break
            size += len(chunk)
            if size > 4096:
                raise ValueError("admission launcher receipt is oversized")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, ValueError):
        raise ValueError("admission launcher receipt is malformed") from None
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "token",
            "identity",
            "parent_pid",
            "guard_pid",
            "guard_start",
        }
        or not all(
            isinstance(value[key], str) and len(value[key]) == 32
            for key in ("token", "identity")
        )
        or type(value["parent_pid"]) is not int
        or type(value["guard_pid"]) is not int
        or not isinstance(value["guard_start"], str)
    ):
        raise ValueError("admission launcher receipt is invalid")
    return value


class RequestGuardBrokerUnavailable(RuntimeError):
    """The startup guard is absent; fail closed before opening a request."""


def start_request_guard_broker(
    path: Path,
    expected_parent_pid: int,
    expected_parent_start: str,
    guard_fd: int,
    guard_pid: int,
    guard_start: str,
    expected_admission_pid: int,
    expected_admission_start: str,
) -> tuple[socket.socket, str]:
    if os.name != "posix":
        raise RequestGuardBrokerUnavailable("request guard requires POSIX sockets")
    if (
        guard_fd < 3
        or guard_pid <= 0
        or expected_parent_pid != os.getppid()
        or expected_admission_pid != os.getpid()
        or expected_admission_start != _linux_process_start(expected_admission_pid)
        or guard_start != _linux_process_start(guard_pid)
        or not _linux_process_matches(expected_parent_pid, expected_parent_start)
        or not _linux_process_matches(guard_pid, guard_start, expected_parent_pid)
    ):
        raise RequestGuardBrokerUnavailable("guard process lineage is invalid")
    try:
        broker_sock = socket.socket(fileno=guard_fd)
        peer_pid, peer_start = _guard_peer_identity(broker_sock)
    except OSError as exc:
        raise RequestGuardBrokerUnavailable("guard peer credentials failed") from exc
    if peer_pid != guard_pid or peer_start != guard_start:
        broker_sock.close()
        raise RequestGuardBrokerUnavailable("guard peer identity is invalid")
    try:
        broker_sock.settimeout(REQUEST_GUARD_RESPONSE_TIMEOUT_SECONDS)
        broker_sock.sendall(REQUEST_GUARD_HANDSHAKE.encode("ascii"))
        if _read_guard_reply(broker_sock) != b"\0" * 6:
            raise OSError("request guard handshake failed")
    except OSError as exc:
        broker_sock.close()
        raise RequestGuardBrokerUnavailable("request guard handshake failed") from exc
    return broker_sock, json.dumps({"guard_fd": guard_fd, "guard_pid": guard_pid})


class RequestGuardBroker:
    """Request-thread client; the lock descriptor lives only in the broker."""

    def __init__(
        self,
        path: Path,
        guard_fd: int | None = None,
        *,
        parent_pid: int = 0,
        parent_start: str = "",
        guard_pid: int = 0,
        guard_start: str = "",
        admission_pid: int = 0,
        admission_start: str = "",
    ) -> None:
        self.path = path
        self._guard_fd = guard_fd
        self._parent_pid = parent_pid
        self._parent_start = parent_start
        self._guard_pid = guard_pid
        self._guard_start = guard_start
        self._admission_pid = admission_pid
        self._admission_start = admission_start
        self._lock = threading.Lock()
        self._next_id = 1
        self._sock: socket.socket | None = None
        self._pid: str = "-"
        self.last_error: int | None = None
        self._leases: dict[int, int] = {}

    def start(self) -> None:
        with self._lock:
            if self._sock is not None:
                return
            if self._guard_fd is None or not self._parent_pid:
                raise RequestGuardBrokerUnavailable(
                    "reviewed launcher process context is missing"
                )
            self._sock, self._pid = start_request_guard_broker(
                self.path,
                self._parent_pid,
                self._parent_start,
                self._guard_fd,
                self._guard_pid,
                self._guard_start,
                self._admission_pid,
                self._admission_start,
            )

    def acquire(self) -> tuple[int, str, int] | None:
        if not self._lock.acquire(timeout=REQUEST_GUARD_ACQUIRE_TIMEOUT_SECONDS):
            self.last_error = errno.ETIMEDOUT
            return None
        try:
            if self._sock is None:
                self.last_error = None
                try:
                    self.start()
                except (OSError, UnicodeError, RequestGuardBrokerUnavailable):
                    self.last_error = errno.ENOENT
            if self._sock is None:
                return None
            lock_id = self._next_id
            self._next_id += 1
            try:
                frame = f"A{lock_id:015d}".encode("ascii")
                self._sock.sendall(frame)
                response, received_fd = _receive_guard_lease(self._sock)
            except OSError as exc:
                self.last_error = exc.errno
                sock = self._sock
                self._sock = None
                sock.close()
                return None
            error_number = struct.unpack("!i", response[:4])[0]
            if error_number != 0:
                self.last_error = error_number
                if received_fd is not None:
                    os.close(received_fd)
                return None
            if received_fd is None:
                self.last_error = errno.EBADF
                return None
            self._leases[lock_id] = received_fd
            fd = received_fd
            return lock_id, self._pid, fd
        finally:
            self._lock.release()

    def release(self, lock_id: int) -> None:
        with self._lock:
            if self._sock is None:
                raise OSError(errno.ECONNRESET, "request guard broker unavailable")
            try:
                frame = f"R{lock_id:015d}".encode("ascii")
                self._sock.sendall(frame)
                _read_guard_reply(self._sock)
                lease_fd = self._leases.pop(lock_id, None)
                if lease_fd is not None:
                    os.close(lease_fd)
            except OSError as exc:
                lease_fd = self._leases.pop(lock_id, None)
                if lease_fd is not None:
                    os.close(lease_fd)
                sock = self._sock
                self._sock = None
                if sock is not None:
                    sock.close()
                self._pid = "-"
                raise OSError(
                    exc.errno or errno.ECONNRESET,
                    "request guard broker release failed",
                ) from exc

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                sock = self._sock
                self._sock = None
                sock.settimeout(REQUEST_GUARD_RESPONSE_TIMEOUT_SECONDS)
                try:
                    sock.sendall(b"X000000000000000")
                    _read_guard_reply(sock)
                except OSError:
                    pass
                sock.close()
            for lease_fd in self._leases.values():
                os.close(lease_fd)
            self._leases.clear()


# SSE responses may sit silent between events longer than any downstream idle
# timer (codex defaults to 300s, nginx proxy_read_timeout is 300s here). A
# heartbeat comment line keeps those timers fed and doubles as liveness
# detection: a failed heartbeat write means the client is gone, so the lane
# lease is released promptly instead of lingering until the read timeout.
SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0
SSE_READ_TIMEOUT_SECONDS = 1800.0
SSE_QUEUE_CHUNKS = 8
SSE_QUEUE_POLL_SECONDS = 0.1
ADMISSION_REQUEST_BODY_TIMEOUT_SECONDS = 30.0
# A queued handler already owns a complete request body while it waits for a
# lane slot. Keeping every body in RAM makes the reviewed 32 MiB body cap
# multiply by the lane's pending queue (and by other in-flight lanes), which
# is the shape seen in the admission process's large anonymous maps. Keep
# ordinary requests in memory, but spill larger lane-bound bodies to an
# unlinked 0600 temporary file before queueing. ``http.client`` can stream a
# readable body with the explicit Content-Length, so the upstream path does
# not need to materialize the body again.
ADMISSION_REQUEST_BODY_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024

# Lane admission bounds.
ADMISSION_MAX_INFLIGHT_BY_LANE = {
    "chatgpt-oauth": 2,
    "zhipu-coding-plan": 3,
    "zcode-start-plan": 1,
    "deepseek-official": 3,
    "antigravity-oauth": 2,
    "claude-oauth": 2,
    # One, not two. WorkBuddy is plugin-scheduled, so CPA's routing.strategy
    # never reaches it: both plugin pickers choose from candidate order and the
    # exhaustion cache without observing in-flight state, and
    # account_max_concurrent=1 is enforced only at execution. Two lane slots
    # therefore handed both concurrent requests to the same first candidate and
    # the second failed `credential busy` in ~1ms. The current
    # max-retry-credentials=3 can rotate within the initial round, but serial
    # admission remains the account-safety bound.
    # Serialising is the only sound bound until a picker can see busy creds.
    "workbuddy-oauth": 1,
    # The INTL credentials use the same plugin scheduler and credential-busy
    # behavior. A separate state bucket isolates failures; it does not justify
    # two concurrent requests against one credential.
    "workbuddy-intl-oauth": 1,
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

# WorkBuddy's plugin keeps an unparseable 6004 in its per-(credential, model)
# registry for one minute.  Mirror that lower bound at the admission boundary
# so a mixed-provider request does not re-enter the plugin pool on every turn
# while the upstream window is still active.  A provider-declared reset, when
# present, always takes precedence and is bounded by the lane's normal cap.
WORKBUDDY_6004_DEFAULT_COOLDOWN_SECONDS = 60
# A 6004 reset is one credential's model window, not a deadline for the shared
# alias. `deepseek-v4.1-flash` is a pooled alias (WorkBuddy primary, OpenCode
# backup) and admission gates by model name, so mirroring the full window also
# removed the backup route: on 2026-11-06 one 6004 body carried a 22254s reset,
# the name stayed hard-pinned (no probe, no queue) and every retry returned the
# same 429 while the same account was healthy in the official client. Mirror it
# only at probe scale -- the plugin still owns the real per-credential park, and
# a bounded park lets the next attempt reach another credential or the backup
# provider instead of deleting the alias for hours.
WORKBUDDY_6004_MAX_COOLDOWN_SECONDS = 300
# WorkBuddy serves some models at x0.00 credits. They consume no quota, so a
# credential-level `Credits exhausted` refusal does not apply to them and must
# not gate them. The list is lane configuration (`zero_cost_models`), not a
# constant: which models are free is provider catalog data that changes without
# a code change.
WORKBUDDY_6004_RESET_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})[ T]"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s*UTC\+8"
)
# A standing account-level restriction, carried in prose rather than in a typed
# error field. Every lane that can be suspended has returned this shape at some
# point: ChatGPT/Codex, WorkBuddy/CodeBuddy, and Antigravity's PERMISSION_DENIED
# wave. The list is applied to every durable lane so a suspension cannot reach
# the capacity ladder (60/120/240/480/900 s) and be retried into a wider
# enforcement response; see oauth_risk_signal.
STANDING_RESTRICTION_MARKERS = (
    "account has been suspended",
    "account has been disabled",
    "account is suspended",
    "account is disabled",
    "account suspended",
    "account disabled",
    "account_deactivated",
    "account_deactivated_by_provider",
)

# Account-level suspension reasons carried in a Google ErrorInfo detail (the
# shape Antigravity returned in the 2026-11-06/07 wave) or in a message body.
STANDING_RESTRICTION_REASONS = (
    "TOS_VIOLATION",
    "POLICY_VIOLATION",
    "SERVICE_DISABLED",
    "ACCOUNT_DISABLED",
    "ACCOUNT_SUSPENDED",
    "VALIDATION_REQUIRED",
)

# Gemini-family transports report quota pressure as RESOURCE_EXHAUSTED: in the
# ErrorInfo reason, in the error status word, or in a gRPC status code. None of
# those strings contain "rate limit"/"quota exceeded"/"usage limit", so without
# this marker a genuine Google quota refusal that arrives as HTTP 200 (or that
# CPA re-wraps) is forwarded to the client and never cools the lane -- the one
# direction this gateway must never fail in, because the next request spends
# more of the same account's quota. Antigravity-only: the WorkBuddy/Zhipu lanes
# return provider-specific 6xxx codes instead.
GEMINI_CAPACITY_MARKERS = ("resource_exhausted",)
# Magnitude bound for a credential-scoped cooling signal on a lane whose model
# names are declared pooled (`pooled_models`): the signal is still honoured, but
# it may not park a name that a second provider also serves.
POOLED_LANE_MODEL_COOLDOWN_SECONDS = 300

ADMISSION_EARLY_PROBE_INTERVAL_SECONDS = 10.0
ADMISSION_RESET_PROBE_INTERVAL_SECONDS = 300.0
# Rolling window for the optional per-lane request-rate guard.
ADMISSION_RPM_WINDOW_SECONDS = 60.0
# Upper bound on a configured requests_per_minute so a projection typo cannot
# silently disable the guard with an enormous value.
ADMISSION_RPM_MAX_CEILING = 600
WORKBUDDY_LANES = frozenset({"workbuddy-oauth", "workbuddy-intl-oauth"})
STRICT_OAUTH_LANES = frozenset(
    {"antigravity-oauth", "claude-oauth", "workbuddy-oauth", "workbuddy-intl-oauth"}
)
DURABLE_OAUTH_LANES = STRICT_OAUTH_LANES | {"chatgpt-oauth"}
# One bounded verification window for a stale write-ahead marker found at
# startup (see the LaneState load path); short enough to be invisible to
# clients that already back off for minutes on the 429 it advertises.
STALE_WRITE_AHEAD_COOLDOWN_SECONDS = 90


class DownstreamClientDisconnected(OSError):
    """The client closed the response while the proxy was forwarding it."""


def _read_sse_upstream(
    response: Any,
    sink: queue.Queue[tuple[bytes, BaseException | None]],
    stop: threading.Event,
) -> None:
    """Backpressure the upstream without trapping a reader after cancellation."""

    def send(chunk: bytes, error: BaseException | None = None) -> None:
        while not stop.is_set():
            try:
                sink.put((chunk, error), timeout=SSE_QUEUE_POLL_SECONDS)
                return
            except queue.Full:
                continue

    try:
        while not stop.is_set():
            # read1 forwards the first available bytes instead of waiting for
            # a full 64 KiB buffer, preserving SSE first-event latency.
            chunk = response.read1(65536)
            if not chunk:
                break
            send(chunk)
    except (OSError, http.client.HTTPException) as exc:
        send(b"", exc)
    except AttributeError as exc:
        # Closing a response may clear fp while a reader observes EOF.
        if getattr(response, "fp", None) is not None:
            send(b"", exc)
    finally:
        send(b"")


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
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite positive number")
    return number


def _shared_route_model_lanes(path: Path) -> dict[str, str]:
    """Load the fail-closed model/lane contract from the adjacent manifest."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("shared route manifest is missing or invalid") from exc
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("version")) is not int
        or manifest.get("version") != 1
    ):
        raise ValueError("shared route manifest must be a version 1 mapping")

    pooled_aliases = manifest.get("pooled_aliases", [])
    if not isinstance(pooled_aliases, list) or not all(
        isinstance(alias, str) and alias.strip() for alias in pooled_aliases
    ):
        raise ValueError(
            "shared route manifest pooled_aliases must be non-empty strings"
        )
    normalized_pooled_aliases = {alias.strip().lower() for alias in pooled_aliases}
    if len(normalized_pooled_aliases) != len(pooled_aliases):
        raise ValueError("shared route manifest pooled_aliases must be unique")

    # Plugin pins remain upstream names in the plugin configuration.  The
    # WorkBuddy INTL entry point is a separately forked CPA alias, so translate
    # only the INTL plugin realm while keeping the CN bare name untouched.
    plugin_aliases: dict[str, dict[str, str]] = {}
    raw_aliases = manifest.get("oauth_model_aliases", {})
    if isinstance(raw_aliases, dict):
        for provider, entries in raw_aliases.items():
            if not isinstance(provider, str) or not isinstance(entries, list):
                continue
            mapping: dict[str, list[tuple[str, bool]]] = {}
            for entry in entries:
                if (
                    isinstance(entry, dict)
                    and isinstance(entry.get("name"), str)
                    and isinstance(entry.get("alias"), str)
                    and entry["name"].strip()
                    and entry["alias"].strip()
                ):
                    name_key = entry["name"].strip().lower()
                    mapping.setdefault(name_key, []).append(
                        (entry["alias"].strip().lower(), bool(entry.get("fork")))
                    )
            plugin_aliases[provider] = mapping

    expected: dict[str, str] = {}
    for key in ("providers", "oauth_routes", "plugin_routes"):
        routes = manifest.get(key)
        if not isinstance(routes, list):
            raise ValueError(f"shared route manifest {key} must be a list")
        for index, route in enumerate(routes):
            if not isinstance(route, dict):
                raise ValueError(
                    f"shared route manifest {key}[{index}] must be a mapping"
                )
            shared = route.get("shared_consumption", False)
            if type(shared) is not bool:
                raise ValueError(
                    f"shared route manifest {key}[{index}].shared_consumption must be boolean"
                )
            if not shared:
                continue
            lane = route.get("admission_lane")
            models = route.get("models")
            if not isinstance(lane, str) or not lane.strip():
                raise ValueError(
                    f"shared route manifest {key}[{index}] requires an admission lane"
                )
            if not isinstance(models, list) or not models:
                raise ValueError(
                    f"shared route manifest {key}[{index}] requires models"
                )
            for model in models:
                if key == "plugin_routes":
                    # 2026-11-17 luode0320 cutover: unfold the fork bridge
                    # like scripts/cpa_catalog_check.py plugin_lane_aliases —
                    # every alias entry contributes its client name, and the
                    # bare upstream id stays listed only under fork=true
                    # (fork omitted REPLACES the id with the alias).
                    model_name = str(model).strip()
                    expansions: list[str] = []
                    if isinstance(route.get("provider"), str):
                        pin_entries = plugin_aliases.get(route["provider"], {}).get(
                            model_name.lower()
                        )
                        if pin_entries:
                            keep_original = False
                            for alias_name, fork in pin_entries:
                                expansions.append(alias_name)
                                keep_original = keep_original or fork
                            if keep_original:
                                expansions.append(model_name.lower())
                    if not expansions:
                        expansions = [model_name]
                    normalized_names = expansions
                else:
                    alias = model.get("alias") if isinstance(model, dict) else None
                    if not isinstance(alias, str) or not alias.strip():
                        raise ValueError(
                            f"shared route manifest {key}[{index}] contains an invalid model alias"
                        )
                    normalized_names = [alias.strip().lower()]
                normalized_lane = lane.strip()
                for normalized in normalized_names:
                    existing_lane = expected.get(normalized)
                    if existing_lane is None:
                        expected[normalized] = normalized_lane
                    elif normalized not in normalized_pooled_aliases:
                        raise ValueError(
                            "shared route manifest assigns a model more than once"
                        )
                    elif existing_lane != normalized_lane:
                        raise ValueError(
                            "shared route manifest assigns a pooled model to different lanes"
                        )
    return expected


def load_config(
    path: Path,
    *,
    route_manifest_path: Path | None = None,
) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(config, dict)
        or type(config.get("version")) is not int
        or config.get("version") != 1
    ):
        raise ValueError("admission config must be a version 1 mapping")
    for key in ("max_body_bytes", "probe_bytes", "retry_after_max_seconds"):
        _positive_int(config.get(key), key)
    raw_stream_probe_bytes = config.get("stream_probe_bytes")
    if raw_stream_probe_bytes is None:
        stream_probe_bytes = config["probe_bytes"]
    else:
        stream_probe_bytes = _positive_int(raw_stream_probe_bytes, "stream_probe_bytes")
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
    if stream_probe_bytes < config["probe_bytes"]:
        raise ValueError("stream_probe_bytes must be at least probe_bytes")
    if stream_probe_bytes > config["max_body_bytes"]:
        raise ValueError("stream_probe_bytes must not exceed max_body_bytes")
    config["stream_probe_bytes"] = stream_probe_bytes

    lanes = config.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise ValueError("lanes must be a non-empty list")
    model_lanes: dict[str, str] = {}
    lane_names: set[str] = set()
    normalized_lanes: list[dict[str, Any]] = []
    for index, raw_lane in enumerate(lanes):
        if not isinstance(raw_lane, dict):
            raise ValueError(f"lanes[{index}] must be a mapping")
        name = raw_lane.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"lanes[{index}].name must be non-empty")
        name = name.strip()
        if name in lane_names:
            raise ValueError(f"lanes[{index}] duplicate lane name {name!r}")
        lane_names.add(name)
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
        # Which of those markers name a *model* rather than the credential.
        # Optional: a lane that omits the key keeps the historical lane-wide
        # breaker, so an older projected JSON cannot change admission semantics.
        model_markers = raw_lane.get("model_capacity_markers", [])
        if not isinstance(model_markers, list) or not all(
            isinstance(item, str) and item.strip() for item in model_markers
        ):
            raise ValueError(
                f"lanes[{index}].model_capacity_markers must be non-empty strings"
            )
        # Optional: models this lane serves at zero cost, so a billing refusal
        # (one credential's credit balance) must not gate them. Absent => empty,
        # which keeps the pre-existing lane-wide behavior for any older JSON.
        zero_cost = raw_lane.get("zero_cost_models", [])
        if not isinstance(zero_cost, list) or not all(
            isinstance(item, str) and item.strip() for item in zero_cost
        ):
            raise ValueError(
                f"lanes[{index}].zero_cost_models must be non-empty strings"
            )
        # Optional: models this lane serves under a pooled alias, i.e. names a
        # second provider also sells. Absent => empty, which keeps the
        # historical lane-wide capacity breaker for any older projected JSON.
        pooled = raw_lane.get("pooled_models", [])
        if not isinstance(pooled, list) or not all(
            isinstance(item, str) and item.strip() for item in pooled
        ):
            raise ValueError(f"lanes[{index}].pooled_models must be non-empty strings")
        normalized_pooled = frozenset(item.strip().lower() for item in pooled)
        if not normalized_pooled.issubset(set(normalized_models)):
            raise ValueError(
                f"lanes[{index}].pooled_models must be listed in lanes[{index}].models"
            )
        # Optional per-lane request-rate guard. Absent => unlimited (historical
        # behavior), so an older projected JSON keeps its semantics. A positive
        # integer bounds normal admissions inside a rolling 60s window.
        rpm = raw_lane.get("requests_per_minute")
        if rpm is not None:
            if isinstance(rpm, bool) or not isinstance(rpm, int) or rpm <= 0:
                raise ValueError(
                    f"lanes[{index}].requests_per_minute must be a positive integer"
                )
            if rpm > ADMISSION_RPM_MAX_CEILING:
                raise ValueError(
                    f"lanes[{index}].requests_per_minute must not exceed "
                    f"{ADMISSION_RPM_MAX_CEILING}"
                )
        # Optional bounded recovery probe for a mirrored capacity cooldown.
        # Absent => 0 (disabled), which keeps the historical blind window for
        # any older projected JSON. A positive value re-tests recovery one
        # request at a time on that cadence while the lane cools; holds and
        # operator review never probe.
        mirror_probe = raw_lane.get("mirror_probe_interval_seconds")
        if mirror_probe is not None:
            mirror_probe = _positive_number(
                mirror_probe, f"lanes[{index}].mirror_probe_interval_seconds"
            )
            if mirror_probe < 30:
                raise ValueError(
                    f"lanes[{index}].mirror_probe_interval_seconds must be at "
                    "least 30 seconds"
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
                "mirror_probe_interval_seconds": mirror_probe or 0,
                "capacity_statuses": frozenset(statuses),
                "capacity_markers": tuple(item.strip().lower() for item in markers),
                "model_capacity_markers": tuple(
                    item.strip().lower() for item in model_markers
                ),
                "zero_cost_models": frozenset(
                    item.strip().lower() for item in zero_cost
                ),
                "pooled_models": normalized_pooled,
                "requests_per_minute": rpm,
            }
        )
    expected_model_lanes = _shared_route_model_lanes(
        route_manifest_path or path.with_name("cpa_provider_routes.json")
    )
    if model_lanes != expected_model_lanes:
        raise ValueError(
            "admission lane model mapping must exactly match shared route manifest"
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


def protocol_errors(body_prefix: bytes, lane_name: str | None = None) -> list[Any]:
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
            # CPA wraps an upstream refusal as prose ending in the provider's
            # own JSON: `upstream 429: {"code":6008,...}`. That embedded object
            # is the only place a numeric provider code survives, so surface it
            # under the `message` field every classifier already inspects.
            payload = None
            if lane_name in WORKBUDDY_LANES:
                match = re.search(r"\{.*\}", candidate, re.DOTALL)
                if match:
                    try:
                        inner = json.loads(match.group(0))
                    except ValueError:
                        inner = None
                    if isinstance(inner, dict) and isinstance(inner.get("code"), int):
                        errors.append({"code": inner["code"], "message": candidate})
            elif lane_name == "chatgpt-oauth":
                match = re.search(r"\{.*\}", candidate, re.DOTALL)
                if match:
                    try:
                        inner = json.loads(match.group(0))
                    except ValueError:
                        inner = None
                    if isinstance(inner, dict):
                        if inner.get("error"):
                            errors.append(inner["error"])
                        elif inner.get("type") == "error":
                            errors.append(
                                {k: inner[k] for k in ("code", "message") if k in inner}
                            )
            if payload is None:
                continue
        if isinstance(payload, dict):
            if payload.get("error"):
                errors.append(payload["error"])
            elif (
                payload.get("code") == 11140
                or (
                    lane_name in WORKBUDDY_LANES
                    and payload.get("code") in (6003, 6004, 6008)
                )
            ) and isinstance(payload.get("msg"), str):
                # 6008 is "Credits exhausted" and arrives as this bare shape,
                # not inside an `error` envelope. Omitting it here made the one
                # real quota event invisible to every classifier downstream.
                errors.append({"code": payload["code"], "message": payload["msg"]})
            elif payload.get("type") == "error":
                errors.append(
                    {k: payload[k] for k in ("code", "message") if k in payload}
                )
            response = payload.get("response")
            if isinstance(response, dict) and response.get("error"):
                errors.append(response["error"])
            continue
    return errors


def quoted_upstream_code(message: Any) -> int | None:
    # CPA forwards upstream failures as `upstream <status>: {json}` inside
    # error.message and labels the wrapper with HTTP-shape fields (type
    # rate_limit_error, code rate_limit_exceeded). The quoted provider code,
    # when parseable, is the upstream's own classification and outranks the
    # wrapper label; an unparseable wrapper stays fail-closed.
    if not isinstance(message, str):
        return None
    # WorkBuddy renders its cached model limit as a bilingual message naming
    # the provider code and the model-only scope. Both deployed generations
    # append a truncated `| raw: {"code":6004,...` dump with no closing brace
    # (observed 2026-10-25), so JSON extraction below cannot see the code.
    # Match the pinned plugin's explicit classification, not generic prose.
    if "model-level rate limit (code 6004)" in message and (
        "other models on the same credential are unaffected" in message
        or "other models on the same credential keep working" in message
    ):
        return 6004
    match = re.search(r"\{.*\}", message, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return None
    code = payload.get("code") if isinstance(payload, dict) else None
    return code if isinstance(code, int) else None


def workbuddy_6004_retry_after(
    status: int,
    body: bytes,
    lane: dict[str, Any],
    now: float | None = None,
) -> int | None:
    """Return a bounded model cooldown for a WorkBuddy 6004 response.

    The plugin owns the per-credential 6004 park.  Admission mirrors that
    state at the shared model boundary so the next mixed-provider selection can
    proceed to another provider without re-entering every parked credential.
    Only a response already classified as WorkBuddy/model-scoped is eligible;
    account-shaped 6003/429/403 evidence remains lane-scoped.
    """

    if lane.get("name") not in WORKBUDDY_LANES:
        return None
    if capacity_scope(status, body, None, lane) != "model":
        return None
    text = body.decode("utf-8", errors="ignore")
    current = time.time() if now is None else now
    match = WORKBUDDY_6004_RESET_RE.search(text)
    if match:
        try:
            reset_at = datetime.strptime(
                f"{match.group('date')} {match.group('time')}",
                "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=timezone(timedelta(hours=8)))
            remaining = math.ceil(reset_at.timestamp() - current)
            if 1 <= remaining <= 86400:
                return min(remaining, WORKBUDDY_6004_MAX_COOLDOWN_SECONDS)
        except (TypeError, ValueError, OverflowError):
            pass
    return WORKBUDDY_6004_DEFAULT_COOLDOWN_SECONDS


def claude_retry_after(headers: Any, now: float | None = None) -> int | None:
    """Use the longest explicit provider deadline, never a guessed quota window."""

    current = time.time() if now is None else now
    standard = headers.get("Retry-After")
    # The generic parser preserves legacy clamping for other lanes. A signed
    # delta is not a valid provider deadline for resuming subscription traffic.
    delays = [
        None
        if standard and standard.strip().startswith(("-", "+"))
        else parse_retry_after(standard, current)
    ]
    milliseconds = headers.get("Retry-After-Ms")
    if milliseconds and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", milliseconds.strip()):
        value = float(milliseconds)
        if math.isfinite(value) and value > 0:
            delays.append(math.ceil(value / 1000))
    # Unified quota reset values are UNIX seconds, not durations. Only a
    # rejected window is a deadline; allowed windows may carry future resets.
    for key, value in headers.items():
        name = key.lower()
        if (
            name.startswith("anthropic-ratelimit-unified-")
            and name.endswith("-status")
            and value.strip().lower() == "rejected"
        ):
            reset = headers.get(name.removesuffix("-status") + "-reset", "")
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", reset.strip()):
                deadline = float(reset)
                if math.isfinite(deadline) and deadline > current:
                    delays.append(math.ceil(deadline - current))
    return max((delay for delay in delays if delay is not None), default=None)


def edge_denial_body(body: bytes) -> bool:
    """Report a 403 page that carries no structured account evidence.

    Edge/WAF denials arrive as an HTML or plain-text page. They describe the
    transport hop rather than the credential, so they must not latch a durable
    operator hold on an otherwise healthy lane (2026-10-31: one "Access denied"
    page parked workbuddy-oauth until manual release).
    """

    stripped = body.lstrip()
    if not stripped:
        return False
    if stripped.startswith(b"<"):
        return True
    try:
        json.loads(stripped)
    except (ValueError, UnicodeDecodeError):
        return True
    return False


def oauth_risk_signal(
    lane_name: str | None, status: int, body: bytes, known_retry: bool = False
) -> tuple[str | None, int | None]:
    # CPA access middleware rejects gateway client keys before contacting any
    # provider. Its exact string envelope is not an OAuth account signal.
    if status == 401:
        try:
            client_error = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            client_error = None
        if client_error in (
            {"error": "Invalid API key"},
            {"error": "Missing API key"},
        ):
            return None, None
    if lane_name == "antigravity-oauth":
        # An edge/WAF 403 page is transport evidence, not account evidence.
        # The same rule already existed for chatgpt/workbuddy (2026-10-31: one
        # Cloudflare "Access denied" page parked workbuddy-oauth until manual
        # release), but the antigravity dispatch sits ABOVE that guard and
        # returned "authentication_required" for every non-JSON 403 body.
        # Antigravity is reached through Google's edge, so an HTML/plain 403
        # there is the likeliest form of a blocked hop; latching it deleted a
        # healthy Google AI Pro lane until an operator looked.
        if status == 403 and edge_denial_body(body):
            return None, None
        return antigravity_risk_signal(status, body)
    # A successful HTTP status can carry a failed JSON/SSE operation. Explicit
    # billing/quota denial needs operator action, not periodic account probes;
    # a guessed Retry-After must not make that denial automatically recoverable.
    if lane_name in DURABLE_OAUTH_LANES:
        for error in protocol_errors(body, lane_name):
            if not isinstance(error, dict):
                continue
            details = error.get("details")
            codes = (
                error.get("code"),
                details.get("error_code") if isinstance(details, dict) else None,
            )
            if any(
                code
                in (
                    "insufficient_quota",
                    "billing_hard_limit_reached",
                    "enforced_spend_limit_reached",
                )
                for code in codes
            ):
                return "retry_window_review_required", None
    if lane_name in {"chatgpt-oauth", *WORKBUDDY_LANES}:
        errors = protocol_errors(body, lane_name)
        if status == 403 and not errors and edge_denial_body(body):
            # A blocked edge hop is transient: cool through the normal ladder
            # instead of parking the lane until an operator releases it.
            return None, None
        # A bare 401/403 status no longer latches by itself: mid-path and
        # fallback-leg rejections arrive with this status too (the
        # 2026-11-11 overnight latch was a fallback leg's JSON 403), and the
        # official WorkBuddy docs read the 6xxx family as recoverable
        # frequency limits. Durable auth holds require structured account
        # evidence below; a genuinely revoked credential re-triggers with
        # that evidence and latches then.
        hold = None
        if lane_name in WORKBUDDY_LANES:
            try:
                envelope = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                envelope = None
            if isinstance(envelope, dict) and envelope.get("code") == 11140:
                return "account_restricted", None
        for error in errors:
            if not isinstance(error, dict):
                continue
            code = error.get("code")
            # Upstream 6008 is the "Credits exhausted" refusal of the UTC+8
            # daily free-credit allowance (daily_credits_cap=300 rhythm; the
            # 2026-11-11 census showed CN balances intact). It self-heals at
            # the UTC+8 day boundary, so it rides the capacity ladder beside
            # the 6004 family instead of latching a durable operator hold:
            # the 2026-11-13 "all 14 credentials dead, purchase add-on"
            # verdict came from exactly that latch and was falsified on
            # 2026-11-14 by a CN-credential single-shot 200 right after the
            # boundary, with zero account actions in between.
            upstream_code = quoted_upstream_code(error.get("message"))
            if lane_name in WORKBUDDY_LANES and (code == 6008 or upstream_code == 6008):
                return None, None
            # The generic WorkBuddy rate-limit family (bare 429/6003 envelopes
            # and `rate_limit_error` wrappers without quoted account codes)
            # no longer latches the operator-review hold. 2026-11-07 (bwg): a
            # single transient 429 blip parked the whole lane behind
            # operator_review_required while a direct upstream single-shot
            # probe succeeded minutes later; frequency-family windows are
            # transient by the provider's own semantics (6004/14003 already
            # cool without holds), so they now ride the same capacity ladder,
            # which self-expires and re-arms while upstream keeps declining.
            # Durable account states keep their dedicated signals: 6008
            # credits above, 11140/auth/restriction below, so an exhausted
            # credential still surfaces for operator action.
            if code in ("account_deactivated", "account_suspended", "account_disabled"):
                hold = "account_restricted"
            elif error.get("type") == "authentication_error":
                hold = hold or "authentication_required"
            # A standing restriction is the provider saying the account is
            # gone, not "slow down". It outranks the recoverable shapes that
            # pass through this lane (6xxx frequency windows, bare 401/403
            # fallback-leg rejections): letting it ride the capacity ladder
            # both hides the real state from the operator and keeps spending
            # requests on a suspended account. Both the pre-existing markers
            # and the shared restriction list apply, so the ChatGPT lane
            # cannot silently fall behind WorkBuddy in what it recognizes.
            message = str(error.get("message", "")).lower()
            if any(marker in message for marker in STANDING_RESTRICTION_MARKERS):
                hold = "account_restricted"
            if lane_name in WORKBUDDY_LANES and code == 11140:
                hold = "account_restricted"
        return hold, None
    if lane_name != "claude-oauth":
        return None, None
    hold = "authentication_required" if status in (401, 403) else None
    for error in protocol_errors(body):
        if not isinstance(error, dict):
            continue
        kind = error.get("type")
        if not known_retry and (kind == "rate_limit_error" or error.get("code") == 429):
            hold = hold or "retry_window_review_required"
        if kind in ("authentication_error", "permission_error") or error.get(
            "code"
        ) in (401, 403):
            hold = hold or "authentication_required"
        message = str(error.get("message", "")).lower()
        if any(
            marker in message
            for marker in (
                "only authorized for use with claude code",
                "account has been disabled",
                "account has been suspended",
                "account is disabled",
                "account is suspended",
                "account suspended",
                "account disabled",
                "account_on_hold",
            )
        ):
            hold = "account_restricted"
    return hold, None


def antigravity_risk_signal(status: int, body: bytes) -> tuple[str | None, int | None]:
    """Read error envelopes only; generated text must never trip this guard."""

    hold = "authentication_required" if status in (401, 403) else None
    delay: int | None = None
    for error in protocol_errors(body):
        if not isinstance(error, dict):
            continue
        if error.get("code") in (401, 403):
            hold = hold or "authentication_required"
        # 2026-11-06/07 wave: Antigravity 403 denials carry
        # status=PERMISSION_DENIED plus reason=TOS_VIOLATION (or
        # SERVICE_DISABLED) in the ErrorInfo detail, and need not say anything
        # about a suspension in free text. Matching only prose therefore
        # classified a TOS revocation as the weaker authentication_required,
        # which is indistinguishable from an expired token; an operator could
        # only tell the difference by re-reading the response dump. Read the
        # error status word and the detail reason as well, and keep the prose
        # markers as the fallback for the shapes the provider spells out.
        status_word = str(error.get("status", "")).upper()
        if status_word in ("PERMISSION_DENIED", "UNAUTHENTICATED"):
            hold = hold or "authentication_required"
        text = " ".join(
            str(error.get(field, "")) for field in ("message", "reason")
        ).lower()
        if any(
            marker in text
            for marker in (
                "account suspended",
                "account has been suspended",
                "service has been disabled",
                "service has been disable",
                "violation of terms",
                "violating the terms",
                "tos_violation",
                "terms of service violation",
            )
        ):
            hold = "account_restricted"
        # Some transports flatten ErrorInfo onto the error object itself
        # (reason: TOS_VIOLATION) instead of nesting it under details; the
        # 2026-11-06/07 appeals all carried the reason somewhere in that shape.
        # Classify it the same way whether nested or flat, so a revocation is
        # never downgraded to the weaker auth hold.
        if str(error.get("reason", "")).upper() in STANDING_RESTRICTION_REASONS:
            hold = "account_restricted"
        details = error.get("details")
        if isinstance(details, dict):
            # A single detail object has been observed unwrapped; normalise it
            # into the list walk instead of dropping the evidence.
            details = [details]
        for detail in details if isinstance(details, list) else []:
            if not isinstance(detail, dict):
                continue
            kind = str(detail.get("@type", "") or "")
            value = None
            if kind.endswith("google.rpc.RetryInfo"):
                value = detail.get("retryDelay")
            elif kind.endswith("google.rpc.ErrorInfo"):
                # TOS_VIOLATION is what the 2026-11-06/07 enforcement wave
                # returns: the account is not merely unauthenticated, its Cloud
                # Code access was revoked. Keep an explicit list so an unknown
                # future reason falls back to the conservative auth hold rather
                # than silently becoming an "appeal" instruction.
                if str(detail.get("reason", "")).upper() in (
                    STANDING_RESTRICTION_REASONS
                ):
                    hold = "account_restricted"
                metadata = detail.get("metadata")
                if isinstance(metadata, dict):
                    value = metadata.get("quotaResetDelay")
            if value is None and kind == "":
                # Google clients do not always echo @type. A duration-shaped
                # retryDelay under a retry-shaped key is explicit provider
                # evidence, which is exactly what the lane's strict backoff
                # exists to honour, so accept it instead of parsing the 429 as
                # "no hint" and letting the local ladder outrun the reset.
                value = detail.get("retryDelay")
                metadata = detail.get("metadata")
                if not value and isinstance(metadata, dict):
                    value = metadata.get("quotaResetDelay")
            # RetryInfo uses protobuf Duration; ErrorInfo also uses Go-style
            # h/m/s durations in the pinned CPA source. No free-text guessing.
            if (
                not isinstance(value, str)
                or not re.fullmatch(
                    r"(?:(?:\d+(?:\.\d+)?)h)?(?:(?:\d+(?:\.\d+)?)m)?"
                    r"(?:(?:\d+(?:\.\d+)?)s)?",
                    value,
                )
                or not value
            ):
                continue
            seconds = sum(
                float(number) * {"h": 3600, "m": 60, "s": 1}[unit]
                for number, unit in re.findall(r"(\d+(?:\.\d+)?)([hms])", value)
            )
            if math.isfinite(seconds) and seconds > 0:
                delay = max(delay or 0, math.ceil(seconds))
    return hold, delay


def antigravity_bare_resource_exhausted(status: int, body: bytes) -> bool:
    """Identify the bare Cloud Code 429 that carries no usable reset scope."""
    if status != 429:
        return False
    payload = parsed_json_body(body)
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    return (
        isinstance(error, dict)
        and str(error.get("status", "")).upper() == "RESOURCE_EXHAUSTED"
        and not error.get("details")
        and not error.get("retryDelay")
    )


def antigravity_credential_switch_429(status: int, body: bytes) -> bool:
    """Identify CPA's local single-credential switch 429 on Antigravity.

    When one Antigravity credential is in short cooldown or reaches its local
    daily cap, CPA returns a local 429 ("auth in short cooldown, ... remaining"
    or "antigravity daily credential cap reached, ... seconds remaining") with
    the single credential's Retry-After so conductor cools that credential and
    selects a sibling on the next attempt. Because max-retry-credentials=1 is
    enforced, CPA returns that single-credential 429 to admission. It is a
    credential-rotation signal, not model-wide or lane-wide capacity
    exhaustion and not a rejected payload, so admission must neither park the
    model across healthy sibling credentials nor count it toward repeat-reject.
    """
    if status != 429:
        return False
    payload = parsed_json_body(body)
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    message = str(error.get("message", "")).strip().lower()
    return (
        message.startswith("auth in short cooldown,") and message.endswith("remaining")
    ) or (
        message.startswith("antigravity daily credential cap reached,")
        and message.endswith("seconds remaining")
    )


# A bare Antigravity RESOURCE_EXHAUSTED carries no usable scope, so the 429
# flows straight back to the caller and retry timing belongs to the client.
# Resubmitting the same rejected payload is the documented escalation path to a
# credential-level TOS_VIOLATION (#7562/#7636/#7768; live 2026-11-22: five
# resubmissions of one ~342 KiB turn across 85 minutes ended in the serving
# account's service disablement). Admission therefore counts bare-429 arms per
# client request id and, past the threshold, pauses that exact request id
# locally before any upstream contact. Other request ids and sibling models on
# the lane are untouched, and the pause is in-memory only so a restart always
# clears it.
ANTIGRAVITY_REPEAT_REJECT_ARM_THRESHOLD = 2
ANTIGRAVITY_REPEAT_REJECT_ARM_WINDOW_SECONDS = 1800
ANTIGRAVITY_REPEAT_REJECT_COOLDOWN_SECONDS = 1800
ANTIGRAVITY_REPEAT_REJECT_MAX_TRACKED = 512

_repeat_reject_arms: dict[str, deque[float]] = {}
_repeat_reject_pauses: dict[str, float] = {}
_repeat_reject_lock = threading.Lock()


def reset_antigravity_repeat_reject_state() -> None:
    """Clear all repeat-reject arms and pauses (drill and test entry point)."""
    with _repeat_reject_lock:
        _repeat_reject_arms.clear()
        _repeat_reject_pauses.clear()


def _prune_repeat_reject_state(now: float) -> None:
    for key in [
        key
        for key, arms in _repeat_reject_arms.items()
        if not arms or arms[-1] <= now - ANTIGRAVITY_REPEAT_REJECT_ARM_WINDOW_SECONDS
    ]:
        _repeat_reject_arms.pop(key, None)
    for key in [
        key for key, not_before in _repeat_reject_pauses.items() if not_before <= now
    ]:
        _repeat_reject_pauses.pop(key, None)
    while len(_repeat_reject_arms) > ANTIGRAVITY_REPEAT_REJECT_MAX_TRACKED:
        _repeat_reject_arms.pop(next(iter(_repeat_reject_arms)))
    while len(_repeat_reject_pauses) > ANTIGRAVITY_REPEAT_REJECT_MAX_TRACKED:
        _repeat_reject_pauses.pop(next(iter(_repeat_reject_pauses)))


def antigravity_repeat_reject_remaining(
    client_hash: str | None,
    now: float | None = None,
    *,
    body: bytes | None = None,
) -> int | None:
    """Return seconds left on a request's repeat-reject pause, if armed."""
    keys = antigravity_repeat_reject_keys(client_hash, body=body)
    if not keys:
        return None
    current = time.monotonic() if now is None else now
    max_remaining: int | None = None
    with _repeat_reject_lock:
        for key in keys:
            not_before = _repeat_reject_pauses.get(key)
            if not_before is None:
                continue
            remaining = int(not_before - current)
            if remaining <= 0:
                _repeat_reject_pauses.pop(key, None)
                continue
            if max_remaining is None or remaining > max_remaining:
                max_remaining = remaining
    return max_remaining


def antigravity_repeat_reject_keys(
    client_hash: str | None, *, body: bytes | None = None
) -> tuple[str, ...]:
    """Build repeat-reject tracking key while ignoring placeholder hashes.

    `diagnostic_hash(None)` returns `"-"` for log readability. Treating `"-"`
    as a real key would pool every headerless request into one shared bucket;
    when an explicit request-id hash is present use it directly, and otherwise
    fall back to the redacted payload digest so headerless retries of the same
    rejected body still pause without cross-contaminating unrelated requests.
    """
    if client_hash and client_hash != "-":
        return (client_hash,)
    if body:
        body_digest = hashlib.sha256(body).hexdigest()[:12]
        return (f"body:{body_digest}",)
    return ()


def record_antigravity_repeat_reject_arm(
    client_hash: str | None,
    model: str | None,
    now: float | None = None,
    *,
    body: bytes | None = None,
) -> None:
    """Count a bare-429 arm for one client request and pause unchanged repeats."""
    keys = antigravity_repeat_reject_keys(client_hash, body=body)
    if not keys:
        return
    now = time.monotonic() if now is None else now
    armed_keys: list[str] = []
    with _repeat_reject_lock:
        window_start = now - ANTIGRAVITY_REPEAT_REJECT_ARM_WINDOW_SECONDS
        for key in keys:
            arms = _repeat_reject_arms.setdefault(key, deque())
            while arms and arms[0] <= window_start:
                arms.popleft()
            arms.append(now)
            if len(arms) >= ANTIGRAVITY_REPEAT_REJECT_ARM_THRESHOLD:
                _repeat_reject_pauses[key] = (
                    now + ANTIGRAVITY_REPEAT_REJECT_COOLDOWN_SECONDS
                )
                _repeat_reject_arms.pop(key, None)
                armed_keys.append(key)
        _prune_repeat_reject_state(now)
    for key in armed_keys:
        logging.info(
            "repeat_reject_pause_armed hash=%s model=%s threshold=%s window_s=%s "
            "cooldown_s=%s",
            key,
            model or "other",
            ANTIGRAVITY_REPEAT_REJECT_ARM_THRESHOLD,
            ANTIGRAVITY_REPEAT_REJECT_ARM_WINDOW_SECONDS,
            ANTIGRAVITY_REPEAT_REJECT_COOLDOWN_SECONDS,
        )


ANTIGRAVITY_QUOTA_GROUPS = (
    "gemini",
    # The GEMINI 4 ARGON weekly bucket rolled out separately from the shared
    # GEMINI pool (same-account panels diverged at one instant, 2026-11-21
    # 09:42:16: GEMINI 83% vs ARGON 32%), so argon gets its own family key
    # and an argon verdict never cools the gemini pool or vice versa.
    "argon",
    "claude",
    "gpt",
    "glm",
    "deepseek",
)


def antigravity_quota_group(model: str | None) -> str | None:
    name = routing_model(model or "").lower()
    if name.startswith("gemini-4-argon"):
        return "argon"
    if name.startswith("gemini-"):
        return "gemini"
    if name.startswith("claude-"):
        return "claude"
    if name.startswith(("gpt-", "gpt_")):
        return "gpt"
    if name.startswith("glm-"):
        return "glm"
    if name.startswith("deepseek-"):
        return "deepseek"
    return None


def antigravity_quota_scope_key(model: str | None) -> str | None:
    """Return the narrowest durable key supported by the observed signal."""
    name = routing_model(model or "").strip().lower()
    if not name or antigravity_quota_group(name) is None:
        return None
    return f"model:{name}"


def antigravity_envelope(body: bytes) -> dict[str, Any] | None:
    """Return the parts of a verified Cloud Code error envelope.

    Field data: the quota and capacity verdicts Google sends on this lane
    carry exactly one error object (HTTP code 429, or the 503 UNAVAILABLE
    twin pinned by the upstream credits-fallback suite) with exactly one
    google.rpc ErrorInfo detail on the cloudcode-pa domain.
    """
    errors = protocol_errors(body)
    if len(errors) != 1 or not isinstance(errors[0], dict):
        return None
    error = errors[0]
    if error.get("code") not in (429, 503):
        return None
    details = error.get("details")
    if not isinstance(details, list):
        return None
    error_info = [
        detail
        for detail in details
        if isinstance(detail, dict)
        and detail.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo"
    ]
    if len(error_info) != 1:
        return None
    info = error_info[0]
    domain = info.get("domain")
    if domain not in (None, "cloudcode-pa.googleapis.com"):
        return None
    reason = info.get("reason")
    if not isinstance(reason, str) or not reason:
        return None
    return {"error": error, "info": info, "reason": reason}


def antigravity_envelope_scope(body: bytes, model: str | None) -> str | None:
    """Map a verified Cloud Code envelope onto the quota-key taxonomy.

    "quota_group": a model-capacity reason (upstream #7884) whose body
    carries a reset delay arms the durable per-model key via
    antigravity_quota_scope_key, so no sibling ever shares the cooldown;
    without a reset it defers to the caller's in-memory model window. The
    per-account bucket verdict (QUOTA_EXHAUSTED with the "Individual quota
    reached" message) also defers there since the 2026-11-24 live firing:
    the bucket belongs to one credential, CPA core cools that credential
    per-model with its own kv-persisted quota cooldown, and a durable
    lane-wide key only froze healthy siblings for the whole advertised
    reset (argon parked 400920s while two Pro accounts still had argon
    headroom). "account": the per-user rate limiter reasons
    (RATE_LIMIT_EXCEEDED / USER_RATE_LIMIT_EXCEEDED) are explicitly
    account-level signals, so they keep the shared lane breaker and are
    exempt from the lane-to-model narrowing in release(). None: anything
    else, including a body that is not a Cloud Code envelope at all.
    """
    parsed = antigravity_envelope(body)
    if parsed is None:
        return None
    error: dict[str, Any] = parsed["error"]
    info: dict[str, Any] = parsed["info"]
    reason: str = parsed["reason"]
    metadata = info.get("metadata")
    named = metadata.get("model") if isinstance(metadata, dict) else None
    message = str(error.get("message", ""))
    requested_group = antigravity_quota_group(model)
    if reason == "QUOTA_EXHAUSTED":
        # One credential's family bucket, not the lane's. The envelope may
        # name a same-family sibling (the bucket is shared), but per-account
        # scoping is core's job now: defer unconditionally to the
        # model-first default so the mirror-cadence window keeps one bounded
        # probe per recheck slot instead of freezing the model for days.
        return None
    if reason in ("MODEL_CAPACITY_EXHAUSTED", "CAPACITY_EXHAUSTED"):
        if requested_group is None:
            return None
        if not isinstance(named, str) or not named:
            marker = "no capacity available for model "
            lowered = message.lower()
            named = (
                lowered.split(marker, 1)[1].strip().strip("\"'.")
                if marker in lowered
                else ""
            )
        if named and str(named) != routing_model(model or ""):
            return None
        # Only arm the durable per-model key when the envelope itself
        # advertises a reset the key can carry; otherwise defer to the
        # caller's in-memory model window.
        retry_info = [
            detail
            for detail in (error.get("details") or [])
            if isinstance(detail, dict)
            and detail.get("@type") == "type.googleapis.com/google.rpc.RetryInfo"
        ]
        quota_reset = (
            isinstance(metadata, dict) and metadata.get("quotaResetDelay")
        ) or retry_info
        return "quota_group" if quota_reset else None
    if reason in ("RATE_LIMIT_EXCEEDED", "USER_RATE_LIMIT_EXCEEDED"):
        return "account"
    return None


def capacity_scope(
    status: int,
    body_prefix: bytes,
    retry_after: str | None,
    config: dict[str, Any],
) -> str | None:
    """Classify a capacity response as model-scoped, lane-scoped, or not one.

    A marker that names the *model* ("Selected model is at capacity") is a
    statement about that model only: the same credential served its siblings
    seconds earlier, and promoting it to the whole lane turned one model's
    capacity window into a cross-model 429 (measured 2026-10-10: a
    `gpt-6-luna` capacity event refused the next `gpt-6.1-sol` request with
    `retry_after=471`). Credential-shaped signals -- `server_is_overloaded`,
    the rate/usage-limit texts, or a bare 429/503 -- stay lane-scoped, so the
    shared account is still protected where the signal is account-level.
    """

    errors = protocol_errors(body_prefix, config.get("name"))
    if config.get("name") in WORKBUDDY_LANES:
        codes = {
            code
            for error in errors
            if isinstance(error, dict)
            for code in (error.get("code"), quoted_upstream_code(error.get("message")))
            if isinstance(code, (int, str))
        }
        # WorkBuddy 6004 says explicitly that only the requested model is
        # throttled. Keep 6003 lane-scoped because its scope is not proven.
        if codes.intersection({6003, 401, 403, 429, 11140}):
            return "lane"
        if 6004 in codes and all(
            isinstance(error, dict)
            and (
                error.get("code") == 6004
                or quoted_upstream_code(error.get("message")) == 6004
            )
            and error.get("type") not in ("authentication_error", "permission_error")
            for error in errors
        ):
            return "model"
    error_text = json.dumps(errors, ensure_ascii=False).lower()
    # protocol_errors only unwraps the CPA `upstream <status>: {json}` prose
    # for the WorkBuddy lane and only for integer provider codes, so a
    # chatgpt-oauth prose wrapper (string "code":"model_not_found") produces
    # no error entry at all and the dump above is empty. Scan the raw body
    # alongside the dump so every wrapper shape reaches the exemptions below.
    raw_text = error_text + body_prefix.decode("utf-8", errors="replace").lower()
    if config.get("name") == "antigravity-oauth" and any(
        marker in raw_text for marker in GEMINI_CAPACITY_MARKERS
    ):
        # RESOURCE_EXHAUSTED is Google's own quota verdict and appears in no
        # other marker list, so it must reach the ladder before the routing/
        # auth exemptions below can interpret a 400-shaped wrapper as "not
        # capacity" and forward it.
        return "lane"
    # A response that carries both a model-specific marker and a
    # credential/account marker must stay lane-scoped.  Narrowing it to the
    # model would let sibling requests continue against a shared account while
    # the upstream is explicitly reporting account-level overload.
    if any(marker in error_text for marker in config["capacity_markers"]):
        model_markers = config.get("model_capacity_markers") or ()
        if not any(marker in error_text for marker in model_markers):
            return "lane"
        if any(
            marker in error_text
            for marker in config["capacity_markers"]
            if marker not in model_markers
        ):
            return "lane"
        return "model"
    if any(
        marker in error_text for marker in config.get("model_capacity_markers") or ()
    ):
        return "model"
    # Known routing/auth failures do not establish shared capacity exhaustion.
    # The bare-name alternates catch the CPA `upstream <status>: {json}`
    # wrapper, whose embedded body lands quote-escaped inside the extracted
    # error dump (or not extracted at all on the chatgpt lane, where
    # protocol_errors does not unwrap): the 2026-11-13 chatgpt-oauth half-day
    # parks rode exactly this gap (a synthesized 503 + huge Retry-After
    # around a 400 whose model_not_found code the bare-quoted check could no
    # longer see).
    if any(
        code in raw_text
        for code in (
            '"model_not_found"',
            "model_not_found",
            "not supported when using codex",
            '"invalid_api_key"',
            '"authentication_error"',
        )
    ):
        return None
    return "lane" if status in config["capacity_statuses"] else None


def _generic_capacity_scope(
    status: int, body_prefix: bytes, retry_after: str | None, config: dict[str, Any]
) -> str | None:
    """Judge capacity the way the lane would if its models were not pooled.

    The pooled narrowing must only apply to a signal that already qualified on
    the generic path; dropping `pooled_models` is exactly that question, so the
    request path asks it once here instead of duplicating the marker lists.
    """

    return capacity_scope(
        status, body_prefix, retry_after, {**config, "pooled_models": ()}
    )


def is_capacity_response(
    status: int,
    body_prefix: bytes,
    retry_after: str | None,
    config: dict[str, Any],
) -> bool:
    return capacity_scope(status, body_prefix, retry_after, config) is not None


# Token accounting is OBSERVATION ONLY. Only these numeric leaf keys are ever
# read, so a usage line can never carry prompt or completion content, and no
# admission decision depends on them. Live traffic previously had no token or
# cache telemetry at all (CPA keeps usage in memory; only error dumps land on
# disk), which made "cache hit rate" answerable only by a canary.
_USAGE_TOTAL_INPUT_KEYS = ("total_input_tokens",)
_USAGE_INPUT_KEYS = ("input_tokens", "prompt_tokens", "promptTokenCount")
_USAGE_OUTPUT_KEYS = (
    "output_tokens",
    "completion_tokens",
    "candidatesTokenCount",
    "total_output_tokens",
)
_USAGE_CACHED_PATHS: tuple[tuple[str, ...], ...] = (
    ("input_tokens_details", "cached_tokens"),
    ("prompt_tokens_details", "cached_tokens"),
    ("cachedContentTokenCount",),
    ("total_cached_tokens",),
    ("cached_tokens",),
    ("cache_read_input_tokens",),
    ("cache_read_tokens",),
)
_USAGE_DISJOINT_CACHE_READ_PATHS: tuple[tuple[str, ...], ...] = (
    ("cache_read_input_tokens",),
    ("cache_read_tokens",),
)
_USAGE_DISJOINT_CACHE_WRITE_PATHS: tuple[tuple[str, ...], ...] = (
    ("cache_creation_input_tokens",),
    ("cache_creation_tokens",),
    ("cache_write_tokens",),
)


def _usage_int(container: dict[str, Any], path: tuple[str, ...]) -> int | None:
    node: Any = container
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    # bool is an int subclass; a boolean must never be read as a token count.
    return node if type(node) is int and node >= 0 else None


def _first_usage_int(
    usage: dict[str, Any], paths: tuple[tuple[str, ...], ...]
) -> int | None:
    return next(
        (
            value
            for value in (_usage_int(usage, path) for path in paths)
            if value is not None
        ),
        None,
    )


def usage_numbers(usage: Any) -> tuple[int | None, int | None, int | None]:
    """Return normalized (total input, cached read, output) token counts.

    OpenAI-style responses report total input with a cached subset. Anthropic
    Messages and some WorkBuddy/plugin responses instead report uncached input,
    cache reads, and cache writes as disjoint fields. The latter must be added
    to the denominator or a large cache hit can be reported as nearly 100% and
    a large cache write can disappear from telemetry entirely.
    """
    if not isinstance(usage, dict):
        return (None, None, None)
    tokens_input = _first_usage_int(
        usage, tuple((key,) for key in _USAGE_TOTAL_INPUT_KEYS)
    )
    if tokens_input is None:
        tokens_input = _first_usage_int(
            usage, tuple((key,) for key in _USAGE_INPUT_KEYS)
        )
        cache_read = _first_usage_int(usage, _USAGE_DISJOINT_CACHE_READ_PATHS)
        cache_write = _first_usage_int(usage, _USAGE_DISJOINT_CACHE_WRITE_PATHS)
        if cache_read is not None or cache_write is not None:
            tokens_input = (tokens_input or 0) + (cache_read or 0) + (cache_write or 0)
    tokens_output = _first_usage_int(usage, tuple((key,) for key in _USAGE_OUTPUT_KEYS))
    tokens_cached = _first_usage_int(usage, _USAGE_CACHED_PATHS)
    return (tokens_input, tokens_cached, tokens_output)


def _event_usage(event: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    """Find usage in the shapes CPA relays: top-level, response.*, message.*, Gemini."""
    for candidate in (
        event.get("usage"),
        (event.get("response") or {}).get("usage")
        if isinstance(event.get("response"), dict)
        else None,
        (event.get("message") or {}).get("usage")
        if isinstance(event.get("message"), dict)
        else None,
        event.get("usageMetadata"),
        (event.get("response") or {}).get("usage_metadata")
        if isinstance(event.get("response"), dict)
        else None,
        (event.get("interaction") or {}).get("usage")
        if isinstance(event.get("interaction"), dict)
        else None,
        (event.get("metadata") or {}).get("total_usage")
        if isinstance(event.get("metadata"), dict)
        else None,
        (event.get("metadata") or {}).get("usage")
        if isinstance(event.get("metadata"), dict)
        else None,
    ):
        found = usage_numbers(candidate)
        if found != (None, None, None):
            return found
    return (None, None, None)


def usage_from_body(payload: bytes) -> tuple[int | None, int | None, int | None]:
    """Usage from a non-stream JSON body; malformed or truncated bodies read as absent."""
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (None, None, None)
    if not isinstance(data, dict):
        return (None, None, None)
    return _event_usage(data)


def usage_cost(usage: Any) -> float | None:
    """Read a provider-reported USD cost without treating token counts as money."""

    if not isinstance(usage, dict):
        return None
    for value in (
        usage.get("cost"),
        usage.get("total_cost"),
        usage.get("cost_usd"),
        usage.get("total_cost_usd"),
    ):
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            return float(value)
    return None


def _event_usage_cost(event: dict[str, Any]) -> float | None:
    for candidate in (
        event.get("usage"),
        (event.get("response") or {}).get("usage")
        if isinstance(event.get("response"), dict)
        else None,
        (event.get("message") or {}).get("usage")
        if isinstance(event.get("message"), dict)
        else None,
        event.get("usageMetadata"),
        (event.get("interaction") or {}).get("usage")
        if isinstance(event.get("interaction"), dict)
        else None,
        (event.get("metadata") or {}).get("total_usage")
        if isinstance(event.get("metadata"), dict)
        else None,
    ):
        found = usage_cost(candidate)
        if found is not None:
            return found
    return None


# Event names are deliberately allowlisted: upstream event types are not
# trusted logging data and may contain user/provider-controlled strings.
SAFE_SSE_EVENT_TYPES = frozenset(
    {
        "response.created",
        "response.queued",
        "response.in_progress",
        "response.output_item.added",
        "response.output_item.done",
        "response.content_part.added",
        "response.content_part.done",
        "response.output_text.delta",
        "response.output_text.done",
        "response.reasoning_summary_text.delta",
        "response.reasoning_summary_text.done",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.refusal.delta",
        "response.refusal.done",
        "response.completed",
        "response.failed",
        "response.incomplete",
        "response.cancelled",
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
        "ping",
        "interaction.completed",
        "interaction.done",
        "interaction.failed",
        "interaction.cancelled",
    }
)


def completed_response_text_state(response: Any) -> str:
    """Classify a completed Responses object without retaining its content.

    The terminal object contains the bounded output shape even when the
    separate text-done event is unavailable to the probe. Only the presence or
    emptiness of output_text parts is observed; text, ids, URLs, and tool
    arguments are discarded before logging.
    """

    if not isinstance(response, dict):
        return "unknown"
    output = response.get("output")
    if not isinstance(output, list):
        return "unknown"
    text_parts = [
        part
        for item in output
        if isinstance(item, dict) and item.get("type") == "message"
        for part in (
            item.get("content") if isinstance(item.get("content"), list) else []
        )
        if isinstance(part, dict) and part.get("type") == "output_text"
    ]
    # Visible text wins outright: an answer was delivered, whatever else the
    # turn did. Only a text-less completion falls through to the shapes below.
    if any(
        isinstance(part.get("text"), str) and part["text"].strip()
        for part in text_parts
    ):
        return "text"
    if text_parts and all(
        isinstance(part.get("text"), str) and not part["text"].strip()
        for part in text_parts
    ):
        return "empty"
    # 2026-11-08: a Codex-style agentic turn ends on a function_call item and
    # carries no visible text. The classifier reported reasoning_only, which is
    # a different and misleading shape -- 1444 of one 24h window's
    # chatgpt-oauth results carried that label, and the real mix was tool turns,
    # reasoning turns and answers. A tool turn is a normal step, not a defect.
    if any(
        isinstance(item, dict)
        and item.get("type")
        in {"function_call", "custom_tool_call", "computer_call", "web_search_call"}
        for item in output
    ):
        return "tool_call"
    return "reasoning_only"


# Cache and first-text telemetry (2026-11-08). Every rolling-window aggregate
# below is counts only: no prompt, answer, identifier or client address is
# retained, and no admission decision reads them. They exist because the two
# questions users actually ask -- "is the gateway actually hitting the provider
# prompt cache?" and "is the first visible token slow?" -- could previously be
# answered only from `journalctl` after the fact, per lane, never per model.
# Windows are rolling and the series count is bounded, so an unhealthy gateway
# cannot grow them without bound.
_TELEMETRY_WINDOW_SECONDS = 3600.0
_TELEMETRY_SERIES_LIMIT = 64


class ModelTelemetry:
    """Bounded rolling-window cache-hit and first-text samples per model."""

    def __init__(self, window_seconds: float = _TELEMETRY_WINDOW_SECONDS) -> None:
        self.window_seconds = window_seconds
        # model -> [[timestamp, usage_in, usage_cached, usage_out, first_text_ms|None]]
        self._samples: dict[str, list[list[float | int | None]]] = {}
        self._lock = threading.Lock()
        self.dropped_series = 0

    def record(
        self,
        model: str,
        usage_in: int | None,
        usage_cached: int | None,
        usage_out: int | None,
        first_text_ms: int | None,
        result: str | None = None,
    ) -> None:
        # Only completed generations feed the cache ratio: a capacity refusal or
        # a truncated stream reports whatever partial usage CPA relayed, and
        # averaging that into "hit rate" would make an outage look like a cache
        # regression. First-text samples stay unfiltered -- they are the very
        # measurement of an interrupted first token.
        completed = result == "completed"
        if not completed:
            usage_in = usage_cached = usage_out = None
        if (
            usage_in is None
            and usage_cached is None
            and usage_out is None
            and first_text_ms is None
        ):
            return
        now = time.monotonic()
        with self._lock:
            series = self._samples.get(model)
            if series is None:
                if len(self._samples) >= _TELEMETRY_SERIES_LIMIT:
                    # Never evict a live series to admit a new one: a model
                    # removed from the catalog must not resurrect stale stats,
                    # and the cap is what keeps memory bounded.
                    self.dropped_series += 1
                    return
                series = []
                self._samples[model] = series
            series.append([now, usage_in, usage_cached, usage_out, first_text_ms])
            horizon = now - self.window_seconds
            while series and series[0][0] < horizon:  # type: ignore[operator]
                series.pop(0)

    @staticmethod
    def _quantile(values: list[int], fraction: float) -> int:
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            horizon = now - self.window_seconds
            models: dict[str, dict[str, Any]] = {}
            for model, series in self._samples.items():
                live = [item for item in series if item[0] >= horizon]  # type: ignore[operator]
                # Keep the pruned list so the structure shrinks with the window.
                series[:] = live
                counted = [
                    item for item in live if item[1] is not None or item[2] is not None
                ]
                cached_total = sum(int(item[2] or 0) for item in counted)
                # Providers disagree on whether the reported input count already
                # includes cache reads. OpenAI/Antigravity/GLM report TOTAL input
                # plus a cached subset, so uncached = input - cached. WorkBuddy's
                # DeepSeek shape reports UNCACHED input with cache reads apart,
                # which is unambiguous when cached > input (the same discriminator
                # the natural-traffic audit uses). Summing input and cached for
                # every provider (the pre-2026-11-08 behaviour) halved the ratio
                # for the total-input shape: a measured 96.8% OpenClaw cache hit
                # read as 49.2% in /healthz, so the operator-facing number was
                # wrong by 2x and pointed at the wrong tier.
                uncached_total = sum(
                    int(item[1] or 0)
                    if int(item[2] or 0) > int(item[1] or 0)
                    else max(0, int(item[1] or 0) - int(item[2] or 0))
                    for item in counted
                )
                first_text = [
                    int(item[4])
                    for item in live
                    if isinstance(item[4], int) and not isinstance(item[4], bool)
                ]
                entry: dict[str, Any] = {"samples": len(live)}
                if counted:
                    denominator = cached_total + uncached_total
                    entry.update(
                        {
                            "usage_samples": len(counted),
                            "cached_tokens": cached_total,
                            "uncached_input_tokens": uncached_total,
                            # None, not 0: a provider that never reports cache
                            # numbers must read as unknown, not as a 0% hit rate.
                            # Numerator is provider-reported cached tokens;
                            # denominator is every prompt token, so this is the
                            # provider ratio, not a local estimate.
                            "cache_hit_ratio": round(cached_total / denominator, 4)
                            if denominator
                            else None,
                        }
                    )
                if first_text:
                    entry["first_text_ms"] = {
                        "n": len(first_text),
                        "p50": self._quantile(first_text, 0.5),
                        "p90": self._quantile(first_text, 0.9),
                        "max": max(first_text),
                    }
                models[model] = entry
            return {
                "window_seconds": int(self.window_seconds),
                "series_limit": _TELEMETRY_SERIES_LIMIT,
                "dropped_series_total": self.dropped_series,
                "models": models,
            }


class StreamErrorProbe:
    """Inspect every SSE event without retaining generated output."""

    def __init__(
        self,
        limit: int,
        lane: dict[str, Any] | None,
        status: int = 200,
        path: str = "",
        retry_after: int | None = None,
        started_at: float | None = None,
    ) -> None:
        self.limit = limit
        self.lane = lane
        self.status = status
        self.buffer = bytearray()
        self.data = bytearray()
        self.discard_line = False
        self.discard_event = False
        self.has_error = False
        self.malformed_event = False
        self.complete = True
        self.terminal_success = False
        self.bytes_received = 0
        self.event_count = 0
        self.last_event_type = "none"
        # Bounded protocol metadata only: the final visible text finish reason.
        # Do not retain answer content or generated identifiers/URLs.
        self.output_finish_reason = "na"
        # Relative to the upstream request start.  This is deliberately a
        # timing-only field: no generated text is retained or logged.
        self.started_at = started_at
        self.first_text_ms: int | None = None
        self.usage_input: int | None = None
        self.usage_cached: int | None = None
        self.usage_output: int | None = None
        # Provider-reported spend is not inferred from tokens or price tables.
        self.usage_cost_usd: float | None = None
        self.path = unquote(urlsplit(path).path).rstrip("/").lower()
        self.scope: str | None = None
        self.hold_reason: str | None = None
        self.retry_after: int | None = None
        # A model-scoped WorkBuddy 6004 deadline must not be confused with a
        # lane-level durable retry signal.  Keep it separate until release()
        # records the model breaker.
        self.model_retry_after: int | None = None
        self.known_retry = retry_after is not None

    def feed(self, chunk: bytes) -> None:
        self.bytes_received += len(chunk)
        for index, part in enumerate(chunk.split(b"\n")):
            if index:
                self._line()
            if not self.discard_line:
                if len(self.buffer) + len(part) > self.limit:
                    self.buffer.clear()
                    self.discard_line = True
                    self.discard_event = True
                    self.complete = False
                else:
                    self.buffer.extend(part)

    def _line(self) -> None:
        line = bytes(self.buffer).rstrip(b"\r")
        self.buffer.clear()
        if self.discard_line:
            self.discard_line = False
            return
        if not line:
            self._event()
        elif line == b"data" or line.startswith(b"data:"):
            value = line[5:]
            if value.startswith(b" "):
                value = value[1:]
            if len(self.data) + len(value) + 1 > self.limit:
                self.data.clear()
                self.discard_event = True
                self.complete = False
            elif not self.discard_event:
                self.data.extend(value + b"\n")

    def _event(self, allow_terminal: bool = True) -> None:
        if self.data or self.discard_event:
            self.event_count += 1
            self.last_event_type = self._safe_event_type(
                bytes(self.data).strip() if not self.discard_event else b""
            )
        if not self.discard_event and self.data:
            payload = bytes(self.data)
            self._observe_output_text(payload.strip())
            lane_name = self.lane.get("name") if self.lane is not None else None
            if protocol_errors(payload, lane_name):
                self.has_error = True
                if self.lane is not None:
                    if lane_name in DURABLE_OAUTH_LANES:
                        hold, delay = oauth_risk_signal(
                            lane_name, self.status, payload, self.known_retry
                        )
                        self.hold_reason = self.hold_reason or hold
                        if delay is not None:
                            self.retry_after = max(self.retry_after or 0, delay)
                    scope = capacity_scope(self.status, payload, None, self.lane)
                    if scope == "lane" or self.scope is None:
                        self.scope = scope
                    model_retry = workbuddy_6004_retry_after(
                        self.status, payload, self.lane
                    )
                    if model_retry is not None:
                        self.model_retry_after = max(
                            self.model_retry_after or 0, model_retry
                        )
            self._terminal(payload.strip(), allow_terminal)
        self.data.clear()
        self.discard_event = False

    def _safe_event_type(self, payload: bytes) -> str:
        if payload == b"[DONE]":
            return "done"
        try:
            event = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "other"
        if not isinstance(event, dict):
            return "other"
        kind = event.get("type") or event.get("event_type")
        if isinstance(kind, str) and kind in SAFE_SSE_EVENT_TYPES:
            return kind
        candidates = event.get("candidates")
        if isinstance(candidates, list) and candidates:
            if all(
                isinstance(candidate, dict) and bool(candidate.get("finishReason"))
                for candidate in candidates
            ):
                return "gemini.finished"
            return "gemini.event"
        return "other"

    def _observe_output_text(self, payload: bytes) -> None:
        """Retain the final user-visible text finish reason, without text.

        CPA's `response.completed` event does not distinguish an answer from a
        reasoning-only completion. A text delta is conclusive; a text-part
        done event is only conclusive when non-empty. Chat completion deltas
        expose their reason directly. Values leave an allowlist before logging.
        """

        try:
            event = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type") or event.get("event_type")
        if kind == "response.created":
            self.output_finish_reason = "na"
            return
        forced_reason: str | None = None
        observed_text = False
        terminated_text = False
        choices = event.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                message = choice.get("message")
                visible = (
                    isinstance(delta, dict)
                    and isinstance(delta.get("content"), str)
                    and bool(delta.get("content"))
                ) or (
                    isinstance(message, dict)
                    and isinstance(message.get("content"), str)
                    and bool(message.get("content"))
                )
                visible = visible or (
                    isinstance(choice.get("text"), str) and bool(choice.get("text"))
                )
                if visible:
                    self._mark_first_text()
                observed_text = observed_text or visible
                reason = choice.get("finish_reason")
                if visible and isinstance(reason, str) and reason.strip():
                    forced_reason = reason.strip()
                if visible and reason in (
                    "stop",
                    "length",
                    "max_tokens",
                    "max_output_tokens",
                    "content_filter",
                ):
                    terminated_text = True
        if forced_reason is not None:
            if forced_reason in {
                "stop",
                "length",
                "max_tokens",
                "max_output_tokens",
                "end_turn",
                "tool_use",
                "tool_calls",
                "function_call",
                "content_filter",
                "STOP",
                "MAX_TOKENS",
                "SAFETY",
                "RECITATION",
                "OTHER",
            }:
                self.output_finish_reason = forced_reason
            return
        if observed_text:
            self.output_finish_reason = "pending"
            return
        if kind == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                self._mark_first_text()
                self.output_finish_reason = "pending"
            return
        if kind == "response.output_text.done":
            # The terminal response carries the authoritative bounded output
            # shape. Do not infer emptiness from an intermediate done frame.
            return
        if kind == "content_block_delta":
            delta = event.get("delta")
            if (
                isinstance(delta, dict)
                and delta.get("type") == "text_delta"
                and isinstance(delta.get("text"), str)
                and bool(delta.get("text"))
            ):
                self._mark_first_text()
                self.output_finish_reason = "pending"
            return
        if kind == "response.completed":
            response = event.get("response")
            visible = completed_response_text_state(response)
            if visible == "text" or self.output_finish_reason == "pending":
                self.output_finish_reason = "stop"
            elif visible == "empty":
                self.output_finish_reason = "empty_text"
            elif visible == "reasoning_only":
                self.output_finish_reason = "reasoning_only"
            elif visible == "tool_call":
                self.output_finish_reason = "tool_call"
            # A completed event that omits the output shape is not proof of a
            # reasoning-only turn; leave it "na" instead of manufacturing one.
            elif self.output_finish_reason == "pending":
                self.output_finish_reason = "stop"
            return
        elif kind == "response.incomplete":
            response = event.get("response")
            details = (
                response.get("incomplete_details")
                if isinstance(response, dict)
                else None
            )
            reason = details.get("reason") if isinstance(details, dict) else None
            if reason == "max_output_tokens":
                self.output_finish_reason = "max_output_tokens"
            elif reason in {"content_filter", "safety"}:
                self.output_finish_reason = "content_filter"
            elif terminated_text:
                self.output_finish_reason = "stop"
        elif kind == "message_delta":
            delta = event.get("delta")
            reason = delta.get("stop_reason") if isinstance(delta, dict) else None
            if isinstance(reason, str) and reason.strip():
                self.output_finish_reason = reason.strip()
            elif self.output_finish_reason == "pending":
                self.output_finish_reason = "stop"
        elif self.path.endswith(":streamgeneratecontent"):
            candidates = event.get("candidates")
            if isinstance(candidates, list):
                for candidate in candidates:
                    content = (
                        candidate.get("content")
                        if isinstance(candidate, dict)
                        else None
                    )
                    parts = content.get("parts") if isinstance(content, dict) else None
                    if isinstance(parts, list) and any(
                        isinstance(part, dict)
                        and isinstance(part.get("text"), str)
                        and bool(part.get("text"))
                        for part in parts
                    ):
                        self._mark_first_text()
                        break
                reasons = [
                    candidate.get("finishReason")
                    for candidate in candidates
                    if isinstance(candidate, dict)
                    and isinstance(candidate.get("finishReason"), str)
                ]
                if reasons:
                    self.output_finish_reason = reasons[-1]
        if self.output_finish_reason == "pending" and terminated_text:
            self.output_finish_reason = "stop"

    def _mark_first_text(self) -> None:
        if self.first_text_ms is None and self.started_at is not None:
            self.first_text_ms = max(
                0, int((time.monotonic() - self.started_at) * 1000)
            )

    def _terminal(self, payload: bytes, allow_terminal: bool) -> None:
        # A clean EOF is transport evidence only. Recovery needs the terminal
        # event belonging to the requested protocol, never generated text.
        chat = self.path.endswith("/completions")
        if payload == b"[DONE]":
            if chat and allow_terminal:
                self.terminal_success = True
            return
        try:
            event = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.malformed_event = True
            return
        if not isinstance(event, dict):
            return
        # Merge usage before the terminal/allow_terminal gates so a last chunk
        # carrying only counts is still accounted even on an incomplete stream.
        tokens_input, tokens_cached, tokens_output = _event_usage(event)
        if tokens_input is not None:
            self.usage_input = tokens_input
        if tokens_cached is not None:
            self.usage_cached = tokens_cached
        if tokens_output is not None:
            self.usage_output = tokens_output
        cost = _event_usage_cost(event)
        if cost is not None:
            self.usage_cost_usd = cost
        kind = event.get("type")
        if (
            kind in ("response.failed", "response.incomplete")
            or event.get("event_type")
            in ("interaction.failed", "interaction.cancelled")
            or ("error" in event and event["error"] is not None)
        ):
            self.has_error = True
        if not allow_terminal:
            return
        if self.path.endswith("/responses"):
            response = event.get("response")
            terminal = (
                kind == "response.completed"
                and isinstance(response, dict)
                and response.get("status") == "completed"
                and not response.get("error")
            )
        elif chat:
            # CPA emits the stream terminator only after finish_reason; a lone
            # choice finish cannot prove that all requested choices finished.
            terminal = False
        elif self.path.endswith("/messages"):
            terminal = kind == "message_stop"
        elif self.path.endswith(":streamgeneratecontent"):
            candidates = event.get("candidates")
            terminal = (
                bool(candidates)
                and isinstance(candidates, list)
                and all(
                    isinstance(candidate, dict) and bool(candidate.get("finishReason"))
                    for candidate in candidates
                )
            )
        elif self.path.endswith("/interactions"):
            interaction = event.get("interaction")
            event_name = event.get("event_type") or event.get("type")
            terminal = (
                event_name in ("interaction.completed", "interaction.done")
                and isinstance(interaction, dict)
                and interaction.get("status") == "completed"
            )
        else:
            terminal = False
        self.terminal_success = self.terminal_success or terminal

    def finish(self) -> None:
        # Also inspect a final error whose peer closed without a blank line.
        if self.buffer or self.discard_line:
            self._line()
        self._event(allow_terminal=False)


def protocol_result_reason(
    *,
    status: int,
    successful: bool,
    capacity_error: bool,
    client_lost: bool,
    stream_probe: StreamErrorProbe | None,
    body_prefix: bytes,
    lane_name: str | None,
) -> str:
    """Return a bounded, body-free reason for an upstream result log line.

    ``successful=false`` previously required correlating several fields and
    could not tell a missing protocol terminator from an upstream error. The
    reason is intentionally an enum-like value: it never includes provider
    text, request content, credentials, or exception details.
    """

    if successful:
        return "completed"
    if client_lost:
        return "client_disconnect"
    if capacity_error:
        return "capacity"
    if stream_probe is not None:
        if stream_probe.has_error:
            return "upstream_error"
        if stream_probe.malformed_event:
            return "malformed_event"
        if not stream_probe.complete:
            return "inspection_limit"
        if not stream_probe.terminal_success:
            return "missing_terminal"
        return "protocol_error"
    if protocol_errors(body_prefix, lane_name):
        return "upstream_error"
    if not (200 <= status < 300):
        return "http_error"
    return "protocol_error"


_PARSE_FAILED = object()

# One entry per server thread: the guards run sequentially on the request
# thread, so a single-slot memo cannot be read concurrently by two requests.
_body_parse_state = threading.local()


def parsed_json_body(body: bytes) -> Any:
    """Parse the request body once and share the result across guards.

    requested_model, requested_stream, the agent-message normalizer and the
    encrypted/image guards each used to json.loads the full body on their
    own -- six parses of one 1-3 MB Codex payload per turn, all on the
    request thread (measured 2026-11-10: 1.09-2.78 MB bodies cost
    ~100-280 ms of pure CPU per turn). Every guard parses the same bytes,
    so the parse is memoized per thread keyed by the body itself: bytes are
    immutable, a mutated-and-reserialized body (agent-message normalize)
    is a different key and misses, and the next request body evicts the
    entry. Parse failures are memoized via the sentinel so every consumer
    keeps its exact historical (UnicodeDecodeError, JSONDecodeError)
    mapping by checking for the sentinel.
    """
    state = getattr(_body_parse_state, "entry", None)
    if state is not None and state[0] == body:
        return state[1]
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = _PARSE_FAILED
    _body_parse_state.entry = (body, payload)
    return payload


def requested_model(path: str, body: bytes) -> str | None:
    """Return the model a request names, whether or not a lane owns it.

    `requested_lane` answers "which shared lane must gate this request", so it
    returns None for every model outside the three lanes and for any body it
    cannot parse. Both cases used to collapse into the journal's `model=other`,
    which hides the one field a burst of pass-through 5xx needs to be attributed
    -- which model the client was actually asking for.
    """

    route = unquote(urlsplit(path).path).rstrip("/").lower()
    native = re.fullmatch(
        r"/v1(?:beta)?/models/(.+):(?:generatecontent|streamgeneratecontent|counttokens)",
        route,
    )
    if native:
        return native.group(1)
    if not route.endswith(
        (
            "/completions",
            "/responses",
            "/responses/compact",
            "/messages",
            "/messages/count_tokens",
            "/images/generations",
            "/audio/speech",
            "/interactions",
        )
    ):
        return None
    if not body:
        return None
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED:
        return None
    model = payload.get("model") if isinstance(payload, dict) else None
    if not isinstance(model, str) or not model.strip():
        return None
    model = model.strip().lower()
    return model.removeprefix("models/") if route.endswith("/interactions") else model


def requested_stream(path: str, body: bytes) -> bool:
    """Return whether a request asks CPA for an SSE response.

    Admission may only take ownership of response headers for streaming
    requests. Non-stream callers must keep receiving the real HTTP status and
    JSON error body, because they have no SSE terminal event to carry it.
    """

    route = unquote(urlsplit(path).path).rstrip("/").lower()
    if re.fullmatch(
        r"/v1(?:beta)?/models/.+:streamgeneratecontent",
        route,
    ):
        # Native Gemini encodes streaming in the method suffix, not a body
        # field. Without this branch, a queued native stream waited silently
        # behind a shared OAuth lane even though its response is SSE.
        return True
    if route not in {
        "/v1/responses",
        "/v1/chat/completions",
        "/v1/completions",
        "/v1/messages",
        "/v1/interactions",
        "/v1beta/interactions",
    }:
        return False
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED:
        return False
    return isinstance(payload, dict) and payload.get("stream") is True


def routing_model(model: str) -> str:
    """Use CPA's base model identity without changing the forwarded request."""

    opening = model.rfind("(")
    return model[:opening].strip() if opening >= 0 and model.endswith(")") else model


def normalize_cross_provider_agent_messages(
    path: str, body: bytes
) -> tuple[bytes, bool]:
    """Map Codex's inter-agent carrier to standard Responses user text.

    Multi-Agent v2 puts the task text in ``agent_message`` content as an
    ``encrypted_content`` field. Third-party Responses endpoints do not
    implement that OpenAI-specific item. The field carries the task message
    here; preserving its text and NEW_TASK/MESSAGE header as an ordinary user
    message lets the selected provider receive the delegated instruction.
    Only the narrow, known Codex envelope is converted. OpenAI routes and
    malformed/unknown content retain their original bytes for the fail-closed
    guard below.
    """
    if not urlsplit(path).path.rstrip("/").endswith("/responses"):
        return body, False
    model = requested_model(path, body)
    if model is None or not routing_model(model).startswith(
        ("claude-", "deepseek-", "glm-", "gemini-")
    ):
        return body, False
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED:
        return body, False
    items = payload.get("input") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return body, False
    normalized = False
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        encrypted = [
            part
            for part in content
            if isinstance(part, dict) and part.get("type") == "encrypted_content"
        ]
        if not encrypted:
            continue
        author = item.get("author")
        recipient = item.get("recipient")
        if not isinstance(author, str) or not isinstance(recipient, str):
            return body, False
        # Refuse to reinterpret an unfamiliar content shape. A current Codex
        # task message is exactly a text header followed by its task string.
        if (
            len(content) != 2
            or len(encrypted) != 1
            or not isinstance(content[0], dict)
            or content[0].get("type") != "input_text"
            or not isinstance(content[0].get("text"), str)
            or not isinstance(encrypted[0].get("encrypted_content"), str)
            or not encrypted[0]["encrypted_content"]
            or content[0]["text"]
            not in (
                f"Message Type: NEW_TASK\nTask name: {recipient}\n"
                f"Sender: {author}\nPayload:\n",
                f"Message Type: MESSAGE\nTask name: {recipient}\n"
                f"Sender: {author}\nPayload:\n",
            )
        ):
            return body, False
        items[index] = {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": content[0]["text"]},
                {"type": "input_text", "text": encrypted[0]["encrypted_content"]},
            ],
        }
        normalized = True
    if not normalized:
        return body, False
    out = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    _body_parse_state.entry = (out, payload)
    return out, True


def _model_switch_developer_text(item: Any) -> str | None:
    """Return stripped text when *item* is a developer text-only message."""
    if not isinstance(item, dict):
        return None
    if item.get("role") != "developer":
        return None
    if item.get("type") not in (None, "message"):
        return None
    content = item.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list) or not content:
        return None
    chunks: list[str] = []
    for part in content:
        if (
            not isinstance(part, dict)
            or part.get("type") != "input_text"
            or not isinstance(part.get("text"), str)
        ):
            return None
        chunks.append(part["text"])
    return "".join(chunks).strip()


_ANTIGRAVITY_STRIPPED_DEVELOPER_TAGS = (
    "model_switch",
    "skills_instructions",
)
_ANTIGRAVITY_DEDUP_DEVELOPER_PART_TAGS = (
    "collaboration_mode",
    "plugins_instructions",
    "codex_apps_client_time_context",
)

_ANTIGRAVITY_RETRY_ONLY_USER_TEXTS = frozenset({"continue", "retry", "resume", "继续"})
_ANTIGRAVITY_HISTORICAL_IMAGE_PLACEHOLDER = (
    "[Historical image omitted after earlier assistant response; "
    "ask the user to re-attach it if visual details are still needed.]"
)
_ANTIGRAVITY_MODEL_RESPONSE_ITEM_TYPES = frozenset(
    {"reasoning", "function_call", "custom_tool_call"}
)


def omit_antigravity_historical_user_images(
    path: str, body: bytes, lane_name: str | None
) -> tuple[bytes, int]:
    """Replace historical user images already answered by earlier model turns.

    Images in the latest unanswered user turn(s) remain intact so the model
    always sees newly attached screenshots. Earlier user images that precede
    an assistant/model/reasoning/tool-call item are replaced with a concise
    text placeholder on Antigravity Responses requests to avoid resending
    large base64 screenshots on every subsequent turn. Images nested inside
    tool results are left untouched.
    """
    if lane_name != "antigravity-oauth":
        return body, 0
    if not urlsplit(path).path.rstrip("/").endswith("/responses"):
        return body, 0
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED or not isinstance(payload, dict):
        return body, 0
    items = payload.get("input")
    if not isinstance(items, list):
        return body, 0

    last_model_response_index: int | None = None
    for index, item in enumerate(items):
        if isinstance(item, dict) and (
            item.get("role") in {"assistant", "model"}
            or item.get("type") in _ANTIGRAVITY_MODEL_RESPONSE_ITEM_TYPES
        ):
            last_model_response_index = index
    if last_model_response_index is None:
        return body, 0

    omitted = 0
    for item in items[:last_model_response_index]:
        if (
            not isinstance(item, dict)
            or item.get("role") != "user"
            or item.get("type", "message") != "message"
        ):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        new_content = []
        item_changed = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "input_image":
                new_content.append(
                    {
                        "type": "input_text",
                        "text": _ANTIGRAVITY_HISTORICAL_IMAGE_PLACEHOLDER,
                    }
                )
                omitted += 1
                item_changed = True
            else:
                new_content.append(part)
        if item_changed:
            item["content"] = new_content

    if not omitted:
        return body, 0
    out = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _body_parse_state.entry = (out, payload)
    return out, omitted


def collapse_antigravity_retry_user_turns(
    path: str, body: bytes, lane_name: str | None
) -> tuple[bytes, int]:
    """Remove duplicate retry-only turns from old sessions.

    A failed Codex turn can remain as a user item even when no assistant/tool
    item was produced. Repeating the same retry action then grows the input
    with duplicate short user turns, and later failures can continue that
    pattern after earlier turns have produced history. Exact retry-only user
    items are therefore collapsed independently of surrounding history, while
    images, tool results, and arbitrary user text pass through. The latest
    retry-only turn for each retry text is retained.
    """
    _ = lane_name
    if not urlsplit(path).path.rstrip("/").endswith("/responses"):
        return body, 0
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED or not isinstance(payload, dict):
        return body, 0
    items = payload.get("input")
    if not isinstance(items, list):
        return body, 0

    retry_indices: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list) and content:
            if any(
                not isinstance(part, dict)
                or part.get("type") != "input_text"
                or not isinstance(part.get("text"), str)
                for part in content
            ):
                continue
            text = "".join(part["text"] for part in content).strip()
        else:
            continue
        normalized = text.casefold()
        if normalized in _ANTIGRAVITY_RETRY_ONLY_USER_TEXTS:
            retry_indices.setdefault(normalized, []).append(index)

    duplicate_indices = {
        index
        for indices in retry_indices.values()
        if len(indices) > 1
        for index in indices[:-1]
    }
    if not duplicate_indices:
        return body, 0

    payload["input"] = [
        item for index, item in enumerate(items) if index not in duplicate_indices
    ]
    out = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _body_parse_state.entry = (out, payload)
    return out, len(duplicate_indices)


def strip_antigravity_model_switch_items(
    path: str, body: bytes, lane_name: str | None
) -> tuple[bytes, dict[str, int]]:
    """Strip Codex developer notice items on /responses routes.

    When Codex Desktop switches models mid-session, it injects a ~20 KiB
    ``role="developer"`` message wrapped in ``<model_switch>...</model_switch>``
    (duplicating the initial system prompt), and on settings/turn re-inits a
    standalone ``<skills_instructions>...</skills_instructions>`` block
    (~34 KiB), retaining both permanently in ``input``. CLIProxyAPI folds
    mid-conversation developer items into ``contents`` user turns for
    Antigravity, which causes Google's Cloud Code gateway to reject
    subsequent requests across all models and accounts with a bare 429
    ``RESOURCE_EXHAUSTED``. 2026-11-22 live evidence showed the notices can
    also accumulate before the first assistant turn -- aborted turns produce
    no assistant output, so every later notice stays in the prompt prefix and
    still trips the fingerprint. The strip therefore matches whole-item tags
    at any position: the session-start skills bundle is multi-part (its
    joined text starts with ``<app-context>``) and travels in
    systemInstruction, the initial system prompt and
    ``<collaboration_mode>`` notices never start with a stripped tag, and
    user-quoted or non-``developer`` text never matches.
    """
    _ = lane_name
    if not urlsplit(path).path.rstrip("/").endswith("/responses"):
        return body, {}
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED or not isinstance(payload, dict):
        return body, {}
    items = payload.get("input")
    if not isinstance(items, list):
        return body, {}
    filtered: list[Any] = []
    removed: dict[str, int] = {}
    for item in items:
        text = _model_switch_developer_text(item)
        matched: str | None = None
        if text is not None:
            for tag in _ANTIGRAVITY_STRIPPED_DEVELOPER_TAGS:
                if text.startswith(f"<{tag}>") and text.endswith(f"</{tag}>"):
                    matched = tag
                    break
        if matched is None:
            filtered.append(item)
        else:
            removed[matched] = removed.get(matched, 0) + 1
    if not removed:
        return body, {}
    payload["input"] = filtered
    out = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _body_parse_state.entry = (out, payload)
    return out, removed


def _wrapped_xml_tag(text: str, tags: tuple[str, ...]) -> str | None:
    """Return the matching tag name when stripped *text* is wrapped in ``<tag>...</tag>``."""
    stripped = text.strip()
    for tag in tags:
        if stripped.startswith(f"<{tag}>") and stripped.endswith(f"</{tag}>"):
            return tag
    return None


def _is_developer_or_system_message(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.get("role") in {"developer", "system"}
        and item.get("type") in (None, "message")
    )


def _is_displaced_app_context_bundle(item: Any) -> bool:
    if not (
        isinstance(item, dict)
        and item.get("role") == "developer"
        and item.get("type") in (None, "message")
    ):
        return False
    content = item.get("content")
    if not isinstance(content, list) or not content:
        return False
    first = content[0]
    return (
        isinstance(first, dict)
        and first.get("type") == "input_text"
        and isinstance(first.get("text"), str)
        and _wrapped_xml_tag(first["text"], ("app-context",)) == "app-context"
    )


def normalize_antigravity_developer_context(
    path: str, body: bytes, lane_name: str | None
) -> tuple[bytes, dict[str, int]]:
    """Keep displaced session-start context in systemInstruction and trim mid-session duplicates.

    In warm-forked or compacted Codex Desktop sessions, early compacted user
    messages can precede the ``<app-context>`` developer bundle, causing
    CLIProxyAPI to emit that ~52 KiB bundle as a mid-conversation ``contents``
    user turn instead of ``systemInstruction``. Later turns can also inject
    multi-part ``developer`` items combining ``<skills_instructions>`` with
    ``<collaboration_mode>`` / ``<plugins_instructions>``, plus repeated
    ``<collaboration_mode>`` and ``<codex_apps_client_time_context>`` notices.
    On ``/responses`` requests across all lanes, this helper:
      1. Promotes displaced ``<app-context>`` developer bundles into the
         leading developer prefix (zero information loss, routed to
         ``systemInstruction``).
      2. Strips mid-conversation ``<skills_instructions>`` parts even inside
         multi-part developer items.
      3. Deduplicates mid-conversation ``<collaboration_mode>``,
         ``<plugins_instructions>``, and ``<codex_apps_client_time_context>``
         parts, dropping copies identical to the leading prefix and keeping
         only the latest changed copy when state actually changed.
    """
    _ = lane_name
    if not urlsplit(path).path.rstrip("/").endswith("/responses"):
        return body, {}
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED or not isinstance(payload, dict):
        return body, {}
    items = payload.get("input")
    if not isinstance(items, list) or not items:
        return body, {}

    first_conv_index: int | None = None
    for idx, item in enumerate(items):
        if not _is_developer_or_system_message(item):
            first_conv_index = idx
            break
    if first_conv_index is None:
        return body, {}

    promoted_bundles = 0
    promoted: list[Any] = []
    rest: list[Any] = []
    for item in items[first_conv_index:]:
        if _is_displaced_app_context_bundle(item):
            promoted.append(item)
            promoted_bundles += 1
        else:
            rest.append(item)
    if promoted:
        items = [*items[:first_conv_index], *promoted, *rest]
        first_conv_index += len(promoted)

    prefix_tag_texts: dict[str, set[str]] = {}
    for item in items[:first_conv_index]:
        if not (isinstance(item, dict) and item.get("role") == "developer"):
            continue
        content = item.get("content")
        if isinstance(content, str):
            tag = _wrapped_xml_tag(content, _ANTIGRAVITY_DEDUP_DEVELOPER_PART_TAGS)
            if tag is not None:
                prefix_tag_texts.setdefault(tag, set()).add(content.strip())
        elif isinstance(content, list):
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "input_text"
                    and isinstance(part.get("text"), str)
                ):
                    tag = _wrapped_xml_tag(
                        part["text"], _ANTIGRAVITY_DEDUP_DEVELOPER_PART_TAGS
                    )
                    if tag is not None:
                        prefix_tag_texts.setdefault(tag, set()).add(
                            part["text"].strip()
                        )

    stripped_skills_parts = 0
    deduped_parts = 0
    seen_tags: set[str] = set()
    kept_tail_rev: list[Any] = []

    for item in reversed(items[first_conv_index:]):
        if not (
            isinstance(item, dict)
            and item.get("role") == "developer"
            and item.get("type") in (None, "message")
        ):
            kept_tail_rev.append(item)
            continue
        content = item.get("content")
        if isinstance(content, str):
            if _wrapped_xml_tag(content, ("skills_instructions",)) is not None:
                stripped_skills_parts += 1
                continue
            tag = _wrapped_xml_tag(content, _ANTIGRAVITY_DEDUP_DEVELOPER_PART_TAGS)
            if tag is not None:
                norm = content.strip()
                if tag in seen_tags or norm in prefix_tag_texts.get(tag, ()):
                    deduped_parts += 1
                    seen_tags.add(tag)
                    continue
                seen_tags.add(tag)
            kept_tail_rev.append(item)
            continue
        if not isinstance(content, list):
            kept_tail_rev.append(item)
            continue

        kept_parts_rev: list[Any] = []
        item_changed = False
        for part in reversed(content):
            if (
                isinstance(part, dict)
                and part.get("type") == "input_text"
                and isinstance(part.get("text"), str)
            ):
                text = part["text"]
                if _wrapped_xml_tag(text, ("skills_instructions",)) is not None:
                    stripped_skills_parts += 1
                    item_changed = True
                    continue
                tag = _wrapped_xml_tag(text, _ANTIGRAVITY_DEDUP_DEVELOPER_PART_TAGS)
                if tag is not None:
                    norm = text.strip()
                    if tag in seen_tags or norm in prefix_tag_texts.get(tag, ()):
                        deduped_parts += 1
                        seen_tags.add(tag)
                        item_changed = True
                        continue
                    seen_tags.add(tag)
            kept_parts_rev.append(part)

        if not kept_parts_rev:
            continue
        if item_changed:
            item = dict(item)
            item["content"] = list(reversed(kept_parts_rev))
        kept_tail_rev.append(item)

    stats: dict[str, int] = {}
    if promoted_bundles:
        stats["promoted_bundles"] = promoted_bundles
    if stripped_skills_parts:
        stats["stripped_skills_parts"] = stripped_skills_parts
    if deduped_parts:
        stats["deduped_parts"] = deduped_parts
    if not stats:
        return body, {}

    payload["input"] = [*items[:first_conv_index], *reversed(kept_tail_rev)]
    out = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _body_parse_state.entry = (out, payload)
    return out, stats


def normalize_antigravity_image_detail(
    path: str, body: bytes, lane_name: str | None
) -> tuple[bytes, int]:
    """Map stale Codex ``original`` image detail to Antigravity ``high``.

    Codex sessions can retain image parts created before the model catalog
    stopped advertising ``supports_image_detail_original`` for non-Codex
    routes.  Preserve the image and the request history, while using the
    Responses detail value accepted by the Antigravity translation path.
    Only direct user message content is normalized.  Images nested in tool
    results are provider output and must remain untouched.
    """
    if lane_name != "antigravity-oauth":
        return body, 0
    if not urlsplit(path).path.rstrip("/").endswith("/responses"):
        return body, 0
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED or not isinstance(payload, dict):
        return body, 0
    items = payload.get("input")
    if not isinstance(items, list):
        return body, 0

    changed = 0
    for item in items:
        if (
            not isinstance(item, dict)
            or item.get("role") != "user"
            or item.get("type", "message") != "message"
        ):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "input_image"
                and isinstance(part.get("detail"), str)
                and part["detail"].strip().lower() == "original"
            ):
                part["detail"] = "high"
                changed += 1
    if not changed:
        return body, 0
    out = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _body_parse_state.entry = (out, payload)
    return out, changed


# Upstream content filters key on the sentence with which a coding agent
# introduces itself. Antigravity returns the bare 429 for it in the system
# instruction and in user text (CLIProxyAPI #7553; the 2026-11-18 controlled
# probe; the 2026-11-22 forked session whose compaction handoff summary quoted
# "You are Codex, an agent based on GPT-5"). WorkBuddy and the ZCode start plan
# serve official-client plans through third-party code, so naming another agent
# in the prompt only discloses the client. CLIProxyAPI cleans systemInstruction
# for Antigravity only, and by whole-word replacement; this pass rewrites the
# identity sentence alone, so paths such as `.zcode/skills` and ordinary
# mentions of a product keep their bytes. Bare product tokens in user text have
# passed through every accepted session, so they are left alone here. The
# ChatGPT lane is excluded: Codex's identity sentence is the official client's
# own shape there.
AGENT_IDENTITY_SENTENCES = (
    (
        re.compile(
            r"\byou are claude code, anthropic['\u2019]s official cli for claude\b\.?",
            re.IGNORECASE,
        ),
        "You are a coding assistant.",
    ),
    (
        # DeepSeek Harness names itself by product phrase, not in one fixed
        # sentence (#7553: three occurrences in its developer prompt drew the
        # 429; "Assistant Framework" cleared them).
        re.compile(r"\bdeepseek harness\b", re.IGNORECASE),
        "Assistant Framework",
    ),
    (
        re.compile(
            r"\byou are (?:an? )?(?:zcode|codebuddy|workbuddy)\b", re.IGNORECASE
        ),
        "You are a coding assistant",
    ),
    (
        re.compile(
            r"\byou are (?:an? )?codex\b(?:, (?:an? )?(?:coding |interactive )?agent)?"
            r"(?:,? based on gpt-[0-9]+(?:\.[0-9]+)*)?",
            re.IGNORECASE,
        ),
        "You are a coding agent",
    ),
)
_ANTIGRAVITY_HANDOFF_PREAMBLE = "another language model started to solve this problem"
# Lane -> message roles whose text gets the identity sentence rewritten.
_AGENT_IDENTITY_TEXT_ROLES = {
    "antigravity-oauth": frozenset({"developer", "user"}),
    "workbuddy-oauth": frozenset({"system", "developer", "user"}),
    "workbuddy-intl-oauth": frozenset({"system", "developer", "user"}),
    "zcode-start-plan": frozenset({"system", "developer", "user"}),
}


def _as_matched_case(matched: str, target: str) -> str:
    if matched.islower():
        return target.lower()
    if matched[:1].islower():
        return target[:1].lower() + target[1:]
    return target


def neutralize_agent_identity_sentences(text: str) -> str:
    for pattern, replacement in AGENT_IDENTITY_SENTENCES:
        text = pattern.sub(
            lambda match, target=replacement: _as_matched_case(match.group(0), target),
            text,
        )
    return text


def neutralize_agent_identity_text(
    path: str, body: bytes, lane_name: str | None
) -> tuple[bytes, dict[str, int]]:
    """Rewrite agent identity sentences in the prompt text of listed lanes.

    Responses `input`, chat-completions `messages`, and Anthropic `system` /
    `messages` are walked. Only text in the lane's roles changes, and only the
    identity sentence inside it; tool output, assistant turns, and every other
    lane keep their bytes. A part that does not change is not counted.
    """
    roles = _AGENT_IDENTITY_TEXT_ROLES.get(lane_name or "")
    if not roles:
        return body, {}
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED or not isinstance(payload, dict):
        return body, {}
    stats: dict[str, int] = {}

    def swap(text: str, role: str) -> str:
        replaced = neutralize_agent_identity_sentences(text)
        if replaced != text:
            handoff = role == "user" and text.lstrip().lower().startswith(
                _ANTIGRAVITY_HANDOFF_PREAMBLE
            )
            key = "handoff_parts" if handoff else f"{role}_parts"
            stats[key] = stats.get(key, 0) + 1
        return replaced

    def rewrite_content(content: Any, role: str) -> Any:
        if isinstance(content, str):
            return swap(content, role)
        if isinstance(content, list):
            for part in content:
                if (
                    isinstance(part, dict)
                    and isinstance(part.get("text"), str)
                    and part.get("type") in (None, "text", "input_text")
                ):
                    part["text"] = swap(part["text"], role)
        return content

    def rewrite_message(message: Any) -> None:
        if (
            isinstance(message, dict)
            and message.get("role") in roles
            and message.get("type") in (None, "message")
        ):
            message["content"] = rewrite_content(
                message.get("content"), message["role"]
            )

    route = urlsplit(path).path.rstrip("/")
    if route.endswith("/responses"):
        items = payload.get("input")
        if not isinstance(items, list):
            return body, {}
        for item in items:
            rewrite_message(item)
    elif route.endswith("/chat/completions"):
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return body, {}
        for message in messages:
            rewrite_message(message)
    elif route.endswith("/messages"):
        if "system" in roles:
            payload["system"] = rewrite_content(payload.get("system"), "system")
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return body, {}
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "user":
                message["content"] = rewrite_content(message.get("content"), "user")
    else:
        return body, {}

    if not stats:
        return body, {}
    out = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _body_parse_state.entry = (out, payload)
    return out, stats


def unsupported_encrypted_agent_payload(path: str, body: bytes) -> bool:
    """Reject unrecognized encrypted inter-agent shapes before upstream."""
    if not urlsplit(path).path.rstrip("/").endswith("/responses"):
        return False
    model = requested_model(path, body)
    if model is None or not routing_model(model).startswith(
        ("claude-", "deepseek-", "glm-", "gemini-")
    ):
        return False
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED:
        return False
    items = payload.get("input") if isinstance(payload, dict) else None
    return isinstance(items, list) and any(
        isinstance(item, dict)
        and item.get("type") == "agent_message"
        and isinstance(item.get("content"), list)
        and any(
            isinstance(part, dict)
            and part.get("type") == "encrypted_content"
            and isinstance(part.get("encrypted_content"), str)
            and bool(part["encrypted_content"])
            for part in item["content"]
        )
        for item in items
    )


def unsupported_user_image(
    path: str, body: bytes, text_only_models: frozenset[str]
) -> bool:
    """Reject user images explicitly unsupported by the selected route.

    Tool results retain CPA's existing text-only normalization. Never drop a
    user's image or recursively mistake an image inside a tool result for one.
    """
    route = urlsplit(path).path.rstrip("/")
    if route not in {
        "/v1/responses",
        "/responses",
        "/v1/chat/completions",
        "/chat/completions",
    }:
        return False
    model = requested_model(path, body)
    if model is None or routing_model(model) not in text_only_models:
        return False
    payload = parsed_json_body(body)
    if payload is _PARSE_FAILED:
        return False
    responses = route.endswith("/responses")
    messages = payload.get("input" if responses else "messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        if responses and message.get("type", "message") != "message":
            continue
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(part, dict)
            and part.get("type") == ("input_image" if responses else "image_url")
            for part in content
        ):
            return True
    return False


def requested_lane(
    path: str, body: bytes, config: dict[str, Any]
) -> tuple[str, str] | None:
    model = requested_model(path, body)
    if model is None:
        return None
    # CPA ParseSuffix resolves model(effort/budget) against the base model.
    # Match that routing identity for admission without rewriting the payload.
    model = routing_model(model)
    lane = config["model_lanes"].get(model)
    return (lane, model) if lane is not None else None


def _ceil_remaining_seconds(deadline: float, now: float) -> int:
    """Ceil a monotonic deadline without promoting float noise to a full second."""

    remaining = deadline - now
    nearest_integer = round(remaining)
    float_error = max(math.ulp(deadline), math.ulp(now))
    if abs(remaining - nearest_integer) <= float_error:
        remaining = float(nearest_integer)
    return math.ceil(remaining)


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
    reset_probe: bool = False
    # Lane model this lease was admitted for, so a model-scoped capacity
    # signal can be attributed without re-parsing the request body.
    model: str | None = None
    model_probe: bool = False
    model_generation: int = 0


def _load_risk_state(path: Path) -> tuple[Any, tuple[int, int] | None]:
    """Read the persisted circuit state without following a symlink.

    The previous read was ``path.exists()`` + ``path.read_text()``: both follow
    symlinks, so a swapped link was followed, and a file that vanished between
    the two calls raised FileNotFoundError straight out of startup. Open once
    with O_NOFOLLOW and fstat the descriptor instead -- the identity recorded
    here is the inode that was actually parsed.
    """
    # A missing file is a launcher/bootstrap error, not "no state": the unit
    # always passes a durable path, so silently starting clean would erase an
    # operator hold. The caller maps this to a startup failure.
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except (IsADirectoryError, PermissionError) as exc:
        # Windows reports a directory as PermissionError from os.open; POSIX
        # reports IsADirectoryError. Both mean "not a regular state file".
        raise ValueError("OAuth circuit state must be a regular file") from exc
    except FileNotFoundError:
        # A missing state file stays a FileNotFoundError: the caller reports it
        # as a bootstrap/launcher error, not as an unreadable file.
        raise
    except OSError as exc:
        # The one remaining errno this open is expected to fail with is ELOOP:
        # O_NOFOLLOW turns a symlinked state path into "too many levels of
        # symbolic links". That is the refusal this function exists for, so it
        # must surface as the same ValueError shape as every other rejection
        # instead of a bare OSError the caller has to classify.
        raise ValueError("OAuth circuit state is unreadable") from exc
    try:
        state_stat = os.fstat(descriptor)
        if not stat.S_ISREG(state_stat.st_mode):
            raise ValueError("OAuth circuit state must be a regular file")
        identity = (
            int(getattr(state_stat, "st_dev", 0)),
            int(getattr(state_stat, "st_ino", 0)),
        )
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            return json.loads(stream.read()), identity
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class LaneState:
    """Thread-safe bounded lane with FIFO pending work and recovery probes."""

    def __init__(self, config: dict[str, Any], state_path: Path | None = None) -> None:
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
        # Optional bounded recovery probe for a mirrored capacity cooldown.
        # Zero (the default for lanes that omit the key) keeps the historical
        # blind window: an upstream-advertised Retry-After then parks the lane
        # with no in-window verification at all. A positive interval re-tests
        # recovery one request at a time on that cadence, so a credential- or
        # shed-scoped refusal heals on real demand instead of at the window's
        # end (2026-11-21: one Antigravity 429 carrying Retry-After 17504 froze
        # the shared lane for the whole window while sibling traffic was still
        # being served seconds later). Holds never probe: the operator-review
        # gate in acquire() returns before this branch is reachable.
        self._mirror_probe_interval = float(
            config.get("mirror_probe_interval_seconds") or 0.0
        )
        self._mirror_probe_at = 0.0
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
        self._reset_probe_not_before = 0.0
        self._pending_queue: deque[object] = deque()
        # Per-model cooldowns for capacity signals that name a model. They are
        # deliberately separate from the lane-wide breaker: the shared
        # credential's concurrency budget is still lane-wide (max_inflight),
        # while a model that is out of capacity no longer parks its siblings.
        self._model_open_until: dict[str, float] = {}
        self._model_streak: dict[str, int] = {}
        self._model_probe_at: dict[str, float] = {}
        self._model_server_not_before: dict[str, float] = {}
        self._model_probe_inflight: set[str] = set()
        self._model_failure_generation: dict[str, int] = {}
        # Per-lane request-rate guard (opt-in). `requests_per_minute` bounds the
        # number of normal admissions inside a rolling 60s window. Absent means
        # unlimited (historical behavior), so an older projected JSON cannot
        # change admission semantics. A deque of admission timestamps is pruned
        # lazily on each admission attempt.
        self._rpm_max: int | None = config.get("requests_per_minute")
        self._rpm_window: deque[float] = deque()
        self._lane_name = str(config.get("name") or "")
        self._antigravity_models = (
            tuple(config.get("models") or ())
            if self._lane_name == "antigravity-oauth"
            else ()
        )
        self._quota_group_until: dict[str, float] = {}
        self._saved_quota_groups: dict[str, float] = {}
        self._quota_checks = 0
        # Models this lane serves at x0.00 credits. A billing lock must not gate
        # them; the lane config is the authority so no code change is needed
        # when the provider's free catalog moves.
        self._zero_cost_models = frozenset(config.get("zero_cost_models") or ())
        self._strict_backoff = config.get("name") in STRICT_OAUTH_LANES
        self._durable_risk = config.get("name") in DURABLE_OAUTH_LANES
        self._state_path = state_path
        self._state_identity: tuple[int, int] | None = None
        self._state_error_hold: str | None = None
        self._state_recovery_phase = "clear"
        self._persisted_clear = True
        self._state_first_error: str | None = None
        self._manual_hold: str | None = None
        self._state_write_failed = False
        # A stale write-ahead marker is a bounded startup verification window,
        # not an ordinary cooldown.  Keep it separate so callers receive an
        # immediate Retry-After instead of waiting in the FIFO queue until the
        # window expires.
        self._stale_write_ahead_until = 0.0
        if state_path is not None:
            saved, identity = _load_risk_state(state_path)
            if identity is not None:
                self._state_identity = identity
            state_fields = {"version", "not_before", "hold_reason"}
            if not isinstance(saved, dict):
                raise ValueError(
                    "invalid OAuth circuit state; operator review required"
                )
            group_deadlines = saved.get("quota_group_not_before", {})
            if not isinstance(group_deadlines, dict):
                raise ValueError(
                    "invalid OAuth circuit state; operator review required"
                )
            # The first rollout used the upstream's observed Claude/GPT
            # aggregate. Preserve that deadline while allowing new events to
            # persist the two families independently.
            if "claude-gpt" in group_deadlines:
                legacy_until = group_deadlines.pop("claude-gpt")
                group_deadlines.setdefault("family:claude", legacy_until)
                group_deadlines.setdefault("family:gpt", legacy_until)
            allowed_fields = state_fields | (
                {"quota_group_not_before"} if self._antigravity_models else set()
            )
            if (
                saved.get("version") != 1
                or not state_fields <= saved.keys() <= allowed_fields
                or any(
                    (
                        not (
                            group in ANTIGRAVITY_QUOTA_GROUPS
                            or group.startswith("family:")
                            or group.startswith("model:")
                        )
                    )
                    or type(until) not in (int, float)
                    or not math.isfinite(until)
                    or until < 0
                    for group, until in group_deadlines.items()
                )
                or type(saved.get("not_before")) not in (int, float)
                or not math.isfinite(saved["not_before"])
                or saved["not_before"] < 0
                or saved.get("hold_reason")
                not in (
                    None,
                    "authentication_required",
                    "account_restricted",
                    "account_credits_exhausted",
                    "state_write_failed",
                    "retry_window_review_required",
                    "unverified_request",
                )
            ):
                raise ValueError(
                    "invalid OAuth circuit state; operator review required"
                )
            self._manual_hold = saved["hold_reason"]
            wall_now, monotonic_now = time.time(), time.monotonic()
            self._quota_group_until = {
                group: monotonic_now + (until - wall_now)
                for group, until in group_deadlines.items()
            }
            self._saved_quota_groups = self._quota_group_until.copy()
            remaining = max(0, saved["not_before"] - wall_now)
            if remaining:
                self.open_until = time.monotonic() + remaining
                self._server_not_before = self.open_until
                if self._mirror_probe_interval:
                    # A restart inside an unverified window must not turn the
                    # first client request into an immediate probe; the next
                    # recheck waits one full interval instead.
                    self._mirror_probe_at = (
                        time.monotonic() + self._mirror_probe_interval
                    )
        self._persisted_clear = (
            self._manual_hold is None
            and not self.open_until
            and not self._server_not_before
        )
        self._pre_failure_hold: str | None = (
            None if self._manual_hold == "state_write_failed" else self._manual_hold
        )
        self._pre_failure_cooldown = 0.0
        if self._manual_hold == "state_write_failed":
            # The failed transition itself was never durable. The surviving
            # file cannot establish a safe in-memory breaker or cooldown, so a
            # restart must not turn a clean-looking marker into permission.
            self._state_write_failed = True
            self._state_error_hold = self._manual_hold
            self._persisted_clear = True
            self._state_recovery_phase = "recovery"
        # A persisted write-failure marker is itself a change to diagnose;
        # every other loaded snapshot already matches the disk.
        self._saved_risk = (
            (self.open_until, None, False)
            if self._state_recovery_phase == "recovery"
            else (self.open_until, self._manual_hold, False)
        )
        if self._manual_hold == "unverified_request":
            # A write-ahead marker on disk only proves a request was in flight
            # when the process stopped; its outcome is unknown, not adverse.
            # Hold the lane for one bounded verification window instead of the
            # old indefinite operator review (2026-10-20/21/31 restart latches
            # all carried zero real upstream signals), then let the next
            # request re-verify and re-latch a genuine signal if one appears.
            self._manual_hold = None
            stale_until = time.monotonic() + STALE_WRITE_AHEAD_COOLDOWN_SECONDS
            self._stale_write_ahead_until = stale_until
            self.open_until = max(self.open_until, stale_until)
            self._server_not_before = max(self._server_not_before, stale_until)
            self._persist_risk()
            if not self._state_write_failed:
                logging.info(
                    "stale_write_ahead_cleared cooldown_s=%d",
                    STALE_WRITE_AHEAD_COOLDOWN_SECONDS,
                )

    def _persist_risk(self) -> None:
        risk = (
            max(self.open_until, self._server_not_before),
            self._manual_hold,
            self.inflight > 0,
        )
        if self._state_path is None:
            return
        recovering = self._state_write_failed
        before_open_until = self.open_until
        before_server_not_before = self._server_not_before
        before_hold = self._manual_hold
        if recovering:
            # The local hold must survive a successful diagnostic write; any
            # cooldown armed by the failed transition still governs admission.
            # A missing risk transition remains operator-visible until review:
            # without the fallback a recovery write over storage that never
            # recorded the failure would silently erase the operator latch.
            self._manual_hold = self._pre_failure_hold or "state_write_failed"
            risk = (
                max(risk[0], self._pre_failure_cooldown),
                self._manual_hold,
                risk[2],
            )
        elif self._state_error_hold == "state_write_failed":
            # Storage failed while another operator hold was active. A later
            # clean risk transition may not erase that upstream hold.
            self._manual_hold = self._pre_failure_hold or "state_write_failed"
        now = time.monotonic()
        quota_groups = {
            group: until
            for group, until in self._quota_group_until.items()
            if until > now
        }
        if (
            risk == self._saved_risk
            and quota_groups == self._saved_quota_groups
            and not recovering
        ):
            return
        payload = {
            "version": 1,
            "not_before": time.time() + max(0, risk[0] - time.monotonic())
            if risk[0]
            else 0,
            # Write ahead before any upstream request. If a later risk update
            # cannot reach disk or the process dies, the stale marker makes
            # the next startup run one bounded verification window (see the
            # LaneState load path) instead of silently treating the old clear
            # state as permission to retry; genuine risk reasons still latch
            # for operator review.
            "hold_reason": risk[1] or ("unverified_request" if risk[2] else None),
        }
        if quota_groups:
            wall_now = time.time()
            payload["quota_group_not_before"] = {
                group: wall_now + (until - now) for group, until in quota_groups.items()
            }
        state_directory = self._state_path.parent
        state_directory.mkdir(parents=True, exist_ok=True)
        candidate = state_directory / (
            f".{self._state_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        descriptor = -1
        published = False
        try:
            descriptor = os.open(
                candidate,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(payload, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                current_stat = self._state_path.stat(follow_symlinks=False)
            except FileNotFoundError:
                # A first-ever state file has no predecessor. This check
                # catches a symlink that raced the destination inspected at
                # startup without rejecting normal initial creation.
                current_stat = None
            if current_stat is not None and (
                stat.S_ISLNK(current_stat.st_mode)
                or not stat.S_ISREG(current_stat.st_mode)
            ):
                raise ValueError("OAuth circuit state is not a regular file")
            if (
                self._state_identity is not None
                and current_stat is not None
                and (
                    int(getattr(current_stat, "st_dev", 0)),
                    int(getattr(current_stat, "st_ino", 0)),
                )
                != self._state_identity
            ):
                raise ValueError("OAuth circuit state changed during write")
            os.replace(candidate, self._state_path)
            published = True
            if hasattr(os, "O_DIRECTORY"):
                directory_descriptor = os.open(
                    state_directory,
                    os.O_RDONLY | os.O_DIRECTORY,
                )
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
            current = self._state_path.stat(follow_symlinks=False)
            if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
                raise ValueError("OAuth circuit state is no longer a regular file")
            self._state_identity = (
                int(getattr(current, "st_dev", 0)),
                int(getattr(current, "st_ino", 0)),
            )
            recovered = recovering
            # This snapshot is the first durable record after recovery. The
            # following clean marker can safely release the local hold while
            # retaining any independent upstream hold or cooldown.
            self._persisted_clear = risk[1] is None and not risk[0]
            if recovered:
                # The recovery write proves storage is usable, not that the
                # account recovered. Preserve any upstream hold/cooldown and
                # keep the operator latch when no upstream hold exists.
                self._manual_hold = self._pre_failure_hold or "state_write_failed"
                self._state_error_hold = None
                self._state_recovery_phase = "clear"
                # The recovered upstream hold is already a durable state.
                # Do not turn a normal load into a permanent "recovery"
                # diagnostic merely because storage once recovered.
                self._state_recovery_phase = "clear"
                self._state_error_hold = None
                self._persisted_clear = risk[1] is None and not risk[0]
                self._state_write_failed = False
                self._state_first_error = None
                logging.warning(
                    "lane_state_persisted lane=%s hold=%s source=state_retry",
                    self._lane_name or "other",
                    self._manual_hold,
                )
            self._saved_risk = (risk[0], risk[1], risk[2])
            self._saved_quota_groups = quota_groups
        except (OSError, ValueError) as exc:
            if descriptor >= 0:
                os.close(descriptor)
            if not published:
                candidate.unlink(missing_ok=True)
            if self._manual_hold is None and self._state_error_hold is not None:
                # A failed recovery retains its exact existing hold. Do not
                # replace an upstream hold with a storage-only diagnosis.
                self._manual_hold = self._pre_failure_hold
            if not self._state_write_failed:
                # First failure: snapshot the pre-failure hold and cooldown so
                # a later recovery write can restore the real account state.
                self._pre_failure_hold = before_hold
                self._pre_failure_cooldown = max(
                    before_open_until,
                    before_server_not_before,
                )
            self._state_recovery_phase = "recovery"
            self._state_write_failed = True
            self._state_first_error = self._state_first_error or type(exc).__name__
            # The visible diagnostic owns the hold slot, but retain a prior
            # account hold so a later write failure cannot be mistaken for
            # proof that the account recovered.
            if self._state_error_hold != "state_write_failed":
                self._state_error_hold = "state_write_failed"
                if self._manual_hold:
                    self._persisted_clear = False
            self.open_until = max(before_open_until, self._pre_failure_cooldown)
            self._server_not_before = max(
                before_server_not_before,
                self._pre_failure_cooldown,
            )
            self._latch_hold("state_write_failed", "state_persist_failed")
            logging.error(
                "oauth_state_write_failed operator_review_required first_error=%s",
                self._state_first_error,
            )

    def recover_state(self) -> bool:
        """Retry only a failed diagnostic write; release no independent hold."""
        with self._condition:
            if not self._state_write_failed:
                return True
            self._persist_risk()
            recovered = not self._state_write_failed
            self._condition.notify_all()
            return recovered

    def record_risk(self, hold_reason: str | None, retry_after: int | None) -> None:
        """Latch streaming/header evidence before waiting for EOF or writing."""

        if not self._durable_risk or (hold_reason is None and retry_after is None):
            return
        with self._condition:
            if hold_reason:
                self._latch_hold(hold_reason, "response_signal")
            if retry_after is not None:
                if retry_after > 2147483647:
                    self._apply_implausible_retry(retry_after, "record_risk")
                else:
                    until = time.monotonic() + max(1, retry_after)
                    self.open_until = max(self.open_until, until)
                    self._server_not_before = max(self._server_not_before, until)
                    self._next_probe_at = self.open_until
                    if self._mirror_probe_interval:
                        self._mirror_probe_at = (
                            time.monotonic() + self._mirror_probe_interval
                        )
                self._failure_generation += 1
            self._persist_risk()
            self._condition.notify_all()

    def _latch_hold(self, reason: str, source: str) -> None:
        """Set a durable operator hold and leave an attributable trace.

        Without this line a latched lane could only be explained by
        re-deriving the classifier from the response dump; the 2026-10-31
        incident needed exactly that after the fact.
        """

        if self._manual_hold != reason:
            logging.warning(
                "lane_hold_latched lane=%s reason=%s previous=%s source=%s",
                self._lane_name or "other",
                reason,
                self._manual_hold or "none",
                source,
            )
        self._manual_hold = reason

    def _apply_implausible_retry(self, retry_after: int, source: str) -> None:
        """Handle a Retry-After too large to use as a real backoff window.

        claude-oauth keeps the durable review hold (its quota windows are the
        reason the guard exists). The other OAuth lanes clamp to the reviewed
        ladder cap instead: an edge page with a hostile or malformed header
        must not park a healthy lane until an operator looks at it.
        """

        if self._lane_name == "claude-oauth":
            self._latch_hold("retry_window_review_required", source)
            return
        capped = int(self._schedule[-1])
        until = time.monotonic() + max(1, capped)
        self.open_until = max(self.open_until, until)
        self._server_not_before = max(self._server_not_before, until)
        self._next_probe_at = self.open_until
        if self._mirror_probe_interval:
            self._mirror_probe_at = time.monotonic() + self._mirror_probe_interval
        logging.warning(
            "implausible_retry_after_clamped lane=%s seconds=%s cap=%s source=%s",
            self._lane_name or "other",
            retry_after,
            capped,
            source,
        )

    def _open_retry_after(self, now: float) -> int:
        return max(1, _ceil_remaining_seconds(self.open_until, now))

    def _cooldown_retry_after(self, now: float) -> int:
        # The cooldown rejection must point at the moment the lane will
        # actually re-evaluate recovery. For a mirror-probe lane that is the
        # next probe slot, so a client retrying on the advertised value rides
        # the probe; for every other lane this stays the window's end.
        if self._mirror_probe_interval:
            return max(
                1,
                min(
                    _ceil_remaining_seconds(self.open_until, now),
                    _ceil_remaining_seconds(self._mirror_probe_at, now),
                ),
            )
        return self._open_retry_after(now)

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
        return max(1, _ceil_remaining_seconds(next_look, now))

    def _model_retry_after(self, model: str, now: float) -> int:
        """Advertise the next moment this model will be re-evaluated.

        Same contract as `_shed_retry_after`: a model in a capacity window is
        refused outright (it will not recover inside the client's queue
        budget), and telling the client "retry in 1s" only burns its retry
        budget, so the value points at the probe slot or the window's end.
        On a mirror-probe lane a Retry-After-armed model window is rechecked
        at the cadence, so the advertised retry points at the next recheck
        slot (when it fits inside the window) instead of the unverified
        magnitude; ladder-armed windows keep advertising the window's end.
        """

        deadline = max(
            self._model_open_until.get(model, 0.0),
            self._model_probe_at.get(model, 0.0),
            self._model_server_not_before.get(model, 0.0),
        )
        server_not_before = self._model_server_not_before.get(model, 0.0)
        if self._mirror_probe_interval and server_not_before:
            deadline = min(
                deadline,
                max(
                    server_not_before,
                    self._model_probe_at.get(model, 0.0),
                ),
            )
        return max(1, _ceil_remaining_seconds(deadline, now))

    def _prune_rpm(self, now: float) -> None:
        """Drop admission timestamps older than the rolling 60s window."""
        cutoff = now - ADMISSION_RPM_WINDOW_SECONDS
        while self._rpm_window and self._rpm_window[0] <= cutoff:
            self._rpm_window.popleft()

    def _rpm_retry_after(self, now: float) -> int:
        """Seconds until the oldest admission in the window frees a slot."""
        if not self._rpm_window:
            return 1
        reopen = self._rpm_window[0] + ADMISSION_RPM_WINDOW_SECONDS
        return max(1, _ceil_remaining_seconds(reopen, now))

    def _record_model_capacity(
        self, model: str, retry_after: int | None, now: float
    ) -> None:
        """Park one model after an upstream signal that names that model."""

        streak = self._model_streak.get(model, 0) + 1
        self._model_streak[model] = streak
        self._model_failure_generation[model] = (
            self._model_failure_generation.get(model, 0) + 1
        )
        if retry_after is not None:
            delay = min(max(1, retry_after), self._retry_after_max)
        elif self._strict_backoff or streak >= ADMISSION_COOLDOWN_FAILURE_THRESHOLD:
            # Strict lanes park the model from the first refusal, mirroring
            # the lane ladder's strict behavior: on the shared OAuth
            # credential one upstream capacity verdict about a model is
            # enough evidence to stop spending requests on it, while the
            # early probe keeps the window short.
            index = min(
                max(
                    0,
                    streak
                    - (
                        1
                        if self._strict_backoff
                        else ADMISSION_COOLDOWN_FAILURE_THRESHOLD
                    ),
                ),
                len(self._schedule) - 1,
            )
            delay = self._schedule[index]
        else:
            delay = None
        if delay is not None:
            # An upstream-advertised Retry-After is the upstream itself telling
            # us to back off that model, so the probe may not fire before it.
            # Same contract as the lane-level `_server_not_before` -- except on
            # a mirror-probe lane, where the advertised magnitude is treated as
            # unverified: the 2026-11-21 opus window claimed 17504s while the
            # upstream kept serving the account seconds later, and the
            # 2026-11-22 flash window recovered in minutes. The model deadline
            # is re-checked at the lane's mirror cadence instead of
            # blind-parking the model for the whole advertisement; a hint
            # shorter than the cadence keeps its own (shorter) deadline.
            probe_delay = self._early_probe_interval
            if retry_after is not None:
                bound = min(max(1, retry_after), self._retry_after_max)
                if self._mirror_probe_interval:
                    bound = min(bound, int(self._mirror_probe_interval))
                    probe_delay = float(bound)
                self._model_server_not_before[model] = max(
                    self._model_server_not_before.get(model, 0.0),
                    now + bound,
                )
            self._model_open_until[model] = max(
                self._model_open_until.get(model, 0.0),
                now + min(delay, self._retry_after_max),
            )
            self._model_probe_at[model] = now + probe_delay

    def begin_quota_check(self) -> None:
        with self._condition:
            self._quota_checks += 1

    def _quota_retry_after(self, model: str | None, now: float) -> int:
        if not self._antigravity_models:
            return 0
        group = antigravity_quota_scope_key(model)
        family = antigravity_quota_group(model)
        family_key = f"family:{family}" if family is not None else None
        until = max(
            self._quota_group_until.get(group, 0.0) if group is not None else 0.0,
            self._quota_group_until.get(family_key, 0.0)
            if family_key is not None
            else 0.0,
        )
        return max(0, _ceil_remaining_seconds(until, now))

    def acquire(
        self, alive: Callable[[], bool] | None = None, model: str | None = None
    ) -> Lease:
        started = time.monotonic()
        deadline = started + self._queue_timeout

        def lease(
            admitted: bool,
            reason: str,
            retry_after: int,
            probe: bool = False,
            model_probe: bool = False,
            reset_probe: bool = False,
        ) -> Lease:
            if admitted and self._durable_risk and self._state_path is not None:
                self._persist_risk()
                if self._state_write_failed:
                    self.inflight = max(0, self.inflight - 1)
                    self.probe_inflight = False
                    self._model_probe_inflight.discard(model)
                    admitted, reason, retry_after = (
                        False,
                        "operator_review_required",
                        300,
                    )
            return Lease(
                admitted,
                reason,
                retry_after,
                probe,
                waited_ms=int((time.monotonic() - started) * 1000),
                generation=self._failure_generation,
                reset_probe=reset_probe,
                model=model,
                model_probe=model_probe,
                model_generation=(
                    self._model_failure_generation.get(model, 0)
                    if model is not None
                    else 0
                ),
            )

        with self._condition:
            ticket = object()
            queued = False
            try:
                while True:
                    now = time.monotonic()
                    if alive is not None and not alive():
                        return lease(False, "downstream_gone", 1)
                    if self._manual_hold:
                        # `account_credits_exhausted` is one credential's
                        # billing state, not a lane-wide outage: a zero-cost
                        # model (WorkBuddy hy4free at x0.00 credits) still
                        # serves from a drained account. Gating the whole lane
                        # made one drained credential delete every model,
                        # including the free one. Every other hold reason stays
                        # lane-wide: those describe the account itself, where
                        # any model would fail.
                        credits_only = (
                            self._manual_hold == "account_credits_exhausted"
                            and model in self._zero_cost_models
                        )
                        if not credits_only:
                            return lease(False, "operator_review_required", 300)
                    if self._stale_write_ahead_until > now:
                        return lease(
                            False,
                            "stale_write_ahead_cooldown",
                            _ceil_remaining_seconds(self._stale_write_ahead_until, now),
                        )
                    if queued and now >= deadline:
                        return lease(
                            False, "queue_timeout", self._shed_retry_after(now)
                        )
                    queue_turn = not self._pending_queue or (
                        queued and self._pending_queue[0] is ticket
                    )
                    if model is not None and now < self._model_server_not_before.get(
                        model, 0.0
                    ):
                        # A lane recovery probe is still a request for this
                        # model and cannot bypass its explicit Retry-After.
                        return lease(
                            False,
                            "model_cooldown",
                            max(
                                self._model_retry_after(model, now),
                                self._open_retry_after(now),
                            ),
                        )
                    quota_wait = self._quota_retry_after(model, now)
                    if quota_wait:
                        return lease(
                            False,
                            "quota_group_cooldown",
                            max(quota_wait, self._open_retry_after(now)),
                        )
                    if self.probe_inflight or self._quota_checks:
                        # While a quota error is being classified, admit no
                        # new work until its shared or group deadline is durable.
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
                    # A zero-cost model (WorkBuddy hy4free at x0.00 credits)
                    # consumes none of the billed allowance the lane breaker
                    # protects, so it passes the cooldown as well. Since
                    # 2026-11-14 the 6008 daily-allowance refusal arms this
                    # cooldown instead of a durable hold; the previous
                    # hold-path-only exemption would otherwise delete the free
                    # model for the whole burnout evening. Holds that describe
                    # the account itself (authentication/restriction) still
                    # gate every model at the hold gate above.
                    free_under_billing_state = model in self._zero_cost_models
                    if self.open_until > now and not free_under_billing_state:
                        if (
                            self._mirror_probe_interval
                            and now >= self._mirror_probe_at
                            and self.inflight < self._max_inflight
                            and queue_turn
                        ):
                            # A mirrored capacity cooldown re-verifies recovery
                            # one request at a time on the lane's configured
                            # cadence. The probe is a reset probe: its success
                            # clears the window immediately, and its failure
                            # re-mirrors the upstream window and re-arms the
                            # cadence, so the rate stays bounded no matter how
                            # long the upstream claims to be busy.
                            self.probe_inflight = True
                            self.inflight += 1
                            self._mirror_probe_at = now + self._mirror_probe_interval
                            return lease(
                                True, "mirror_probe", 0, probe=True, reset_probe=True
                            )
                        if (
                            not self._strict_backoff
                            and now >= max(self._next_probe_at, self._server_not_before)
                            and self.inflight < self._max_inflight
                            and queue_turn
                        ):
                            self.probe_inflight = True
                            self.inflight += 1
                            self._next_probe_at = now + self._early_probe_interval
                            return lease(True, "early_probe", 0, probe=True)
                        next_admission = self.open_until
                        if not self._strict_backoff:
                            next_admission = min(
                                next_admission,
                                max(self._next_probe_at, self._server_not_before),
                            )
                        if self._mirror_probe_interval:
                            # A mirror-probe lane queues a fitting arrival for
                            # the next recheck slot instead of bouncing it for
                            # the whole window; the queue head rides the probe.
                            next_admission = min(next_admission, self._mirror_probe_at)
                        # An upstream Retry-After can outlast the remaining
                        # budget of a request already in the queue.
                        if (
                            queued
                            and self._server_not_before >= deadline
                            and next_admission >= deadline
                        ):
                            return lease(
                                False, "cooldown", self._cooldown_retry_after(now)
                            )
                        if not queued and (next_admission - now <= self._queue_timeout):
                            # A long local cooldown can still have an early
                            # probe inside the queue budget. Wait for that slot
                            # without bypassing the server's Retry-After.
                            # The FIFO head becomes the single recovery probe;
                            # followers remain bounded by the same queue budget.
                            # If neither expiry nor a permitted probe fits,
                            # refuse instead of holding an unserviceable turn.
                            if self.pending >= self._max_pending:
                                return lease(False, "busy", self._shed_retry_after(now))
                            self._pending_queue.append(ticket)
                            self.pending += 1
                            queued = True
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
                        return lease(False, "cooldown", self._cooldown_retry_after(now))
                    if model is not None and (
                        self._model_open_until.get(model, 0.0) > now
                    ):
                        # A model-attributed capacity signal parks that model
                        # only. Its siblings on the same credential keep the
                        # lane's shared concurrency budget and are admitted
                        # normally; this request is refused outright rather
                        # than queued, because a model that is out of capacity
                        # will not come back inside the client's queue budget.
                        # One probe per interval re-tests it, and only a
                        # current-generation success clears the window.
                        if (
                            now
                            >= max(
                                self._model_probe_at.get(model, 0.0),
                                self._model_server_not_before.get(model, 0.0),
                            )
                            and self.inflight < self._max_inflight
                            and queue_turn
                            and model not in self._model_probe_inflight
                        ):
                            self._model_probe_inflight.add(model)
                            self.inflight += 1
                            if self._rpm_max is not None:
                                self._prune_rpm(now)
                                self._rpm_window.append(now)
                            self._model_probe_at[model] = (
                                now + self._early_probe_interval
                            )
                            return lease(
                                True, "model_half_open", 0, probe=True, model_probe=True
                            )
                        return lease(
                            False,
                            "model_cooldown",
                            self._model_retry_after(model, now),
                        )
                    if self.inflight < self._max_inflight and queue_turn:
                        if self.open_until:
                            self.probe_inflight = True
                            self.inflight += 1
                            if self._rpm_max is not None:
                                self._prune_rpm(now)
                                self._rpm_window.append(now)
                            return lease(True, "half_open", 0, probe=True)
                        # Per-lane request-rate guard (opt-in via
                        # requests_per_minute). It shapes *normal* admissions
                        # only: recovery probes above bypass it so a cooldown
                        # can still be re-tested. It smooths bursty fan-out
                        # (parallel titles/summaries) toward a per-credential
                        # cadence so a single shared account is not pushed past
                        # the upstream's IP/account-level rate window; it is a
                        # dispersal control, never a quota/ban workaround.
                        # A request that arrives after the rolling window is
                        # full waits in the same bounded FIFO as concurrency
                        # waiters instead of failing mid-stream. Only a full
                        # queue or an exhausted queue budget is refused, so a
                        # short window delay does not become a desktop stream
                        # failure.
                        if self._rpm_max is not None:
                            self._prune_rpm(now)
                            if len(self._rpm_window) >= self._rpm_max:
                                if not queued:
                                    if self.pending >= self._max_pending:
                                        return lease(
                                            False,
                                            "rate_limited",
                                            self._rpm_retry_after(now),
                                        )
                                    self._pending_queue.append(ticket)
                                    self.pending += 1
                                    queued = True
                                remaining = deadline - now
                                if remaining <= 0:
                                    return lease(
                                        False,
                                        "rate_limited",
                                        self._rpm_retry_after(now),
                                    )
                                wait_for = min(
                                    remaining,
                                    ADMISSION_QUEUE_POLL_SECONDS,
                                    max(0.05, float(self._rpm_retry_after(now))),
                                )
                                self._condition.wait(timeout=wait_for)
                                continue
                        self.inflight += 1
                        if self._rpm_max is not None:
                            self._rpm_window.append(now)
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

    def acquire_after_reset(self, expected_generation: int) -> Lease:
        with self._condition:
            now = time.monotonic()
            reason = ""
            retry_after = 1
            if self._strict_backoff or self._manual_hold:
                reason = "operator_review_required"
            elif expected_generation != self._failure_generation:
                reason = "stale_generation"
            elif self.inflight or self.pending or self.probe_inflight:
                reason = "busy"
            elif now < self._reset_probe_not_before:
                reason = "reset_probe_throttled"
                retry_after = max(
                    1, _ceil_remaining_seconds(self._reset_probe_not_before, now)
                )
            elif not self.open_until:
                reason = "not_cooling"
            if reason:
                return Lease(
                    False, reason, retry_after, generation=self._failure_generation
                )
            self.inflight += 1
            self.probe_inflight = True
            self._reset_probe_not_before = now + ADMISSION_RESET_PROBE_INTERVAL_SECONDS
            self._persist_risk()
            if self._state_write_failed:
                self.inflight -= 1
                self.probe_inflight = False
                return Lease(
                    False,
                    "operator_review_required",
                    300,
                    generation=self._failure_generation,
                )
            return Lease(
                True,
                "quota_reset_probe",
                0,
                probe=True,
                generation=self._failure_generation,
                reset_probe=True,
            )

    def release(
        self,
        lease: Lease,
        *,
        capacity_error: bool,
        retry_after: int | None,
        successful: bool = True,
        capacity_scope: str = "lane",
        hold_reason: str | None = None,
        quota_check: bool = False,
    ) -> None:
        with self._condition:
            now = time.monotonic()
            if quota_check:
                self._quota_checks -= 1
            self.inflight = max(0, self.inflight - 1)
            if lease.probe and not lease.model_probe:
                self.probe_inflight = False
            if lease.model_probe and lease.model is not None:
                self._model_probe_inflight.discard(lease.model)
            if self._durable_risk and hold_reason:
                # Route through _latch_hold, not a direct assignment: an OAuth
                # hold latched only from the request path left no
                # lane_hold_latched line, so a parked lane could only be
                # explained by re-deriving the classifier from a response dump
                # that CPA may already have rotated away. 2026-11-08: the
                # antigravity 401s of 01:59 had no attributable latch at all.
                self._latch_hold(hold_reason, "response_signal")
            quota_group = (
                antigravity_quota_scope_key(lease.model)
                if self._antigravity_models and capacity_scope == "quota_group"
                else None
            )
            if (
                capacity_error
                and self._antigravity_models
                and lease.model is not None
                and capacity_scope == "lane"
            ):
                # A model-bearing Antigravity refusal must not fall through to
                # the shared lane breaker when an upstream path omitted its
                # narrower scope annotation.
                capacity_scope = "model"
            if capacity_error and quota_group is not None and retry_after is not None:
                self._quota_group_until[quota_group] = max(
                    self._quota_group_until.get(quota_group, 0.0), now + retry_after
                )
                logging.info(
                    "quota_group_cooldown lane=%s group=%s retry_after=%s",
                    self._lane_name,
                    quota_group,
                    retry_after,
                )
            elif (
                capacity_error and capacity_scope == "model" and lease.model is not None
            ):
                # The upstream named one model, so only that model is parked.
                # Promoting it to the lane would refuse the healthy siblings
                # that share the credential (and the concurrency budget).
                model_retry_after = retry_after
                if model_retry_after is None and self._lane_name == "antigravity-oauth":
                    model_retry_after = self._schedule[0]
                self._record_model_capacity(lease.model, model_retry_after, now)
                if (
                    lease.probe
                    and not lease.model_probe
                    and lease.generation == self._failure_generation
                ):
                    # A lane probe answered with a model marker still proves the
                    # account is reachable, so it must not keep the lane breaker
                    # shut: otherwise one parked model holds the lane open and
                    # its siblings keep seeing the lane-level refusal.
                    self.failure_streak = 0
                    self.open_until = 0.0
                    self._next_probe_at = 0.0
                    self._server_not_before = 0.0
                    self._mirror_probe_at = 0.0
            elif capacity_error:
                self._failure_generation += 1
                self.failure_streak += 1
                if (
                    self._strict_backoff
                    and retry_after is not None
                    and retry_after > 2147483647
                ):
                    # Do not turn a malformed/implausible upstream window into
                    # either float overflow or permission to resume sooner.
                    self._apply_implausible_retry(retry_after, "capacity_window")
                    retry_after = None
                if retry_after is not None:
                    # An upstream-advertised Retry-After is the upstream itself
                    # telling us to back off, so honour it immediately rather
                    # than waiting for the streak to build.  Keep the
                    # provider's value inside the reviewed safety bound: an
                    # unbounded or stale header must not pin the lane beyond
                    # the contract advertised by /healthz.
                    delay = (
                        max(1, retry_after)
                        if self._strict_backoff
                        else min(max(1, retry_after), self._retry_after_max)
                    )
                    self._server_not_before = max(self._server_not_before, now + delay)
                elif (
                    self._strict_backoff
                    or self.failure_streak >= ADMISSION_COOLDOWN_FAILURE_THRESHOLD
                ):
                    # Antigravity waits from the first refusal. Other lanes
                    # retain their consecutive-failure threshold.
                    index = min(
                        max(
                            0,
                            self.failure_streak
                            - (
                                1
                                if self._strict_backoff
                                else ADMISSION_COOLDOWN_FAILURE_THRESHOLD
                            ),
                        ),
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
                    self._next_probe_at = (
                        self.open_until
                        if self._strict_backoff
                        else now + self._early_probe_interval
                    )
                    if self._mirror_probe_interval:
                        self._mirror_probe_at = now + self._mirror_probe_interval
                if lease.model is not None:
                    # A lane-scoped refusal that landed on this model's request
                    # is still evidence about *this model*: upstream sheds are
                    # model-correlated even when the signal text is
                    # account-level (2026-10-10: the same credential served
                    # gpt-6.1-sol requests all afternoon while every
                    # gpt-6-luna request was refused). Count a per-model streak
                    # so a repeatedly refused model gets parked and its clients
                    # fail fast, instead of every refusal riding on the lane
                    # streak that healthy siblings keep resetting. A
                    # model-scoped Retry-After would be fabricated here -- the
                    # advertised account backoff lives in the lane's
                    # server_not_before -- so the streak counts without a
                    # deadline and only the reviewed schedule parks it.
                    self._record_model_capacity(lease.model, None, now)
            elif (
                lease.model_probe
                and lease.model is not None
                and successful
                and lease.model_generation
                == self._model_failure_generation.get(lease.model, 0)
            ):
                # A surviving model probe proves that model's capacity window
                # is over. A newer model failure carries a newer generation and
                # therefore cannot be cleared by this stale success.
                self._model_streak.pop(lease.model, None)
                self._model_open_until.pop(lease.model, None)
                self._model_probe_at.pop(lease.model, None)
                self._model_server_not_before.pop(lease.model, None)
            elif (
                lease.probe
                and not lease.model_probe
                and successful
                and lease.generation == self._failure_generation
                and (lease.reset_probe or now >= self._server_not_before)
            ):
                # A successful half-open probe proves the outage is over.
                self.failure_streak = 0
                self.open_until = 0.0
                self._next_probe_at = 0.0
                self._server_not_before = 0.0
                self._mirror_probe_at = 0.0
                if (
                    lease.model is not None
                    and lease.model_generation
                    == self._model_failure_generation.get(lease.model, 0)
                ):
                    # The probe rode on a real model request, so its success is
                    # that model's success too: clear its streak under the same
                    # consecutive-failure contract as a plain request.
                    self._model_streak.pop(lease.model, None)
            elif (
                successful
                and not lease.probe
                and (lease.generation == self._failure_generation)
            ):
                # A success on a normal request also breaks the streak: the
                # threshold counts *consecutive* failures, so two blips
                # separated by a served request must not open the breaker.
                self.failure_streak = 0
                if (
                    lease.model is not None
                    and lease.model_generation
                    == self._model_failure_generation.get(lease.model, 0)
                ):
                    # Same consecutive-failure contract for the per-model
                    # streak: a served sibling request must clear it.
                    self._model_streak.pop(lease.model, None)
            self._persist_risk()
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            now = time.monotonic()
            model_cooldowns = {
                model: max(0, _ceil_remaining_seconds(until, now))
                for model, until in sorted(self._model_open_until.items())
                if until > now
            }
            for model in self._antigravity_models:
                quota_wait = self._quota_retry_after(model, now)
                if quota_wait:
                    model_cooldowns[model] = max(
                        model_cooldowns.get(model, 0), quota_wait
                    )
            return {
                "inflight": self.inflight,
                "strict_backoff": self._strict_backoff,
                "hold_reason": self._manual_hold,
                "state_persisted": self._state_path is not None,
                "state_write_failed": self._state_write_failed,
                "pending": self.pending,
                "failure_streak": self.failure_streak,
                # `open_until` is a monotonic deadline.  Once it has elapsed,
                # the lane is no longer cooling even if the demand-driven
                # half-open probe has not run yet; reporting the stale truth
                # makes recovery workflows issue an unnecessary reset probe.
                "cooldown_active": self.open_until > now,
                "cooldown_remaining": max(
                    0, _ceil_remaining_seconds(self.open_until, now)
                ),
                # Which scope holds the breaker shut: the whole lane, or only
                # the models the upstream named. Observable so a cross-model
                # regression is visible in /healthz instead of only in 429s.
                "cooldown_scope": (
                    "lane"
                    if self.open_until > now
                    else ("model" if model_cooldowns else "none")
                ),
                "model_cooldowns": model_cooldowns,
                "early_probe_in": max(
                    0, _ceil_remaining_seconds(self._next_probe_at, now)
                ),
                "mirror_probe_interval": self._mirror_probe_interval,
                "mirror_probe_in": max(
                    0, _ceil_remaining_seconds(self._mirror_probe_at, now)
                ),
                "half_open_probe": self.probe_inflight,
                "failure_generation": self._failure_generation,
                "server_retry_after_remaining": max(
                    0, _ceil_remaining_seconds(self._server_not_before, now)
                ),
                "reset_probe_in": max(
                    0, _ceil_remaining_seconds(self._reset_probe_not_before, now)
                ),
                # Optional per-lane request-rate guard observability.
                "requests_per_minute": self._rpm_max,
                "rpm_window_used": (
                    None
                    if self._rpm_max is None
                    else len(
                        [
                            t
                            for t in self._rpm_window
                            if t > now - ADMISSION_RPM_WINDOW_SECONDS
                        ]
                    )
                ),
            }


class AdmissionProxy:
    def __init__(
        self,
        config: dict[str, Any],
        heartbeat_interval: float = SSE_HEARTBEAT_INTERVAL_SECONDS,
        state_path: Path | None = None,
        claude_state_path: Path | None = None,
        chatgpt_state_path: Path | None = None,
        workbuddy_state_path: Path | None = None,
        workbuddy_intl_state_path: Path | None = None,
        traffic_lock_path: Path | None = None,
    ) -> None:
        self.config = config
        # Use the existing deployed route manifest as the capability authority;
        # do not duplicate a list of model names in admission configuration.
        routes = json.loads(
            Path(__file__)
            .with_name("cpa_provider_routes.json")
            .read_text(encoding="utf-8")
        )
        self.text_only_models = frozenset(
            model["alias"].strip().lower()
            for provider in routes["providers"]
            for model in provider["models"]
            if model.get("input-modalities") == ["text"]
        )
        self.traffic_lock_path = traffic_lock_path
        self.request_guard_broker = (
            RequestGuardBroker(traffic_lock_path)
            if traffic_lock_path is not None
            else None
        )
        self.heartbeat_interval = heartbeat_interval
        self.lanes = {
            lane["name"]: LaneState(
                lane,
                {
                    "antigravity-oauth": state_path,
                    "claude-oauth": claude_state_path,
                    "chatgpt-oauth": chatgpt_state_path,
                    "workbuddy-oauth": workbuddy_state_path,
                    "workbuddy-intl-oauth": workbuddy_intl_state_path,
                }.get(lane["name"]),
            )
            for lane in config["lanes"]
        }
        self.lane_config = {lane["name"]: lane for lane in config["lanes"]}
        # Reader threads that could not be joined (see _retire_reader) are
        # parked on a socket that is already being torn down. Track the live
        # resident set separately from the cumulative retirement count so a
        # temporary disconnect does not look like a permanent thread leak.
        self._retired_readers: dict[int, threading.Thread] = {}
        self.retired_readers_total = 0
        self._resident_lock = threading.Lock()
        # Observation only; see ModelTelemetry. Deliberately not consulted by
        # any admission decision.
        self.telemetry = ModelTelemetry()

    @contextmanager
    def request_guard(self) -> Iterator[bool]:
        """Hold a shared OS lock until the complete upstream request finishes.

        The updater holds the exclusive side across replacement and rollback.
        The bootstrap-owned guard holds a separate shared lock descriptor for
        each request. A retired worker thread has no request-lock fd of its own,
        so it cannot extend maintenance. Missing/inaccessible guards fail closed.
        """
        if self.traffic_lock_path is None:
            yield True
            return
        # Compatibility window for the currently deployed direct
        # `python3 cpa-admission.py` generation. It has no marker and no
        # broker, so after the updater closes its drain fd it uses the legacy
        # per-request descriptor. The reviewed launcher generation always sets
        # a marker and fails closed if its broker is unavailable.
        marker = os.environ.get("VPS_SSH_LAUNCHER_ADMISSION_PROCESS_MARKER", "")
        if not marker:
            import fcntl

            try:
                handle = self.traffic_lock_path.open("a")
            except OSError as exc:
                logging.error(
                    "request_guard_refused stage=open reason=lock_unavailable errno=%s",
                    exc.errno,
                )
                yield False
                return
            with handle:
                try:
                    fcntl.flock(  # type: ignore[attr-defined]
                        handle,
                        fcntl.LOCK_SH | fcntl.LOCK_NB,  # type: ignore[attr-defined]
                    )
                except OSError as exc:
                    busy = exc.errno in (errno.EACCES, errno.EAGAIN)
                    logging.warning(
                        "request_guard_refused stage=flock reason=%s errno=%s",
                        "maintenance_busy" if busy else "lock_error",
                        exc.errno,
                    )
                    yield False
                    return
                try:
                    yield True
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)  # type: ignore[attr-defined]
            return
        if (
            self.request_guard_broker is None
            or self.request_guard_broker._guard_fd is None
        ):
            yield False
            return
        guard = self.request_guard_broker.acquire()
        errno_number = (
            None if guard is not None else self.request_guard_broker.last_error
        )
        if guard is None:
            busy = errno_number in (errno.EACCES, errno.EAGAIN)
            logging.error(
                "request_guard_refused stage=%s reason=%s errno=%s",
                "flock" if busy else "open",
                "maintenance_busy" if busy else "lock_unavailable",
                errno_number,
            )
            yield False
            return
        lock_id = guard[0]
        try:
            yield True
        finally:
            try:
                self.request_guard_broker.release(lock_id)
            except OSError as exc:
                # The broker closes all outstanding fds when admission exits.
                # A loss reported only after the business response must not
                # produce a misleading second response on the same connection.
                logging.error(
                    "request_guard_refused stage=release reason=broker_lost errno=%s",
                    exc.errno,
                )

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
            "status": "degraded"
            if any(
                lane.snapshot()["state_write_failed"] for lane in self.lanes.values()
            )
            else "ok",
            "listen": (f"{self.config['listen_host']}:{self.config['listen_port']}"),
            "upstream": f"{self.config['upstream_host']}:{self.config['upstream_port']}",
            "retry_after_max_seconds": self.config["retry_after_max_seconds"],
            "retired_readers": resident_readers,
            "retired_readers_total": retired_readers_total,
            "telemetry": self.telemetry.snapshot(),
            "traffic_guard": str(self.traffic_lock_path)
            if self.traffic_lock_path
            else None,
            "lanes": {
                name: {
                    "models": list(self.lane_config[name]["models"]),
                    "max_inflight": self.lane_config[name]["max_inflight"],
                    "max_pending": self.lane_config[name]["max_pending"],
                    "queue_timeout_seconds": self.lane_config[name][
                        "queue_timeout_seconds"
                    ],
                    "requests_per_minute": self.lane_config[name].get(
                        "requests_per_minute"
                    ),
                    "state": lane.snapshot(),
                }
                for name, lane in self.lanes.items()
            },
        }


def completed_reset_probe(body: bytes) -> bool:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return False
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "completed"
        or payload.get("error")
        or not isinstance(payload.get("output"), list)
    ):
        return False
    text = "".join(
        part["text"]
        for item in payload["output"]
        if isinstance(item, dict)
        and item.get("type") == "message"
        and isinstance(item.get("content"), list)
        for part in item["content"]
        if isinstance(part, dict)
        and part.get("type") == "output_text"
        and isinstance(part.get("text"), str)
    )
    return text.strip() == "OK"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Streamed SSE chunks are flushed incrementally; disabling Nagle avoids
    # loopback Delayed-ACK stalls between small chunked frames.
    disable_nagle_algorithm = True

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
        self._client_request_hash = client_request_hash
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

    def _begin_queued_stream(self) -> None:
        """Commit a close-delimited SSE response before waiting for a slot.

        A desktop client abandons a silent request after roughly 30-45 seconds.
        Waiting for admission and the upstream before sending even the response
        headers made a long FIFO wait look like an idle network failure. Once
        committed here, every later outcome is delivered as an SSE terminal
        event.
        """

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self._send_trace_headers("queue_stream")
            self.end_headers()
            # The queue heartbeat starts after one full reviewed interval, so
            # an immediately admitted request keeps its upstream bytes intact.
            self.wfile.flush()
        except (OSError, ValueError) as exc:
            raise DownstreamClientDisconnected(
                "downstream client disconnected before queue headers"
            ) from exc
        self.close_connection = True

    def _send_stream_failure(
        self,
        *,
        code: str,
        message: str,
        retry_after: int | None = None,
    ) -> None:
        """Deliver a terminal failure after a 200 SSE header was committed.

        The wire shape is protocol-specific and must stay parseable by the
        client that opened the stream. Anthropic Messages peers (route
        ``/v1/messages``) reject a bare ``{"error": ...}`` data frame because
        every event there carries a ``type`` discriminator; they surface it as
        a protocol violation instead of a retryable capacity signal. Such a
        client never sees our ``Retry-After`` and cannot back off, so one
        admission refusal turns into a hard turn failure. Messages therefore
        gets a typed ``event: error`` envelope, which is what the protocol
        defines for an in-stream failure.
        """

        error = {
            "type": "server_error",
            "code": code,
            "message": message,
        }
        if retry_after is not None:
            error["retry_after"] = max(1, retry_after)
        route = urlsplit(self.path).path.rstrip("/").lower()
        if route == "/v1/messages":
            payload = {"type": "error", "error": error}
            encoded = (
                f"event: error\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
            ).encode("utf-8")
        elif route == "/v1/responses":
            request_id = getattr(self, "_request_id", "unknown")
            payload = {
                "type": "response.failed",
                "response": {
                    "id": f"resp_admission_{request_id}",
                    "object": "response",
                    "created_at": int(time.time()),
                    "status": "failed",
                    "error": error,
                },
            }
            encoded = (
                "event: response.failed\n"
                f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
            ).encode("utf-8")
        else:
            encoded = (
                f"data: {json.dumps({'error': error}, separators=(',', ':'))}\n\n"
                "data: [DONE]\n\n"
            ).encode("utf-8")
        try:
            self.wfile.write(encoded)
            self.wfile.flush()
        except (OSError, ValueError):
            self.close_connection = True
            logging.info(
                "downstream_gone stage=stream_terminal %s",
                getattr(self, "_trace_context", "request_id=- route=other"),
            )

    def _proxy(self) -> None:
        self._start_request_trace()
        proxy: AdmissionProxy = self.server.proxy  # type: ignore[attr-defined]
        if self.command == "GET" and urlsplit(self.path).path == "/healthz":
            self._proxy_unlocked()
            return
        if self._answers_locally_without_a_dump():
            # Answered here, never forwarded: see the method's docstring.
            self._proxy_unlocked()
            return
        with proxy.request_guard() as admitted:
            if not admitted:
                self.close_connection = True
                self._send_json(503, {"error": {"type": "maintenance_in_progress"}})
                return
            self._proxy_unlocked()

    @staticmethod
    def _silent_probe_route(path: str) -> str | None:
        """Return a local-only answer for probe routes CPA does not implement.

        CPA has no /v1/files endpoint and never will (it is an Anthropic
        Files-API surface); a client that probes it gets 404. That 404 is also
        written to the error-dump ring, and the ring only holds
        error-logs-max-files=5 entries. Measured on bwg 2026-11-08: the DSH
        client probes the route on every file reference, 570 times in six
        hours, so the five slots rotated every 12-25 seconds and a genuine
        upstream failure dump survived under 90 seconds. The 404 itself carries
        no upstream evidence -- it is generated by CPA before any provider call
        -- so answering it here costs nothing and frees the ring for the dumps
        an operator actually needs.

        Cockpit Desktop likewise probes /v1/cockpit/connection on startup and
        reconnect to check whether a remote gateway is a native Cockpit server;
        answering /v1/cockpit(/...) locally prevents those probes from hitting
        CPA :8317 or polluting passthrough/other HTTP 404 error telemetry.

        This is deliberately narrow: it answers only routes with no upstream
        semantics, and it must never swallow a path a provider could serve.
        """

        route = urlsplit(path).path.rstrip("/")
        if route in ("/v1/files", "/v1/files/list"):
            return "files_api_unsupported"
        if route.startswith("/v1/files/"):
            return "files_api_unsupported"
        if route == "/v1/cockpit" or route.startswith("/v1/cockpit/"):
            return "cockpit_api_unsupported"
        return None

    def _answers_locally_without_a_dump(self) -> bool:
        return self._silent_probe_route(self.path) is not None

    def _proxy_unlocked(self) -> None:
        proxy: AdmissionProxy = self.server.proxy  # type: ignore[attr-defined]
        config = proxy.config
        body = self._read_body(config["max_body_bytes"])
        if self.command == "GET" and urlsplit(self.path).path == "/healthz":
            self._send_json(200, proxy.health())
            return
        probe_reason = self._silent_probe_route(self.path)
        if probe_reason is not None:
            # Drain the body first (done above) so the connection stays
            # reusable, then answer with the same 404 shape CPA would send.
            probe_message = (
                "This gateway does not implement the Cockpit management API."
                if probe_reason == "cockpit_api_unsupported"
                else (
                    "This gateway does not implement the Anthropic Files "
                    "API. Inline the file content in the request instead."
                )
            )
            self._send_json(
                404,
                {
                    "error": {
                        "message": probe_message,
                        "type": "not_found_error",
                        "code": probe_reason,
                    }
                },
                admission_reason=probe_reason,
            )
            logging.info(
                "probe_answered_locally reason=%s %s",
                probe_reason,
                self._trace_context,
            )
            return
        content_encodings = self.headers.get_all("Content-Encoding", [])
        content_encoding = (
            content_encodings[0].strip().lower() if len(content_encodings) == 1 else ""
        )
        if content_encodings and (
            len(content_encodings) != 1 or content_encoding not in {"", "identity"}
        ):
            # CPA accepts zstd request bodies, but admission can only enforce
            # model/lane, image, and encrypted-agent checks on decoded JSON.
            # Passing an encoded body through would let those guards fail open.
            # Rejecting before reading also avoids an ambiguous pipelined body.
            self.close_connection = True
            reason = "unsupported_content_encoding"
            self._send_json(
                415,
                {
                    "error": {
                        "type": "invalid_request_error",
                        "code": reason,
                        "message": (
                            "Admission requires an identity request body so shared "
                            "account and protocol guards can inspect it."
                        ),
                    },
                    "upstream_contacted": False,
                },
                admission_reason=reason,
            )
            logging.info(
                "protocol_reject reason=%s %s",
                reason,
                self._trace_context,
            )
            return
        reset_probe = urlsplit(self.path).path == "/admin/recover-after-reset"
        if reset_probe and (
            self.command != "POST"
            or self.client_address[0] != "127.0.0.1"
            or any(key.lower().startswith("x-forwarded-") for key in self.headers)
        ):
            self.close_connection = True
            self._send_json(404, {"error": {"type": "not_found"}})
            return
        expected_generation = None
        if reset_probe:
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("invalid quota reset recovery request") from None
            if (
                not isinstance(payload, dict)
                or set(payload) != {"model", "reason", "expected_generation"}
                or payload.get("reason") not in {"quota_reset", "cooldown_reset"}
                or type(payload.get("expected_generation")) is not int
                or payload["expected_generation"] < 0
                or not isinstance(payload.get("model"), str)
                or payload["model"] not in config["model_lanes"]
                or config["model_lanes"][payload["model"]] != "chatgpt-oauth"
            ):
                raise ValueError("invalid quota reset recovery request")
            expected_generation = payload["expected_generation"]
            self.path = "/v1/responses"
            body = json.dumps(
                {
                    "model": payload["model"],
                    "input": "Reply with exactly: OK",
                    "max_output_tokens": 1024,
                    "reasoning": {"effort": "low"},
                    "stream": False,
                }
            ).encode("utf-8")
        named_model = requested_model(self.path, body)
        if named_model is not None and routing_model(named_model) == "auto":
            self._send_json(
                400,
                {
                    "error": {
                        "type": "invalid_request_error",
                        "code": "explicit_model_required",
                        "message": "Name an explicit model so admission can enforce its lane.",
                    }
                },
                admission_reason="explicit_model_required",
            )
            return
        body, agent_payload_normalized = normalize_cross_provider_agent_messages(
            self.path, body
        )
        if agent_payload_normalized:
            logging.info(
                "protocol_normalized model=%s kind=codex_agent_message %s",
                named_model,
                self._trace_context,
            )
        if unsupported_encrypted_agent_payload(self.path, body):
            reason = "encrypted_agent_payload_unsupported"
            self._send_json(
                422,
                {
                    "error": {
                        "type": "invalid_request_error",
                        "code": reason,
                        "message": (
                            "This model route cannot decode encrypted inter-agent task "
                            "content. No upstream request was sent. Stop and inspect "
                            "host delegation compatibility; do not retry or substitute models."
                        ),
                    },
                    "upstream_contacted": False,
                    "task_mode": "encrypted_agent_message",
                    "route_capability": "unsupported",
                },
                admission_reason=reason,
            )
            logging.info(
                "protocol_reject model=%s reason=%s %s",
                named_model,
                reason,
                self._trace_context,
            )
            return
        if unsupported_user_image(self.path, body, proxy.text_only_models):
            reason = "user_image_input_unsupported"
            self._send_json(
                422,
                {
                    "error": {
                        "type": "invalid_request_error",
                        "code": reason,
                        "message": (
                            "This model route accepts text only, but this request "
                            "includes a user image in the current message or chat "
                            "history. No upstream request was sent. Start a text-only "
                            "chat, or explicitly select a model that supports images "
                            "to preserve this history. Retrying unchanged will fail."
                        ),
                    },
                    "upstream_contacted": False,
                    "task_mode": "user_image_input",
                    "route_capability": "text_only",
                },
                admission_reason=reason,
            )
            logging.info(
                "protocol_reject model=%s reason=%s %s",
                named_model,
                reason,
                self._trace_context,
            )
            return
        selection = requested_lane(self.path, body, config)
        lane_name = selection[0] if selection is not None else None
        model = selection[1] if selection is not None else None
        # Journal the model the client actually named even when no lane owns the
        # request. `model` stays lane-scoped because the lease, the probe flag
        # and the breaker must keep reading it that way; only the log lines use
        # the wider view, so a pass-through 5xx can be attributed to a model.
        observed_model = model or requested_model(self.path, body)
        if lane_name == "antigravity-oauth" and model is None:
            # Some streaming retries arrive through a lane-owned path whose
            # catalog selection omitted the model even though the request
            # body still names it. Preserve that model on the lease so a
            # capacity response cannot fall back to the shared breaker.
            model = observed_model
        body, omitted_historical_images = omit_antigravity_historical_user_images(
            self.path, body, lane_name
        )
        if omitted_historical_images:
            logging.info(
                "protocol_normalized model=%s kind=antigravity_historical_image "
                "omitted=%s %s",
                observed_model,
                omitted_historical_images,
                self._trace_context,
            )
        body, normalized_image_details = normalize_antigravity_image_detail(
            self.path, body, lane_name
        )
        if normalized_image_details:
            logging.info(
                "protocol_normalized model=%s kind=antigravity_image_detail "
                "original_to_high=%s %s",
                observed_model,
                normalized_image_details,
                self._trace_context,
            )
        body, stripped_notices = strip_antigravity_model_switch_items(
            self.path, body, lane_name
        )
        if stripped_notices:
            notice_counts = " ".join(
                f"{tag}={count}" for tag, count in sorted(stripped_notices.items())
            )
            logging.info(
                "protocol_normalized model=%s kind=antigravity_developer_notice %s %s",
                observed_model,
                notice_counts,
                self._trace_context,
            )
        body, dev_context_stats = normalize_antigravity_developer_context(
            self.path, body, lane_name
        )
        if dev_context_stats:
            context_counts = " ".join(
                f"{key}={count}" for key, count in sorted(dev_context_stats.items())
            )
            logging.info(
                "protocol_normalized model=%s kind=antigravity_developer_context %s %s",
                observed_model,
                context_counts,
                self._trace_context,
            )
        body, collapsed_retry_turns = collapse_antigravity_retry_user_turns(
            self.path, body, lane_name
        )
        if collapsed_retry_turns:
            logging.info(
                "protocol_normalized model=%s kind=antigravity_retry_history "
                "duplicate_user_turns=%s %s",
                observed_model,
                collapsed_retry_turns,
                self._trace_context,
            )
        body, identity_text_stats = neutralize_agent_identity_text(
            self.path, body, lane_name
        )
        if identity_text_stats:
            identity_counts = " ".join(
                f"{key}={count}" for key, count in sorted(identity_text_stats.items())
            )
            logging.info(
                "protocol_normalized model=%s kind=%s %s %s",
                observed_model,
                (
                    "antigravity_identity_user_text"
                    if lane_name == "antigravity-oauth"
                    else "agent_identity_neutralized"
                ),
                identity_counts,
                self._trace_context,
            )
        if lane_name == "antigravity-oauth":
            repeat_remaining = antigravity_repeat_reject_remaining(
                getattr(self, "_client_request_hash", None),
                body=body,
            )
            if repeat_remaining is not None:
                reason = "repeat_rejected_cooldown"
                self._send_json(
                    429,
                    {
                        "error": {
                            "type": "official_account_admission_circuit_open",
                            "code": reason,
                            "message": (
                                "Upstream repeatedly rejected this exact request; "
                                f"admission is pausing it for {repeat_remaining}s to "
                                "protect the lane. Modify the request or start a "
                                "fresh conversation instead of retrying it unchanged."
                            ),
                        },
                        "upstream_contacted": False,
                    },
                    retry_after=repeat_remaining,
                    admission_reason=reason,
                )
                logging.info(
                    "lane_reject lane=%s model=%s reason=%s retry_after=%s "
                    "waited_ms=0 %s",
                    lane_name,
                    observed_model or "other",
                    reason,
                    repeat_remaining,
                    self._trace_context,
                )
                return
        lane_config = proxy.lane_config[lane_name] if lane_name is not None else None
        lease: Lease | None = None
        response_started = False
        response_is_sse = False
        early_stream = (
            lane_name is not None
            and lane_config is not None
            and requested_stream(self.path, body)
        )
        body_length = len(body)
        body_source: Any = body
        body_spool: Any = None

        def close_body_spool() -> None:
            nonlocal body_spool
            if body_spool is not None:
                body_spool.close()
                body_spool = None

        if (
            lane_name is not None
            and body_length > ADMISSION_REQUEST_BODY_MEMORY_LIMIT_BYTES
        ):
            body_spool = tempfile.TemporaryFile(mode="w+b")
            body_spool.write(body)
            body_spool.seek(0)
            body_source = body_spool
            # Do not retain the large in-memory request while the lane FIFO
            # waits. The file object is closed on every early refusal and in
            # the upstream cleanup path below.
            del body
        queue_client_lost = threading.Event()
        if lane_name is not None and lane_config is not None:
            queue_heartbeat_stop = threading.Event()
            queue_heartbeat_thread: threading.Thread | None = None

            def queue_alive() -> bool:
                return not queue_client_lost.is_set() and self._downstream_alive()

            def queue_heartbeat_loop() -> None:
                while not queue_heartbeat_stop.wait(proxy.heartbeat_interval):
                    if not queue_alive():
                        queue_client_lost.set()
                        return
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (OSError, ValueError):
                        queue_client_lost.set()
                        return

            try:
                if early_stream:
                    self._begin_queued_stream()
                    response_started = True
                    queue_heartbeat_thread = threading.Thread(
                        target=queue_heartbeat_loop, daemon=True
                    )
                    queue_heartbeat_thread.start()
                    lease = (
                        proxy.lanes[lane_name].acquire_after_reset(expected_generation)
                        if expected_generation is not None
                        else proxy.lanes[lane_name].acquire(queue_alive, model=model)
                    )
                else:
                    lease = (
                        proxy.lanes[lane_name].acquire_after_reset(expected_generation)
                        if expected_generation is not None
                        else proxy.lanes[lane_name].acquire(
                            self._downstream_alive, model=model
                        )
                    )
            except DownstreamClientDisconnected:
                self.close_connection = True
                close_body_spool()
                logging.info(
                    "downstream_disconnect lane=%s model=%s stage=queue_headers %s",
                    lane_name,
                    observed_model or "other",
                    self._trace_context,
                )
                return
            finally:
                queue_heartbeat_stop.set()
                if queue_heartbeat_thread is not None:
                    queue_heartbeat_thread.join(timeout=2)
            if queue_client_lost.is_set():
                close_body_spool()
                if lease is not None and lease.admitted:
                    proxy.lanes[lane_name].release(
                        lease,
                        capacity_error=False,
                        retry_after=None,
                        successful=False,
                    )
                self.close_connection = True
                logging.info(
                    "downstream_disconnect lane=%s model=%s stage=queue %s",
                    lane_name,
                    observed_model or "other",
                    self._trace_context,
                )
                return
            if not lease.admitted:
                close_body_spool()
                manual_hold = lease.reason == "operator_review_required"
                if manual_hold:
                    message = (
                        f"Shared upstream lane {lane_name} requires operator review."
                    )
                elif lease.reason == "quota_group_cooldown":
                    message = (
                        f"Antigravity quota group for {model} is cooling down; "
                        f"retry after {lease.retry_after}s."
                    )
                elif lease.reason == "model_cooldown":
                    message = (
                        f"Antigravity model {model} is cooling down; "
                        f"retry after {lease.retry_after}s. Sibling models "
                        f"on the lane remain available."
                    )
                elif lease.reason == "rate_limited":
                    message = (
                        f"Shared upstream lane {lane_name} is at its local "
                        f"request-rate guard; retry after {lease.retry_after}s."
                    )
                elif lease.reason == "busy":
                    message = (
                        f"Shared upstream lane {lane_name} has a full queue; "
                        f"retry after {lease.retry_after}s."
                    )
                else:
                    message = (
                        f"Shared upstream lane {lane_name} is cooling down or "
                        f"busy; retry after {lease.retry_after}s."
                    )
                if early_stream:
                    self._send_stream_failure(
                        code="operator_review_required"
                        if manual_hold
                        else lease.reason,
                        message=message,
                        retry_after=None if manual_hold else lease.retry_after,
                    )
                else:
                    self._send_json(
                        409 if reset_probe else (403 if manual_hold else 429),
                        {
                            "error": {
                                "message": message,
                                "type": "official_account_admission_circuit_open",
                                "code": "operator_review_required"
                                if manual_hold
                                else "shared_upstream_capacity",
                            }
                        },
                        retry_after=None if manual_hold else lease.retry_after,
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
            # Journal the admission moment itself: cpa_natural_traffic_audit
            # pairs lane_admit with the request's probe/http lines to measure
            # true admit peaks. Without this line the audit can only derive
            # admission times from SSE terminals, which undercounts on
            # non-stream traffic (the dead-contract finding of 2026-11-10).
            logging.info(
                "lane_admit lane=%s model=%s waited_ms=%s %s",
                lane_name,
                model,
                lease.waited_ms,
                self._trace_context,
            )
            if lease.probe:
                # An early half-open probe, an expired-cooldown probe, or a
                # per-model probe is a real client request verifying recovery;
                # mark it (and which breaker it is testing) so the journal can
                # pair the probe with its upstream_result line.
                logging.info(
                    "lane_probe lane=%s model=%s reason=%s scope=%s %s",
                    lane_name,
                    model,
                    lease.reason,
                    "model" if lease.model_probe else "lane",
                    self._trace_context,
                )
        capacity_error = False
        capacity_kind: str | None = None
        antigravity_quota_check = False
        antigravity_park_on_release = False
        antigravity_credential_switch = False
        successful = False
        retry_after: int | None = None
        hold_reason: str | None = None
        forward_upstream_stream = False
        upstream_started = time.monotonic()
        upstream_header_ms: int | None = None
        upstream_first_byte_ms: int | None = None
        stream_probe: StreamErrorProbe | None = None
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
                and key.lower() not in STRIPPED_REQUEST_HEADERS
                and key.lower() != "host"
            }
            headers["Host"] = f"{config['upstream_host']}:{config['upstream_port']}"
            headers["Content-Length"] = str(body_length)
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
            conn.request(self.command, self.path, body=body_source, headers=headers)
            response = conn.getresponse()
            upstream_header_ms = int((time.monotonic() - upstream_started) * 1000)
            retry_after_header = response.getheader("Retry-After")
            retry_after = parse_retry_after(retry_after_header)
            if lane_name == "claude-oauth":
                # Error events can arrive inside HTTP 200 JSON/SSE responses.
                # Preserve the longest provider deadline before inspecting them.
                retry_after = claude_retry_after(response.headers)
                if retry_after is not None:
                    retry_after_header = str(retry_after)
            if lane_name == "claude-oauth" and response.status in (429, 503, 529):
                if response.status == 429 and retry_after is None:
                    # CPA v8.0.31 may consume the provider deadline without
                    # forwarding it (#6700). Never invent a short reset time.
                    hold_reason = "retry_window_review_required"
                proxy.lanes[lane_name].record_risk(
                    hold_reason,
                    retry_after or lane_config["cooldown_schedule_seconds"][0],
                )
                if retry_after is not None:
                    retry_after_header = str(retry_after)
            elif lane_name in WORKBUDDY_LANES and response.status in (
                429,
                503,
                529,
            ):
                # Do not arm a lane cooldown from the status/header alone.
                # WorkBuddy may put a model-only 6004 in the bounded response:
                # pre-arming here would leave the lane shut even after the body
                # later narrows the breaker to that model. The one-inflight
                # lane keeps concurrent work queued until final classification,
                # so release() remains the single authority for scope.
                if retry_after is not None:
                    retry_after_header = str(retry_after)
            if lane_name == "antigravity-oauth":
                if response.status == 429:
                    proxy.lanes[lane_name].begin_quota_check()
                    antigravity_quota_check = True
                    antigravity_park_on_release = True
                elif response.status == 503:
                    proxy.lanes[lane_name].record_risk(
                        None, retry_after or lane_config["cooldown_schedule_seconds"][0]
                    )
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
            response_is_sse = "text/event-stream" in content_type
            upstream_content_encoding = (
                (response.getheader("Content-Encoding") or "").strip().lower()
            )
            if upstream_content_encoding not in ("", "identity"):
                # Defense in depth (2026-11-04): this proxy strips
                # Accept-Encoding before CPA, so an encoded response can only
                # mean a config or upstream regression. The downstream headers
                # are re-framed here and do not faithfully re-issue
                # Content-Encoding on every path, so silently forwarding the
                # bytes would hand clients undetectable gzip - the failure
                # behind the 2026-11-04 DSH full-protocol streaming outage.
                # Fail closed with a relayable error instead.
                logging.warning(
                    "upstream_content_encoding_rejected encoding=%s status=%s "
                    "lane=%s model=%s %s",
                    upstream_content_encoding,
                    response.status,
                    lane_name,
                    observed_model or "other",
                    self._trace_context,
                )
                if early_stream:
                    self._send_stream_failure(
                        code="upstream_content_encoding",
                        message=(
                            "Upstream returned an encoded stream this proxy "
                            "does not relay."
                        ),
                        retry_after=None,
                    )
                else:
                    self._send_json(
                        502,
                        {
                            "error": {
                                "message": (
                                    "Upstream returned an encoded response "
                                    "this proxy does not relay."
                                ),
                                "type": "server_error",
                                "code": "upstream_content_encoding",
                            }
                        },
                        admission_reason="upstream_content_encoding",
                    )
                return
            forward_upstream_stream = (
                early_stream and 200 <= response.status < 300 and response_is_sse
            )
            # Early queue headers are close-delimited. Otherwise preserve the
            # existing chunked framing for every SSE/native chunked response.
            downstream_chunked = not early_stream and (
                response_is_chunked or response_is_sse
            )
            if not early_stream:
                try:
                    self.send_response(response.status, response.reason)
                    for key, value in response.getheaders():
                        if (
                            key.lower() in HOP_BY_HOP_HEADERS
                            or key.lower() in GENERATED_RESPONSE_HEADERS
                            or key.lower() in CPA_TRACE_HEADERS
                            or (
                                lane_name == "claude-oauth"
                                and retry_after is not None
                                and key.lower() in {"retry-after", "retry-after-ms"}
                            )
                        ):
                            continue
                        self.send_header(key, value)
                    if lane_name == "claude-oauth" and retry_after is not None:
                        self.send_header("Retry-After", str(retry_after))
                    self._send_trace_headers()
                    if downstream_chunked:
                        # http.client hands over decoded body bytes without
                        # framing. Recreate chunked framing for the downstream
                        # HTTP/1.1 client - nginx must never wait for a
                        # connection close on a live stream - including for SSE
                        # responses re-framed from a close-delimited upstream.
                        self.send_header("Transfer-Encoding", "chunked")
                    elif body_allowed and response.getheader("Content-Length") is None:
                        # Close-delimited upstream responses need an explicit
                        # framing signal on the sidecar connection.
                        self.send_header("Connection", "close")
                        self.close_connection = True
                    self.end_headers()
                except (OSError, ValueError) as exc:
                    raise DownstreamClientDisconnected(
                        "downstream client disconnected before response headers"
                    ) from exc
                response_started = True
            probe = bytearray()
            stream_probe = (
                StreamErrorProbe(
                    config["stream_probe_bytes"],
                    lane_config,
                    response.status,
                    self.path,
                    retry_after=retry_after,
                    started_at=upstream_started,
                )
                if "text/event-stream" in content_type
                else None
            )
            latched_risk: tuple[str | None, int | None] = (hold_reason, None)
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
                            frame = b"".join(
                                (
                                    f"{len(payload):X}\r\n".encode("ascii"),
                                    payload,
                                    b"\r\n",
                                )
                            )
                            self.wfile.write(frame)
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

            def _inspect(chunk: bytes) -> None:
                nonlocal hold_reason, retry_after, latched_risk
                if len(probe) < config["probe_bytes"]:
                    probe.extend(chunk[: config["probe_bytes"] - len(probe)])
                if stream_probe is None and lane_name in DURABLE_OAUTH_LANES:
                    # Wait for a complete error envelope so an invalid gateway
                    # client key cannot poison the shared OAuth circuit. Latch
                    # before forwarding it, even if the client disconnects.
                    try:
                        json.loads(probe)
                    except (ValueError, UnicodeDecodeError):
                        pass
                    else:
                        signal_hold, _ = oauth_risk_signal(
                            lane_name,
                            response.status,
                            bytes(probe),
                            known_retry=retry_after is not None,
                        )
                        if signal_hold:
                            hold_reason = signal_hold
                            proxy.lanes[lane_name].record_risk(hold_reason, None)
                if stream_probe is not None:
                    stream_probe.feed(chunk)
                    if lane_name in DURABLE_OAUTH_LANES:
                        signal_delay = stream_probe.retry_after
                        if (
                            signal_delay is None
                            and stream_probe.scope is not None
                            and lane_name in STRICT_OAUTH_LANES
                        ):
                            signal_delay = lane_config["cooldown_schedule_seconds"][0]
                        signal = (stream_probe.hold_reason, signal_delay)
                        if signal != latched_risk and any(
                            value is not None for value in signal
                        ):
                            hold_reason = stream_probe.hold_reason or hold_reason
                            if signal_delay is not None:
                                retry_after = max(retry_after or 0, signal_delay)
                            proxy.lanes[lane_name].record_risk(hold_reason, retry_after)
                            latched_risk = signal

            def _forward(chunk: bytes) -> None:
                nonlocal last_forward, upstream_first_byte_ms
                try:
                    _inspect(chunk)
                    if upstream_first_byte_ms is None:
                        # Measure the first upstream payload, excluding the
                        # downstream SSE heartbeat comments emitted while the
                        # provider is still silent.
                        upstream_first_byte_ms = int(
                            (time.monotonic() - upstream_started) * 1000
                        )
                    if early_stream and not forward_upstream_stream:
                        # The committed response can only carry an SSE terminal
                        # event. Inspect the real upstream error body for risk
                        # and capacity classification, then synthesize the
                        # terminal after the body is complete.
                        return
                    try:
                        _write_chunk(chunk)
                    except DownstreamClientDisconnected:
                        client_lost.set()
                        raise
                except (OSError, http.client.HTTPException):
                    # Non-SSE processing can fail while the client resets.
                    # Check downstream liveness before assigning the origin.
                    if (
                        not response_is_sse
                        and not client_lost.is_set()
                        and not self._downstream_alive()
                    ):
                        client_lost.set()
                    raise
                last_forward = time.monotonic()

            reader: threading.Thread | None = None
            reader_stop = threading.Event()
            try:
                if response_is_sse:
                    # At most 512 KiB queued per stream. A slow downstream
                    # applies backpressure instead of buffering the whole turn.
                    events: queue.Queue[tuple[bytes, BaseException | None]] = (
                        queue.Queue(maxsize=SSE_QUEUE_CHUNKS)
                    )
                    reader = threading.Thread(
                        target=_read_sse_upstream,
                        args=(response, events, reader_stop),
                        daemon=True,
                    )
                    reader.start()
                    while True:
                        # Bounded wait on the queue keeps the loop responsive:
                        # every slice re-checks liveness, so a vanished client
                        # releases the lane lease within one heartbeat slice
                        # instead of waiting for the upstream to speak.
                        if client_lost.is_set() or _client_gone():
                            client_lost.set()
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
                            client_lost.set()
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
                    # once the body ends. A read failure can nevertheless
                    # coincide with a downstream reset, so it too must check
                    # the client before the shared handler attributes it to
                    # the upstream.
                    while True:
                        try:
                            chunk = response.read1(65536)
                        except (OSError, http.client.HTTPException) as exc:
                            if not self._downstream_alive():
                                client_lost.set()
                                raise DownstreamClientDisconnected(
                                    "downstream client disconnected during upstream read"
                                ) from exc
                            raise
                        if not chunk:
                            break
                        _forward(chunk)
                if body_allowed and response.length is not None and response.length > 0:
                    # HTTPResponse.read1() can return EOF without raising when
                    # a fixed-length upstream body ends early. Treat the
                    # remainder as a torn response so it cannot be logged as a
                    # completed result or clear a lane's recovery breaker.
                    raise http.client.IncompleteRead(b"", response.length)
            finally:
                reader_stop.set()
                heartbeat_stop.set()
                if heartbeat_thread is not None:
                    heartbeat_thread.join(timeout=2)
            if stream_probe is not None:
                stream_probe.finish()
            if downstream_chunked and not client_lost.is_set():
                try:
                    with write_lock:
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                except (OSError, ValueError) as exc:
                    raise DownstreamClientDisconnected(
                        "downstream client disconnected before response end"
                    ) from exc
            capacity_kind = (
                capacity_scope(
                    response.status,
                    b"" if stream_probe is not None else bytes(probe),
                    retry_after_header,
                    lane_config,
                )
                if lease is not None and lane_config is not None
                else None
            )
            if stream_probe is not None and stream_probe.has_error:
                capacity_kind = stream_probe.scope
            # A credential-scoped signal on a pooled lane must not become a
            # alias-wide park. `deepseek-v4.1-flash` is declared as a pooled
            # alias (WorkBuddy primary, OpenCode backup) and this gateway gates
            # by model name, so a lane-wide park of hours also deletes the
            # healthy backup route and every sibling credential -- the exact
            # 2026-11-06 shape that made one account's 6004 reset the whole
            # alias. Keep the cooling signal, clamp its magnitude so the next
            # attempt reaches a sibling credential or the backup provider; only
            # standing restriction holds (account_restricted,
            # authentication_required) park the lane for review.
            pooled_models = frozenset((lane_config or {}).get("pooled_models") or ())
            requested_name = routing_model(observed_model or "")
            pooled_lane = bool(
                pooled_models and requested_name.strip().lower() in pooled_models
            )
            body_delay = None
            if lane_name in DURABLE_OAUTH_LANES:
                body_hold, body_delay = oauth_risk_signal(
                    lane_name,
                    response.status,
                    bytes(probe) if stream_probe is None else b"",
                    known_retry=retry_after is not None,
                )
                hold_reason = body_hold or hold_reason
                if stream_probe is not None:
                    hold_reason = hold_reason or stream_probe.hold_reason
                    body_delay = stream_probe.retry_after
                if body_delay is not None:
                    retry_after = max(retry_after or 0, body_delay)
                    capacity_kind = "lane"
            if (
                pooled_lane
                and lane_config is not None
                and capacity_kind == "lane"
                and hold_reason is None
                and _generic_capacity_scope(
                    response.status if stream_probe is not None else 200,
                    b"" if stream_probe is not None else bytes(probe),
                    retry_after_header,
                    lane_config,
                )
                is not None
            ):
                # Delay magnitude stays bounded; the scope does not widen to
                # the alias. The re-test above drops the pooled marker so only
                # a signal that was already capacity-shaped on the generic path
                # is narrowed -- 6003 stays the lane-wide credential signal it
                # has always been. See the comment above the durable-lane branch.
                capacity_kind = "model"
                retry_after = min(
                    retry_after or POOLED_LANE_MODEL_COOLDOWN_SECONDS,
                    POOLED_LANE_MODEL_COOLDOWN_SECONDS,
                )
            if stream_probe is not None:
                # Preserve the model scope of 6004 while carrying its reset
                # deadline into LaneState.release().  The generic
                # stream_probe.retry_after field is reserved for lane-level
                # OAuth signals and would otherwise widen the breaker.
                if stream_probe.model_retry_after is not None:
                    retry_after = max(retry_after or 0, stream_probe.model_retry_after)
            elif (
                lane_name in WORKBUDDY_LANES
                and capacity_kind == "model"
                and retry_after is None
            ):
                retry_after = workbuddy_6004_retry_after(
                    response.status, bytes(probe), lane_config
                )
            if antigravity_quota_check:
                # Default: a model-bearing Antigravity 429 parks only that
                # model (the user-directed no-collective-cooldown contract).
                # A verified Cloud Code envelope upgrades the verdict: a
                # model-capacity reason with a body reset arms the durable
                # per-model quota key, and the per-user rate limiter reasons
                # stay account-level ("account" scopes the shared lane
                # breaker and is exempt from the lane-to-model narrowing in
                # release()).
                capacity_kind = "model" if lease is not None else "lane"
                envelope_scope = (
                    antigravity_envelope_scope(bytes(probe), lease.model)
                    if lease is not None
                    and stream_probe is None
                    and not client_lost.is_set()
                    and hold_reason is None
                    else None
                )
                if envelope_scope == "quota_group" and not (
                    body_delay is not None
                    and retry_after == body_delay
                    and 0 < body_delay <= 2147483647
                ):
                    # The durable key is only armable from a body-advertised
                    # reset; without it the in-memory model window applies.
                    envelope_scope = None
                if envelope_scope is not None:
                    capacity_kind = envelope_scope
                elif (
                    stream_probe is None
                    and hold_reason is None
                    and antigravity_credential_switch_429(response.status, bytes(probe))
                ):
                    # CPA's local single-credential rotation 429 ("auth in
                    # short cooldown, ... remaining" or "antigravity daily
                    # credential cap reached, ... seconds remaining") tells
                    # conductor to cool that one credential and switch to a
                    # healthy sibling on the next attempt. Do not lock the
                    # model across all credentials or arm repeat-reject, and
                    # bound the downstream retry hint to 1s for fast failover.
                    capacity_kind = None
                    antigravity_park_on_release = False
                    antigravity_credential_switch = True
                    retry_after = 1
                elif (
                    capacity_kind == "model"
                    and retry_after_header is None
                    and antigravity_bare_resource_exhausted(
                        response.status, bytes(probe)
                    )
                ):
                    # Cloud Code's bare RESOURCE_EXHAUSTED response has no
                    # credential, model, or reset scope. A process-wide
                    # model cooldown would let one stale conversation block
                    # every other session using a healthy credential. Preserve
                    # the upstream 429, but leave retry timing to the caller
                    # and the provider's credential selector. The request id
                    # still joins the repeat-reject counter so an unchanged
                    # resubmission loop pauses before it escalates upstream.
                    capacity_kind = None
                    record_antigravity_repeat_reject_arm(
                        getattr(self, "_client_request_hash", None),
                        observed_model,
                        body=body,
                    )
            capacity_error = capacity_kind is not None
            successful = (
                200 <= response.status < 300
                and not capacity_error
                and (not early_stream or forward_upstream_stream)
                and (
                    stream_probe.complete
                    and stream_probe.terminal_success
                    and not stream_probe.has_error
                    and not stream_probe.malformed_event
                    if stream_probe is not None
                    else not protocol_errors(bytes(probe), lane_name)
                )
                and not client_lost.is_set()
                and (not reset_probe or completed_reset_probe(bytes(probe)))
            )
            result_reason = protocol_result_reason(
                status=response.status,
                successful=successful,
                capacity_error=capacity_error,
                client_lost=client_lost.is_set(),
                stream_probe=stream_probe,
                body_prefix=bytes(probe),
                lane_name=lane_name,
            )
            # Observation only: a failed or unparsed request must never inflate
            # a model's cache ratio. The snapshot therefore also carries the
            # sample count, so a ratio is always read against its denominator.
            proxy.telemetry.record(
                observed_model or "other",
                *usage_from_body(bytes(probe))
                if stream_probe is None
                else (
                    stream_probe.usage_input,
                    stream_probe.usage_cached,
                    stream_probe.usage_output,
                ),
                stream_probe.first_text_ms if stream_probe is not None else None,
                result_reason,
            )
            if stream_probe is not None:
                usage_in, usage_cached, usage_out = (
                    stream_probe.usage_input,
                    stream_probe.usage_cached,
                    stream_probe.usage_output,
                )
                usage_cost_usd = stream_probe.usage_cost_usd
            else:
                usage_in, usage_cached, usage_out = usage_from_body(bytes(probe))
                usage_cost_usd = usage_cost(bytes(probe))
            terminal_delivered = (
                forward_upstream_stream
                and stream_probe is not None
                and stream_probe.complete
                and not stream_probe.malformed_event
                and (stream_probe.terminal_success or stream_probe.has_error)
            )
            if early_stream and not client_lost.is_set() and not terminal_delivered:
                if antigravity_credential_switch:
                    message = (
                        "Antigravity credential rotated locally after short cooldown "
                        "or daily cap; retry after 1 second to bind a healthy sibling "
                        "credential."
                    )
                elif lane_name == "antigravity-oauth" and response.status == 429:
                    message = "Antigravity upstream returned HTTP 429."
                    if retry_after is None:
                        message += " CPA did not receive a retry time."
                    else:
                        message += f" Retry after {retry_after} seconds."
                elif (
                    stream_probe is not None
                    and stream_probe.has_error
                    and stream_probe.last_event_type
                    in ("response.failed", "response.incomplete")
                ):
                    message = (
                        "CPA received an upstream stream error before completion "
                        f"(last event: {stream_probe.last_event_type}). "
                        "This is an upstream provider failure, not a local "
                        "cooldown or admission refusal."
                    )
                elif stream_probe is not None and stream_probe.last_event_type in (
                    "response.in_progress",
                    "response.created",
                    "response.output_item.added",
                    "response.output_item.done",
                ):
                    message = (
                        "CPA upstream stream closed before a terminal event "
                        f"(last event: {stream_probe.last_event_type}). "
                        "This is an upstream provider failure, not a local "
                        "cooldown or admission refusal."
                    )
                else:
                    message = (
                        "CPA could not deliver a complete streaming response. "
                        f"Upstream status={response.status}."
                    )
                self._send_stream_failure(
                    code=(
                        result_reason
                        if result_reason != "completed"
                        else "stream_protocol_error"
                    ),
                    message=message,
                    retry_after=retry_after,
                )
            if reset_probe:
                logging.info(
                    "reset_probe_result lane=%s model=%s successful=%s "
                    "generation=%s %s",
                    lane_name,
                    observed_model,
                    str(successful).lower(),
                    lease.generation if lease is not None else -1,
                    self._trace_context,
                )
            logging.info(
                "upstream_result lane=%s model=%s status=%s capacity=%s "
                "retry_after=%s waited_ms=%s successful=%s protocol_result=%s "
                "upstream_header_ms=%s upstream_first_byte_ms=%s "
                "first_text_ms=%s "
                "sse_bytes=%s sse_events=%s sse_last_event=%s "
                "sse_inspection_complete=%s sse_malformed_json=%s "
                "sse_terminal_seen=%s sse_text_finish=%s "
                "usage_in=%s usage_cached=%s usage_out=%s usage_cost_usd=%s %s",
                lane_name or "passthrough",
                observed_model or "other",
                response.status,
                str(capacity_error).lower(),
                "present" if retry_after_header else "absent",
                lease.waited_ms if lease is not None else 0,
                str(successful).lower(),
                result_reason,
                upstream_header_ms if upstream_header_ms is not None else "na",
                upstream_first_byte_ms if upstream_first_byte_ms is not None else "na",
                # "na" for every absent value. A probe that measured nothing
                # left a literal "None" here, so one field carried two spellings
                # for one state (4123 None vs 3757 na in a 24h window). Readers
                # that only recognised "na" still fell back to a ValueError
                # parse, so no sample was lost -- but a grep for na= under-
                # counted the unavailable set, which is exactly the number an
                # operator uses to judge whether a latency reading is usable.
                (
                    stream_probe.first_text_ms
                    if stream_probe is not None
                    and stream_probe.first_text_ms is not None
                    else "na"
                ),
                stream_probe.bytes_received if stream_probe is not None else 0,
                stream_probe.event_count if stream_probe is not None else 0,
                stream_probe.last_event_type if stream_probe is not None else "none",
                str(stream_probe.complete).lower()
                if stream_probe is not None
                else "na",
                str(stream_probe.malformed_event).lower()
                if stream_probe is not None
                else "na",
                str(stream_probe.terminal_success).lower()
                if stream_probe is not None
                else "na",
                stream_probe.output_finish_reason if stream_probe is not None else "na",
                "na" if usage_in is None else usage_in,
                "na" if usage_cached is None else usage_cached,
                "na" if usage_out is None else usage_out,
                "na" if usage_cost_usd is None else f"{usage_cost_usd:.8f}",
                self._trace_context,
            )
        except DownstreamClientDisconnected:
            self.close_connection = True
            # A client-side RST/BrokenPipe is not evidence that the shared
            # upstream account is overloaded. Do not open or extend a lane
            # cooldown for a request that the caller abandoned.
            capacity_error = False
            capacity_kind = None
            antigravity_park_on_release = False
            logging.info(
                "downstream_disconnect lane=%s model=%s waited_ms=%s "
                "sse_bytes=%s sse_events=%s sse_last_event=%s "
                "sse_inspection_complete=%s sse_malformed_json=%s "
                "sse_terminal_seen=%s %s",
                lane_name or "passthrough",
                observed_model or "other",
                lease.waited_ms if lease is not None else 0,
                stream_probe.bytes_received if stream_probe is not None else 0,
                stream_probe.event_count if stream_probe is not None else 0,
                stream_probe.last_event_type if stream_probe is not None else "none",
                str(stream_probe.complete).lower()
                if stream_probe is not None
                else "na",
                str(stream_probe.malformed_event).lower()
                if stream_probe is not None
                else "na",
                str(stream_probe.terminal_success).lower()
                if stream_probe is not None
                else "na",
                self._trace_context,
            )
        except (OSError, http.client.HTTPException) as exc:
            # Headers already sent cannot be replaced with a JSON error. Close
            # the incomplete downstream response instead of leaving keep-alive
            # waiting for a terminator until the client's own timeout expires.
            if response_started:
                self.close_connection = True
            # Distinguish "the upstream transport itself failed" from "the
            # upstream answered and told us it is out of capacity". Only the
            # latter is capacity evidence. A connect/read timeout, a reset, or
            # a truncated response is transport jitter that historically made
            # the breaker advance on network noise alone, so it is reported
            # and served a bounded Retry-After but does not move the streak.
            transport_failure = not response_started
            capacity_error = False
            capacity_kind = None
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
            elif early_stream and not client_lost.is_set():
                try:
                    self._send_stream_failure(
                        code="upstream_transport_error",
                        message="CPA upstream is temporarily unavailable.",
                        retry_after=60 if lease else None,
                    )
                except DownstreamClientDisconnected:
                    client_lost.set()
            elif response_started and not response_is_sse and client_lost.is_set():
                # The response headers made it downstream, but its client
                # reset before the JSON body arrived. This is not upstream
                # capacity evidence and no further bytes can be written.
                logging.info(
                    "downstream_disconnect lane=%s model=%s waited_ms=%s %s",
                    lane_name or "passthrough",
                    observed_model or "other",
                    lease.waited_ms if lease is not None else 0,
                    self._trace_context,
                )
            else:
                # A torn SSE stream is still reported as an incomplete
                # protocol result. Its terminal-success status must remain
                # false so it can never renew a cooldown as a success.
                successful = False
                capacity_error = False
                capacity_kind = None
        finally:
            close_body_spool()
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
                    # A torn or unreadable 429 still provides shared evidence.
                    capacity_error=capacity_error or antigravity_park_on_release,
                    retry_after=retry_after,
                    successful=successful,
                    capacity_scope=capacity_kind or "lane",
                    hold_reason=hold_reason,
                    quota_check=antigravity_quota_check,
                )

    def _downstream_alive(self) -> bool:
        """Report whether the client is still connected while this request queues.

        A queued request has already had its body read, so the socket is either
        silent (client waiting -- alive), readable with a pipelined next request
        (alive), or readable because the peer closed or reset (gone). Windows may
        accept sends into a reset connection without raising, so this peeks
        instead of writing. A non-empty buffer is reported alive because a
        pipelined request and a body-then-FIN look identical at the socket layer,
        while peeking does not consume the next request line.
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
            lengths = self.headers.get_all("Content-Length", [])
            encodings = self.headers.get_all("Transfer-Encoding", [])
            if len(lengths) > 1 or len(encodings) > 1 or (lengths and encodings):
                raise ValueError("ambiguous request body framing")
            transfer_encoding = (
                self.headers.get("Transfer-Encoding", "").strip().lower()
            )
            if encodings:
                if transfer_encoding != "chunked":
                    raise ValueError("unsupported transfer encoding")
                body = bytearray()
                while True:
                    line = self.rfile.readline(65537)
                    if not line.endswith(b"\r\n") or len(line) > 65536:
                        raise ValueError("invalid chunked request body")
                    size_text = line[:-2].split(b";", 1)[0].rstrip(b" \t")
                    if re.fullmatch(rb"[0-9a-fA-F]+", size_text) is None:
                        raise ValueError("invalid chunk size")
                    size = int(size_text, 16)
                    if size == 0:
                        trailer_bytes = 0
                        while True:
                            trailer = self.rfile.readline(65537)
                            trailer_bytes += len(trailer)
                            if not trailer.endswith(b"\r\n") or trailer_bytes > 65536:
                                raise ValueError("invalid chunked trailer")
                            if trailer == b"\r\n":
                                return bytes(body)
                    if len(body) + size > max_body_bytes:
                        raise ValueError("request body exceeds configured limit")
                    body.extend(self._read_exact(size))
                    if self._read_exact(2) != b"\r\n":
                        raise ValueError("invalid chunk delimiter")
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                return b""
            raw_length = raw_length.strip(" \t")
            if re.fullmatch(r"[0-9]+", raw_length) is None:
                raise ValueError("invalid content length")
            length = int(raw_length)
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
            if self.close_connection:
                self.send_header("Connection", "close")
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
            # The parser may have left body bytes unread. Closing prevents
            # those bytes from becoming another request on this connection.
            self.close_connection = True
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
            self.close_connection = True
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
    # socketserver's default listen backlog is 5, and `ss -ltn` showed
    # `LISTEN 0 5` on 8318. Measured 2026-11-08: one-at-a-time probes sustain
    # 447 req/s, but at 8-way concurrency throughput fell to 57 req/s and at
    # 32-way to 29 req/s with a client reset -- connections were waiting in a
    # five-deep kernel queue, not in admission's own lane queues. The desktop
    # opens a main turn plus auxiliary requests against a lane whose
    # max_inflight is 2, so a deeper backlog only bounds the brief burst before
    # the lane rejects; it does not admit more upstream work, because the lane
    # queue and its 120 s budget still decide that. 128 matches Nginx's
    # worker_connections headroom without changing any lane semantics.
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], proxy: AdmissionProxy) -> None:
        self.proxy = proxy
        super().__init__(address, Handler)


def main(
    _waiting_parent_pid: int | None = None,
    _waiting_parent_start: str = "",
    _bridge_fd: int | None = None,
    _guard_fd: int | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path, nargs="?")
    launched = _waiting_parent_pid is not None
    proxy_guard_fd: int | None = None
    launcher_receipt: dict[str, Any] = {}
    if launched:
        assert _waiting_parent_pid is not None
        reviewed_args = sys.argv[1:]
        if len(reviewed_args) < 6:
            parser.error("reviewed child arguments are invalid")
        expected_args = [
            str(Path(__file__).resolve()),
            str(Path(__file__).resolve().parent),
            str(_waiting_parent_pid),
            _waiting_parent_start,
        ]
        if (
            reviewed_args[:4] != expected_args
            or _guard_fd is None
            or _bridge_fd is None
            or _guard_fd == _bridge_fd
        ):
            parser.error(
                "reviewed child handoff is invalid "
                f"reviewed={reviewed_args[:6]!r} expected={expected_args!r} "
                f"bridge_fd={_bridge_fd} guard_fd={_guard_fd}"
            )
        try:
            bridge = socket.socket(fileno=_checked_launcher_fd(_bridge_fd, "bridge"))
            try:
                message = bridge.recv(1, socket.MSG_CMSG_CLOEXEC)
            finally:
                bridge.close()
        except (OSError, ValueError, TypeError):
            parser.error("reviewed child bridge handoff is invalid")
        if message != b"f":
            parser.error("reviewed child bridge handoff is invalid")
        try:
            proxy_guard_fd = _checked_launcher_fd(_guard_fd, "guard")
            launcher_receipt = _read_launcher_receipt(
                Path("/run/cpa-admission/runtime-token")
            )
        except (OSError, ValueError) as exc:
            parser.error(f"reviewed child launcher receipt is invalid: {exc}")
        if launcher_receipt["parent_pid"] != _waiting_parent_pid:
            parser.error("reviewed child launcher process binding is invalid")
        sys.argv = ["cpa-admission.py", *reviewed_args[6:]]
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--claude-state-file", type=Path)
    parser.add_argument("--chatgpt-state-file", type=Path)
    parser.add_argument("--workbuddy-state-file", type=Path)
    parser.add_argument("--workbuddy-intl-state-file", type=Path)
    args = parser.parse_args()
    if args.config is None:
        parser.error("config is required")
    config = load_config(args.config)
    state_files = {
        "antigravity-oauth": args.state_file,
        "claude-oauth": args.claude_state_file,
        "chatgpt-oauth": args.chatgpt_state_file,
        "workbuddy-oauth": args.workbuddy_state_file,
        "workbuddy-intl-oauth": args.workbuddy_intl_state_file,
    }
    for lane, path in state_files.items():
        if lane in config["model_lanes"].values() and path is None:
            parser.error(f"{lane} requires a durable circuit state file")
    paths = [path.resolve() for path in state_files.values() if path is not None]
    if len(paths) != len(set(paths)):
        parser.error("OAuth lanes require distinct circuit state files")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    proxy = AdmissionProxy(
        config,
        state_path=args.state_file,
        claude_state_path=args.claude_state_file,
        chatgpt_state_path=args.chatgpt_state_file,
        workbuddy_state_path=args.workbuddy_state_file,
        workbuddy_intl_state_path=args.workbuddy_intl_state_file,
        traffic_lock_path=Path("/run/cpa-admission/requests.lock"),
    )
    # Bind the listening socket before the startup receipt so the bootstrap
    # never publishes an admission that is already exiting. serve_forever
    # remains the first point at which traffic is accepted.
    server = Server((config["listen_host"], config["listen_port"]), proxy)
    process_marker = os.environ.get("VPS_SSH_LAUNCHER_ADMISSION_PROCESS_MARKER", "")
    if process_marker:
        proxy.request_guard_broker = RequestGuardBroker(
            proxy.traffic_lock_path,
            proxy_guard_fd,
            parent_pid=_waiting_parent_pid or 0,
            parent_start=_waiting_parent_start,
            guard_pid=int(launcher_receipt.get("guard_pid", 0)),
            guard_start=str(launcher_receipt.get("guard_start", "")),
            admission_pid=os.getpid() if launched else 0,
            admission_start=_linux_process_start(os.getpid()) or "",
        )
        proxy.request_guard_broker.start()
    # Create before reporting ready so an updater never locks a different inode.
    assert proxy.traffic_lock_path is not None
    with proxy.traffic_lock_path.open("a"):
        pass
    if launched:
        receipt_path = Path("/run/cpa-admission/runtime-receipt")
        runtime = {
            "token": launcher_receipt["token"],
            "identity": Path("/run/cpa-admission/startup-generation")
            .read_text(encoding="ascii")
            .strip(),
            "parent_pid": _waiting_parent_pid,
            "admission_pid": os.getpid(),
            "guard_pid": launcher_receipt["guard_pid"],
            "guard_start": launcher_receipt["guard_start"],
        }
        temporary = receipt_path.with_name(receipt_path.name + ".new")
        temporary.write_text(json.dumps(runtime), encoding="utf-8")
        os.replace(temporary, receipt_path)
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

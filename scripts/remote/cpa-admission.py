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
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
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
SSE_QUEUE_CHUNKS = 8
SSE_QUEUE_POLL_SECONDS = 0.1
ADMISSION_REQUEST_BODY_TIMEOUT_SECONDS = 30.0

# Lane admission bounds.
ADMISSION_MAX_INFLIGHT_BY_LANE = {
    "chatgpt-oauth": 2,
    "zhipu-coding-plan": 3,
    "deepseek-official": 3,
    "antigravity-oauth": 2,
    "claude-oauth": 2,
    "workbuddy-oauth": 1,
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

ADMISSION_EARLY_PROBE_INTERVAL_SECONDS = 10.0
ADMISSION_RESET_PROBE_INTERVAL_SECONDS = 300.0
STRICT_OAUTH_LANES = frozenset({"antigravity-oauth", "claude-oauth", "workbuddy-oauth"})
DURABLE_OAUTH_LANES = STRICT_OAUTH_LANES | {"chatgpt-oauth"}


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

    expected: dict[str, str] = {}
    for key in ("providers", "oauth_routes", "plugin_routes"):
        routes = manifest.get(key)
        if not isinstance(routes, list):
            raise ValueError(f"shared route manifest {key} must be a list")
        for index, route in enumerate(routes):
            if not isinstance(route, dict):
                raise ValueError(f"shared route manifest {key}[{index}] must be a mapping")
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
                raise ValueError(f"shared route manifest {key}[{index}] requires an admission lane")
            if not isinstance(models, list) or not models:
                raise ValueError(f"shared route manifest {key}[{index}] requires models")
            for model in models:
                alias = model if key == "plugin_routes" else (
                    model.get("alias") if isinstance(model, dict) else None
                )
                if not isinstance(alias, str) or not alias.strip():
                    raise ValueError(
                        f"shared route manifest {key}[{index}] contains an invalid model alias"
                    )
                normalized = alias.strip().lower()
                if normalized in expected:
                    raise ValueError("shared route manifest assigns a model more than once")
                expected[normalized] = lane.strip()
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
                "model_capacity_markers": tuple(
                    item.strip().lower() for item in model_markers
                ),
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
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("error"):
            errors.append(payload["error"])
        elif (
            payload.get("code") == 11140
            or (lane_name == "workbuddy-oauth" and payload.get("code") in (6003, 6004))
        ) and isinstance(payload.get("msg"), str):
            errors.append({"code": payload["code"], "message": payload["msg"]})
        elif payload.get("type") == "error":
            errors.append({k: payload[k] for k in ("code", "message") if k in payload})
        response = payload.get("response")
        if isinstance(response, dict) and response.get("error"):
            errors.append(response["error"])
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
    if (
        "model-level rate limit (code 6004)" in message
        and (
            "other models on the same credential are unaffected" in message
            or "other models on the same credential keep working" in message
        )
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
            {"error": "Invalid API key"}, {"error": "Missing API key"},
        ):
            return None, None
    if lane_name == "antigravity-oauth":
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
    if lane_name in {"chatgpt-oauth", "workbuddy-oauth"}:
        hold = "authentication_required" if status in (401, 403) else None
        errors = protocol_errors(body, lane_name)
        if lane_name == "workbuddy-oauth":
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
            if code in (401, 403) or error.get("type") == "permission_error":
                hold = hold or "authentication_required"
            quoted_model_busy = (
                lane_name == "workbuddy-oauth"
                and error.get("type") == "rate_limit_error"
                and quoted_upstream_code(error.get("message")) == 14003
            )
            model_scoped_6004 = (
                lane_name == "workbuddy-oauth"
                and (code == 6004 or quoted_upstream_code(error.get("message")) == 6004)
                and code not in (401, 403, 429, 6003, 11140)
                and quoted_upstream_code(error.get("message")) not in
                (401, 403, 429, 6003, 11140)
            )
            if (
                lane_name == "workbuddy-oauth"
                and not known_retry
                and not quoted_model_busy
                and not model_scoped_6004
                and (
                    code in (429, 6003) or error.get("type") == "rate_limit_error"
                )
            ):
                hold = hold or "retry_window_review_required"
            if code in ("account_deactivated", "account_suspended", "account_disabled"):
                hold = "account_restricted"
            elif error.get("type") == "authentication_error":
                hold = hold or "authentication_required"
            message = str(error.get("message", "")).lower()
            if any(marker in message for marker in (
                "account has been suspended", "account has been disabled",
                "account is suspended", "account is disabled", "account_deactivated",
                "account suspended", "account disabled",
            )):
                hold = "account_restricted"
            if lane_name == "workbuddy-oauth" and code == 11140:
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
            hold = "authentication_required"
        text = str(error.get("message", "")).lower()
        if any(
            marker in text
            for marker in (
                "account suspended",
                "account has been suspended",
                "service has been disabled",
                "service has been disable",
                "violation of terms",
                "violating the terms",
            )
        ):
            hold = "account_restricted"
        details = error.get("details")
        for detail in details if isinstance(details, list) else []:
            if not isinstance(detail, dict):
                continue
            kind = detail.get("@type")
            value = None
            if kind == "type.googleapis.com/google.rpc.RetryInfo":
                value = detail.get("retryDelay")
            elif kind == "type.googleapis.com/google.rpc.ErrorInfo":
                if detail.get("reason") in (
                    "SERVICE_DISABLED",
                    "ACCOUNT_DISABLED",
                    "ACCOUNT_SUSPENDED",
                ):
                    hold = "account_restricted"
                metadata = detail.get("metadata")
                if isinstance(metadata, dict):
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
    if config.get("name") == "workbuddy-oauth":
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
            and (error.get("code") == 6004
                 or quoted_upstream_code(error.get("message")) == 6004)
            and error.get("type") not in ("authentication_error", "permission_error")
            for error in errors
        ):
            return "model"
    error_text = json.dumps(errors, ensure_ascii=False).lower()
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
    if any(
        code in error_text
        for code in (
            '"model_not_found"',
            '"invalid_api_key"',
            '"authentication_error"',
        )
    ):
        return None
    return "lane" if status in config["capacity_statuses"] else None


def is_capacity_response(
    status: int,
    body_prefix: bytes,
    retry_after: str | None,
    config: dict[str, Any],
) -> bool:
    return capacity_scope(status, body_prefix, retry_after, config) is not None


# Event names are deliberately allowlisted: upstream event types are not
# trusted logging data and may contain user/provider-controlled strings.
SAFE_SSE_EVENT_TYPES = frozenset({
    "response.created", "response.queued", "response.in_progress",
    "response.output_item.added", "response.output_item.done",
    "response.content_part.added", "response.content_part.done",
    "response.output_text.delta", "response.output_text.done",
    "response.reasoning_summary_text.delta",
    "response.reasoning_summary_text.done",
    "response.function_call_arguments.delta",
    "response.function_call_arguments.done",
    "response.refusal.delta", "response.refusal.done",
    "response.completed", "response.failed", "response.incomplete",
    "response.cancelled", "message_start", "content_block_start",
    "content_block_delta", "content_block_stop", "message_delta",
    "message_stop", "ping", "interaction.completed", "interaction.done",
    "interaction.failed", "interaction.cancelled",
})


class StreamErrorProbe:
    """Inspect every SSE event without retaining generated output."""

    def __init__(
        self, limit: int, lane: dict[str, Any], status: int = 200, path: str = "",
        retry_after: int | None = None,
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
        self.path = unquote(urlsplit(path).path).rstrip("/").lower()
        self.scope: str | None = None
        self.hold_reason: str | None = None
        self.retry_after: int | None = None
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
            if protocol_errors(payload, self.lane.get("name")):
                self.has_error = True
                if self.lane.get("name") in DURABLE_OAUTH_LANES:
                    hold, delay = oauth_risk_signal(
                        self.lane.get("name"), self.status, payload, self.known_retry
                    )
                    self.hold_reason = self.hold_reason or hold
                    if delay is not None:
                        self.retry_after = max(self.retry_after or 0, delay)
                scope = capacity_scope(self.status, payload, None, self.lane)
                if scope == "lane" or self.scope is None:
                    self.scope = scope
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
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    model = payload.get("model") if isinstance(payload, dict) else None
    if not isinstance(model, str) or not model.strip():
        return None
    model = model.strip().lower()
    return model.removeprefix("models/") if route.endswith("/interactions") else model


def routing_model(model: str) -> str:
    """Use CPA's base model identity without changing the forwarded request."""

    opening = model.rfind("(")
    return model[:opening].strip() if opening >= 0 and model.endswith(")") else model


def normalize_cross_provider_agent_messages(path: str, body: bytes) -> tuple[bytes, bool]:
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
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
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
            or content[0]["text"] not in (
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
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), True


def unsupported_encrypted_agent_payload(path: str, body: bytes) -> bool:
    """Reject unrecognized encrypted inter-agent shapes before upstream."""
    if not urlsplit(path).path.rstrip("/").endswith("/responses"):
        return False
    model = requested_model(path, body)
    if model is None or not routing_model(model).startswith(
        ("claude-", "deepseek-", "glm-", "gemini-")
    ):
        return False
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
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
        "/v1/responses", "/responses", "/v1/chat/completions", "/chat/completions"
    }:
        return False
    model = requested_model(path, body)
    if model is None or routing_model(model) not in text_only_models:
        return False
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
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
        self._strict_backoff = config.get("name") in STRICT_OAUTH_LANES
        self._durable_risk = config.get("name") in DURABLE_OAUTH_LANES
        self._state_path = state_path
        self._manual_hold: str | None = None
        self._state_write_failed = False
        if state_path is not None:
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            if (
                not isinstance(saved, dict)
                or saved.get("version") != 1
                or set(saved) != {"version", "not_before", "hold_reason"}
                or type(saved.get("not_before")) not in (int, float)
                or not math.isfinite(saved["not_before"])
                or saved["not_before"] < 0
                or saved.get("hold_reason")
                not in (
                    None,
                    "authentication_required",
                    "account_restricted",
                    "state_write_failed",
                    "retry_window_review_required",
                    "unverified_request",
                )
            ):
                raise ValueError(
                    "invalid OAuth circuit state; operator review required"
                )
            self._manual_hold = saved["hold_reason"]
            remaining = max(0, saved["not_before"] - time.time())
            if remaining:
                self.open_until = time.monotonic() + remaining
                self._server_not_before = self.open_until
        self._saved_risk = (self.open_until, self._manual_hold, False)

    def _persist_risk(self) -> None:
        risk = (
            max(self.open_until, self._server_not_before),
            self._manual_hold,
            self.inflight > 0,
        )
        if self._state_path is None or risk == self._saved_risk:
            return
        payload = {
            "version": 1,
            "not_before": time.time() + max(0, risk[0] - time.monotonic())
            if risk[0]
            else 0,
            # Write ahead before any upstream request. If a later risk update
            # cannot reach disk or the process dies, restart requires review
            # instead of treating the old clear state as permission to retry.
            "hold_reason": self._manual_hold
            or ("unverified_request" if self.inflight else None),
        }
        candidate = self._state_path.with_suffix(".tmp")
        try:
            with candidate.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(candidate, self._state_path)
            self._saved_risk = risk
        except OSError:
            self._state_write_failed = True
            self._manual_hold = "state_write_failed"
            logging.error("oauth_state_write_failed operator_review_required")

    def record_risk(self, hold_reason: str | None, retry_after: int | None) -> None:
        """Latch streaming/header evidence before waiting for EOF or writing."""

        if not self._durable_risk or (hold_reason is None and retry_after is None):
            return
        with self._condition:
            if hold_reason:
                self._manual_hold = hold_reason
            if retry_after is not None:
                if retry_after > 2147483647:
                    self._manual_hold = "retry_window_review_required"
                else:
                    until = time.monotonic() + max(1, retry_after)
                    self.open_until = max(self.open_until, until)
                    self._server_not_before = max(self._server_not_before, until)
                    self._next_probe_at = self.open_until
            self._failure_generation += 1
            self._persist_risk()
            self._condition.notify_all()

    def _open_retry_after(self, now: float) -> int:
        return max(1, _ceil_remaining_seconds(self.open_until, now))

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
        """

        return max(
            1,
            _ceil_remaining_seconds(
                max(
                    self._model_open_until.get(model, 0.0),
                    self._model_probe_at.get(model, 0.0),
                    self._model_server_not_before.get(model, 0.0),
                ),
                now,
            ),
        )

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
        elif streak >= ADMISSION_COOLDOWN_FAILURE_THRESHOLD:
            index = min(
                streak - ADMISSION_COOLDOWN_FAILURE_THRESHOLD,
                len(self._schedule) - 1,
            )
            delay = self._schedule[index]
        else:
            delay = None
        if delay is not None:
            # An upstream-advertised Retry-After is the upstream itself telling
            # us to back off that model, so the probe may not fire before it.
            # Same contract as the lane-level `_server_not_before`.
            if retry_after is not None:
                self._model_server_not_before[model] = max(
                    self._model_server_not_before.get(model, 0.0),
                    now + min(max(1, retry_after), self._retry_after_max),
                )
            self._model_open_until[model] = max(
                self._model_open_until.get(model, 0.0),
                now + min(delay, self._retry_after_max),
            )
            self._model_probe_at[model] = now + self._early_probe_interval

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
                        return lease(False, "operator_review_required", 300)
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
                        if not queued and (
                            next_admission - now <= self._queue_timeout
                        ):
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
                        return lease(False, "cooldown", self._open_retry_after(now))
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
    ) -> None:
        with self._condition:
            now = time.monotonic()
            self.inflight = max(0, self.inflight - 1)
            if lease.probe and not lease.model_probe:
                self.probe_inflight = False
            if lease.model_probe and lease.model is not None:
                self._model_probe_inflight.discard(lease.model)
            if self._durable_risk and hold_reason:
                self._manual_hold = hold_reason
            if capacity_error and capacity_scope == "model" and lease.model is not None:
                # The upstream named one model, so only that model is parked.
                # Promoting it to the lane would refuse the healthy siblings
                # that share the credential (and the concurrency budget).
                self._record_model_capacity(lease.model, retry_after, now)
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
                    self._manual_hold = "retry_window_review_required"
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
                "half_open_probe": self.probe_inflight,
                "failure_generation": self._failure_generation,
                "server_retry_after_remaining": max(
                    0, _ceil_remaining_seconds(self._server_not_before, now)
                ),
                "reset_probe_in": max(
                    0, _ceil_remaining_seconds(self._reset_probe_not_before, now)
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
        traffic_lock_path: Path | None = None,
    ) -> None:
        self.config = config
        # Use the existing deployed route manifest as the capability authority;
        # do not duplicate a list of model names in admission configuration.
        routes = json.loads(
            Path(__file__).with_name("cpa_provider_routes.json").read_text(
                encoding="utf-8"
            )
        )
        self.text_only_models = frozenset(
            model["alias"].strip().lower()
            for provider in routes["providers"]
            for model in provider["models"]
            if model.get("input-modalities") == ["text"]
        )
        self.traffic_lock_path = traffic_lock_path
        self.heartbeat_interval = heartbeat_interval
        self.lanes = {
            lane["name"]: LaneState(
                lane,
                {
                    "antigravity-oauth": state_path,
                    "claude-oauth": claude_state_path,
                    "chatgpt-oauth": chatgpt_state_path,
                    "workbuddy-oauth": workbuddy_state_path,
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

    @contextmanager
    def request_guard(self) -> Iterator[bool]:
        """Hold a shared OS lock until the complete upstream request finishes.

        The updater holds the exclusive side across replacement and rollback.
        Each request opens its own descriptor: sharing one across threads would
        let one request unlock another. Missing/inaccessible locks fail closed.
        """
        if self.traffic_lock_path is None:
            yield True
            return
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
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
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
                fcntl.flock(handle, fcntl.LOCK_UN)

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
            "traffic_guard": str(self.traffic_lock_path) if self.traffic_lock_path else None,
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
        if self.command == "GET" and urlsplit(self.path).path == "/healthz":
            self._proxy_unlocked()
            return
        with proxy.request_guard() as admitted:
            if not admitted:
                self.close_connection = True
                self._send_json(503, {"error": {"type": "maintenance_in_progress"}})
                return
            self._proxy_unlocked()

    def _proxy_unlocked(self) -> None:
        proxy: AdmissionProxy = self.server.proxy  # type: ignore[attr-defined]
        config = proxy.config
        body = self._read_body(config["max_body_bytes"])
        if self.command == "GET" and urlsplit(self.path).path == "/healthz":
            self._send_json(200, proxy.health())
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
            payload = json.loads(body)
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
        lane_config = proxy.lane_config[lane_name] if lane_name is not None else None
        lease: Lease | None = None
        if lane_name is not None and lane_config is not None:
            lease = (
                proxy.lanes[lane_name].acquire_after_reset(expected_generation)
                if expected_generation is not None
                else proxy.lanes[lane_name].acquire(self._downstream_alive, model=model)
            )
            if not lease.admitted:
                manual_hold = lease.reason == "operator_review_required"
                self._send_json(
                    409 if reset_probe else (403 if manual_hold else 429),
                    {
                        "error": {
                            "message": (
                                f"Shared upstream lane {lane_name} requires operator review."
                                if manual_hold
                                else f"Shared upstream lane {lane_name} is cooling "
                                "down or busy; retry after the advertised interval."
                            ),
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
        successful = False
        retry_after: int | None = None
        hold_reason: str | None = None
        response_started = False
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
            elif lane_name == "workbuddy-oauth" and response.status in (
                429,
                503,
                529,
            ):
                # Peak-hour "14003 Model busy" waves arrive without a
                # Retry-After and self-clear in ~30 minutes, so they only get
                # the ladder rung below: the lane cools and reopens by itself.
                # Account-shaped bodies (rate_limit_error, 6003/6004, 11140,
                # auth failures) still hold through the stream-signal path.
                # Pre-arming the operator hold here turned every capacity wave
                # into a manual release (2026-10-20: two same-evening waves).
                proxy.lanes[lane_name].record_risk(
                    None,
                    retry_after or lane_config["cooldown_schedule_seconds"][0],
                )
                if retry_after is not None:
                    retry_after_header = str(retry_after)
            if lane_name == "antigravity-oauth" and response.status in (429, 503):
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
                        or (lane_name == "claude-oauth" and retry_after is not None
                            and key.lower() in {"retry-after", "retry-after-ms"})
                    ):
                        continue
                    self.send_header(key, value)
                if lane_name == "claude-oauth" and retry_after is not None:
                    self.send_header("Retry-After", str(retry_after))
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
            stream_probe = (
                StreamErrorProbe(
                    config["probe_bytes"], lane_config, response.status, self.path,
                    retry_after=retry_after,
                )
                if "text/event-stream" in content_type and lane_config is not None
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
                        if (signal_delay is None and stream_probe.scope is not None
                            and lane_name in STRICT_OAUTH_LANES):
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
            if lane_name in DURABLE_OAUTH_LANES:
                body_hold, body_delay = oauth_risk_signal(
                    lane_name, response.status,
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
            capacity_error = capacity_kind is not None
            successful = (
                200 <= response.status < 300
                and not capacity_error
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
                "sse_bytes=%s sse_events=%s sse_last_event=%s "
                "sse_inspection_complete=%s sse_malformed_json=%s "
                "sse_terminal_seen=%s %s",
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
                stream_probe.bytes_received if stream_probe is not None else 0,
                stream_probe.event_count if stream_probe is not None else 0,
                stream_probe.last_event_type if stream_probe is not None else "none",
                str(stream_probe.complete).lower() if stream_probe is not None else "na",
                str(stream_probe.malformed_event).lower() if stream_probe is not None else "na",
                str(stream_probe.terminal_success).lower() if stream_probe is not None else "na",
                self._trace_context,
            )
        except DownstreamClientDisconnected:
            self.close_connection = True
            # A client-side RST/BrokenPipe is not evidence that the shared
            # upstream account is overloaded. Do not open or extend a lane
            # cooldown for a request that the caller abandoned.
            capacity_error = False
            capacity_kind = None
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
                str(stream_probe.complete).lower() if stream_probe is not None else "na",
                str(stream_probe.malformed_event).lower() if stream_probe is not None else "na",
                str(stream_probe.terminal_success).lower() if stream_probe is not None else "na",
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
                    capacity_scope=capacity_kind or "lane",
                    hold_reason=hold_reason,
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

    def __init__(self, address: tuple[str, int], proxy: AdmissionProxy) -> None:
        self.proxy = proxy
        super().__init__(address, Handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--claude-state-file", type=Path)
    parser.add_argument("--chatgpt-state-file", type=Path)
    parser.add_argument("--workbuddy-state-file", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    state_files = {
        "antigravity-oauth": args.state_file,
        "claude-oauth": args.claude_state_file,
        "chatgpt-oauth": args.chatgpt_state_file,
        "workbuddy-oauth": args.workbuddy_state_file,
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
        config, state_path=args.state_file, claude_state_path=args.claude_state_file,
        chatgpt_state_path=args.chatgpt_state_file,
        workbuddy_state_path=args.workbuddy_state_file,
        traffic_lock_path=Path("/run/cpa-admission/requests.lock"),
    )
    # Create before reporting ready so an updater never locks a different inode.
    assert proxy.traffic_lock_path is not None
    with proxy.traffic_lock_path.open("a"):
        pass
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

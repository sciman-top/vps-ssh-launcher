#!/usr/bin/env python3
r"""Read-only natural-traffic audit for the CPA admission journal.

The admission journal already records per-request latency, capacity scope,
protocol completion, and SSE byte counts.  What it did not have was a compact
way to answer the operational questions that keep coming back:

* Which lane/model is currently carrying a burst close to a provider's limit?
* Is a slow turn waiting in admission, or is upstream or decoding itself slow?
* How much output bytes per second does a completed stream actually deliver?
* Did a nominally completed response contain no user-visible text?

This tool reads the journal on the host (or a saved copy) and emits aggregates
only.  Health-probe request counts stay visible, while latency, protocol,
throughput and cache metrics use natural request records so cold probe
prefixes and tiny probe outputs cannot be mistaken for user traffic.  It never
prints prompts, response text, request IDs, client keys, or credential
material.  ``sse_bytes_per_s`` is a transport proxy for output speed; it is
not a token-throughput measurement.  ``output_tokens_per_s`` is a bounded
stream-window estimate, never a provider throughput benchmark.

Examples::

    ./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --hours 24
    ./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --ssh-profile bwg --json
    journalctl -u cpa-admission -o short-iso-precise --no-pager > /tmp/cpa.log
    ./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --file /tmp/cpa.log
    ./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --hours 24 ^
      --origin chatgpt-oauth/gpt-6-luna:C:\captures\one-response.dump
"""

from __future__ import annotations

import argparse
import ast
import base64
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import zlib
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

try:
    from scripts.audit_cli import make_streams_lossy_safe
except ModuleNotFoundError:
    if __package__:
        raise
    from bootstrap import ensure_repo_imports  # type: ignore[import-not-found]

    ensure_repo_imports()
    from scripts.audit_cli import (  # type: ignore[import-not-found]
        make_streams_lossy_safe,
    )

_TIMESTAMP = re.compile(r"^(\S+)")
_FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
_EMPTY_TEXT_FINISH_REASONS = frozenset({"empty_text"})
# `tool_call` is a distinct completion shape, not an empty answer: a Codex
# agentic turn legitimately ends on a function call with no visible text. The
# admission classifier reported it as `reasoning_only` until 2026-11-08, which
# made 1444 of one day's chatgpt-oauth results look like reasoning turns.
_TOOL_CALL_FINISH_REASONS = frozenset(
    {"tool_call", "tool_calls", "function_call", "tool_use"}
)
_REASONING_ONLY_FINISH_REASONS = frozenset({"reasoning_only"})
_TRUNCATED_TEXT_FINISH_REASONS = frozenset(
    # A CPA protocol reports one of these only when the preceding user-visible
    # text event was cut off by its own output budget.
    {"length", "max_tokens", "max_output_tokens", "MAX_TOKENS"}
)
_TEXT_FINISH = re.compile(rb'"text"\s*:\s*"[^\"]')
_TEXT_STOP_SENTINEL = re.compile(rb'"text"\s*:\s*"stop"')
_TEXT_OUTPUT_DONE = re.compile(
    rb'"type"\s*:\s*"response\.output_text\.done"[^}\n]*"text"\s*:\s*"[^\"]'
)
_EMPTY_TEXT_DONE = re.compile(
    rb'"type"\s*:\s*"response\.output_text\.done"[^}\n]*"text"\s*:\s*""'
)
_FINISH_REASON = re.compile(rb'"finish_reason"\s*:\s*"([^"]+)"')
_FINISH_REASON_CAMEL = re.compile(rb'"finishReason"\s*:\s*"([^"]+)"')
_STOP_REASON = re.compile(rb'"stop_reason"\s*:\s*"([^"]+)"')
_ORIGIN_COUNT = 4
_OUTPUT_FINISH = "output"
_OUTPUT_EMPTY = "empty"
_OUTPUT_TRUNCATED = "truncated"
_OUTPUT_REASONING_ONLY = "reasoning_only"
_OUTPUT_UNKNOWN = "unknown"

# Cache telemetry is observation-only. Require enough natural token reports and
# input volume before flagging a low ratio, so short sessions do not create a
# tuning signal from one cold prefix.
_CACHE_HEALTH_MIN_REPORTS = 10
_CACHE_HEALTH_MIN_INPUT_TOKENS = 1_000_000
_CACHE_HEALTH_WARN_RATIO = 0.5


def _parse_timestamp(token: str) -> datetime | None:
    """Parse the leading journal timestamp without assuming a timezone suffix."""

    try:
        return datetime.fromisoformat(token.replace(",", "."))
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    if value is None or value in ("na", "none"):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile, matching the doctor's bounded reporting style."""

    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _peak_in_window(timestamps: Sequence[datetime], seconds: float) -> int:
    """Largest number of events inside any sliding window of ``seconds``."""

    # A 60-second rolling window is half-open: event at T and event at T+60s
    # do not share a minute.  Keep the stored timestamps exact for downstream
    # attribution, and only place the boundary on the exclusive end here.
    ordered = sorted(timestamps)
    peak = 0
    start = 0
    for end, current in enumerate(ordered):
        while (current - ordered[start]).total_seconds() >= seconds:
            start += 1
        peak = max(peak, end - start + 1)
    return peak


def _text_finish_reason(line: str) -> str | None:
    """Return a bounded completion shape from admission's allowlist field."""

    observed = dict(_FIELD.findall(line)).get("sse_text_finish")
    if observed in (
        _EMPTY_TEXT_FINISH_REASONS
        | _TRUNCATED_TEXT_FINISH_REASONS
        | _TOOL_CALL_FINISH_REASONS
        | _REASONING_ONLY_FINISH_REASONS
    ):
        return str(observed)
    return None


def _metrics(
    record: dict[str, Any], result_ts: datetime
) -> tuple[int | None, int | None]:
    """Return (total_duration_ms, first_output_ms) for one request.

    ``upstream_result`` is written at terminal delivery.  Stream mode commits
    its downstream headers before the queue, so total duration must come from
    the recorded stage durations.  A non-stream response commits its headers
    after upstream answer, so its ``http`` interval measures additional decode
    time after the upstream header; use that interval when available.
    """

    waited_ms = record.get("waited_ms")
    header_ms = record.get("header_ms")
    first_byte_ms = record.get("first_byte_ms")
    header_at = record.get("header_at")
    sse = record.get("sse_inspection") != "na"
    post_header_ms = None
    if isinstance(header_at, datetime) and result_ts >= header_at:
        post_header_ms = int(round((result_ts - header_at).total_seconds() * 1000))
    total_duration_ms = (
        waited_ms + header_ms
        if waited_ms is not None and header_ms is not None
        else None
    )
    if not sse and post_header_ms is not None and header_ms is not None:
        total_duration_ms = header_ms + post_header_ms
    if not sse and post_header_ms is None:
        total_duration_ms = None
    first_output_ms = (
        waited_ms + first_byte_ms
        if waited_ms is not None and first_byte_ms is not None
        else None
    )
    return total_duration_ms, first_output_ms


def parse_journal(
    lines: Iterable[str],
) -> tuple[list[dict[str, Any]], int]:
    """Pair request-start lines with upstream-result lines.

    The result line is the authoritative completion record.  A start line is
    used only to derive total duration; if it is outside the captured window,
    duration-based metrics stay ``None`` rather than guessing.
    """

    header_logs: dict[str, datetime] = {}
    admission_logs: dict[str, datetime] = {}
    probe_ids: set[str] = set()
    records: list[dict[str, Any]] = []
    header_lines = 0
    for raw in lines:
        line = raw.rstrip("\n")
        match = _TIMESTAMP.match(line)
        if not match:
            continue
        timestamp = _parse_timestamp(match.group(1))
        if timestamp is None:
            continue
        fields = {key: value for key, value in _FIELD.findall(line)}
        request_id = fields.get("request_id")
        if request_id and " lane_probe " in line:
            probe_ids.add(request_id)
            continue
        if request_id and " lane_admit " in line:
            admission_logs[request_id] = timestamp
            continue
        if request_id and " http " in line and "upstream_result " not in line:
            # BaseHTTPRequestHandler.log_message runs when the downstream
            # response headers are written, not when the request arrives.
            # Pairing the result with this line therefore measures the stream
            # from header commit to terminal event, not queue plus upstream
            # header time.  The header time is reported separately.
            header_logs[request_id] = timestamp
            header_lines += 1
            continue
        if "upstream_result " not in line:
            continue
        header_at = header_logs.get(request_id) if request_id else None
        waited_ms = _parse_int(fields.get("waited_ms"))
        header_ms = _parse_int(fields.get("upstream_header_ms"))
        first_byte_ms = _parse_int(fields.get("upstream_first_byte_ms"))
        first_text_ms = _parse_int(fields.get("first_text_ms"))
        sse_inspection = fields.get("sse_inspection_complete") or "na"
        admission_ts = admission_logs.get(request_id) if request_id else None
        if admission_ts is None and header_at is not None and header_ms is not None:
            if waited_ms is not None and sse_inspection != "na":
                # A lane-owned SSE request commits its downstream headers before
                # queueing, so the admit instant is the logged header time plus
                # the measured queue wait.
                admission_ts = header_at + timedelta(milliseconds=waited_ms)
            else:
                # A non-SSE request logs downstream headers only after the
                # upstream has answered; subtract that upstream header time.
                admission_ts = header_at - timedelta(milliseconds=header_ms)
        if admission_ts is None or admission_ts > timestamp:
            admission_ts = None
        record = {
            "waited_ms": waited_ms,
            "header_ms": header_ms,
            "first_byte_ms": first_byte_ms,
            "first_text_ms": first_text_ms,
            "header_at": header_at,
            "sse_inspection": sse_inspection,
            "lane": fields.get("lane") or "unknown",
            "observed_model": fields.get("model") or "unknown",
        }
        total_duration_ms, first_output_ms = _metrics(record, timestamp)
        stream_duration_s = None
        if header_at is not None and timestamp >= header_at:
            stream_duration_s = (timestamp - header_at).total_seconds()
        # A result line is written at terminal delivery.  For a multi-event SSE
        # turn, subtract its upstream first-byte directly; single-frame streams
        # have no inner decode interval, so leave them unreported instead of
        # turning a tiny terminal frame into fabricated throughput.  For
        # non-SSE, subtract both queue and TTFB from the result.  The result
        # itself is the precise receipt; a pre-header http timestamp would put
        # queue time on the wrong side of a subtraction.
        output_origin_at: datetime | None
        terminal_seen = fields.get("sse_terminal_seen") == "true"
        sse_events = _parse_int(fields.get("sse_events")) or 0
        if sse_inspection != "na" and terminal_seen and sse_events > 1:
            output_origin_at = (
                timestamp - timedelta(milliseconds=first_byte_ms)
                if first_byte_ms is not None
                else None
            )
        elif (
            sse_inspection == "na"
            and waited_ms is not None
            and first_byte_ms is not None
        ):
            output_origin_at = timestamp - timedelta(
                milliseconds=waited_ms + first_byte_ms
            )
            if output_origin_at > timestamp:
                output_origin_at = None
        else:
            output_origin_at = None
        first_output_at = output_origin_at
        records.append(
            {
                "lane": record["lane"],
                "model": record["observed_model"],
                "protocol_result": fields.get("protocol_result") or "unknown",
                "waited_ms": waited_ms,
                "header_ms": header_ms,
                "first_byte_ms": first_byte_ms,
                "first_text_ms": first_text_ms,
                "sse_bytes": _parse_int(fields.get("sse_bytes")) or 0,
                "sse_events": _parse_int(fields.get("sse_events")) or 0,
                "sse_inspection": sse_inspection,
                "text_finish": _text_finish_reason(line),
                "first_output_ms": first_output_ms,
                "total_duration_ms": total_duration_ms,
                "first_output_at": first_output_at,
                "usage_in": _parse_int(fields.get("usage_in")),
                "usage_cached": _parse_int(fields.get("usage_cached")),
                "usage_out": _parse_int(fields.get("usage_out")),
                "usage_cost_usd": _parse_cost(fields.get("usage_cost_usd")),
                "probe": request_id in probe_ids if request_id else False,
                "admission_ts": admission_ts,
                "result_ts": timestamp,
                "stream_duration_s": stream_duration_s,
            }
        )
    return records, header_lines


def _values(records: Sequence[dict[str, Any]], key: str) -> list[float]:
    return [
        float(value) for record in records if (value := record.get(key)) is not None
    ]


def _parse_cost(value: str | None) -> float | None:
    if value is None or value in ("na", "none"):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _dump_response_section(raw: bytes) -> bytes:
    """Return the response half of a short CPA request dump, or all of a stream.

    CPA labels the response section in request dumps, but a raw streaming
    capture may contain only SSE bytes.  Never scan the request half: it may
    include prompt text that merely discusses truncation or an empty answer.
    """

    text = raw.decode("utf-8", "replace")
    offset = max(
        (
            text.rfind(marker)
            for marker in (
                "=== RESPONSE HEADERS ===",
                "=== RESPONSE BODY ===",
                "=== RESPONSE ===",
                "=== UPSTREAM RESPONSE ===",
            )
        ),
        default=-1,
    )
    if offset >= 0:
        return text[offset:].encode("utf-8")
    first = raw.lstrip()[:1]
    return raw if first in (b"d", b"e", b"{") else b""


def _finish_reason(body: bytes) -> str | None:
    found = [
        match.group(1).decode("ascii", "replace")
        for pattern in (_FINISH_REASON, _FINISH_REASON_CAMEL, _STOP_REASON)
        if (match := pattern.search(body))
    ]
    return found[-1] if found else None


def _response_origin(body: bytes) -> str:
    """Classify a captured response without retaining text or tokens.

    A non-empty output text part proves a text answer.  An empty final text
    with a normal stop proves the empty-completion failure.  A terminal
    success without a text part is labelled reasoning-only, not empty prose,
    because CPA emits identical event names for those distinct outcomes.
    """

    if (
        _TEXT_FINISH.search(body) and not _TEXT_STOP_SENTINEL.search(body)
    ) or _TEXT_OUTPUT_DONE.search(body):
        return _OUTPUT_FINISH
    if _EMPTY_TEXT_DONE.search(body):
        return _OUTPUT_EMPTY
    normal = (
        b'"type":"response.incomplete"',
        b'"type": "response.incomplete"',
        b'"type":"response.completed"',
        b'"type": "response.completed"',
        b'"finish_reason":"stop"',
        b'"finish_reason": "stop"',
        b'"finishReason":"STOP"',
        b'"finishReason": "STOP"',
        b'"stop_reason":"end_turn"',
        b'"stop_reason": "end_turn"',
    )
    if not any(marker in body for marker in normal):
        return (
            _OUTPUT_TRUNCATED
            if _finish_reason(body) in _TRUNCATED_TEXT_FINISH_REASONS
            else _OUTPUT_UNKNOWN
        )
    # The canary sentinel is an empty visible text part followed by a normal
    # stop. A terminal event with no text part at all is reasoning-only and
    # must not be labelled an empty answer.
    # Match via the shared whitespace-tolerant sentinel; the historical
    # literal `b'"text":"stop"'` here disagreed with it and would have
    # mislabelled a spaced-JSON empty answer as reasoning_only.
    return _OUTPUT_EMPTY if _TEXT_STOP_SENTINEL.search(body) else _OUTPUT_REASONING_ONLY


def _merge_origins(
    previous: tuple[str, ...] | None, observed: str
) -> tuple[str, ...] | None:
    """Bound explicit canary origin tracking to four observations."""

    if previous is not None and len(previous) >= _ORIGIN_COUNT:
        return previous
    current = list(previous or ())
    current.append(observed)
    return tuple(current)


def _origin_projection(origins: Sequence[str], name: str) -> tuple[str, ...]:
    """Select canary observations for one lane/model without substring bleed.

    ``model`` names can be prefixes of another model, so a plain ``startswith``
    would attribute one canary to both routes.  Split once at the lane/model
    separator and compare the complete label.
    """

    selected: list[str] = []
    for origin in origins:
        label, separator, state = origin.rpartition(":")
        if separator and label == name:
            selected.append(state)
    return tuple(selected)


def summarize(
    records: Sequence[dict[str, Any]],
    window_hours: float,
    header_logs: int = 0,
    origins: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Aggregate by lane/model without retaining request-level identifiers."""

    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(f"{record['lane']}/{record['model']}", []).append(record)

    lanes: list[dict[str, Any]] = []
    for name in sorted(groups):
        items = groups[name]
        # This is a natural-traffic audit. Health probes share the same lane
        # and can intentionally use cold prefixes and tiny outputs, so folding
        # them into latency/cache aggregates makes the operator attribute probe
        # behaviour to user traffic. Keep their count observable while all
        # operational metrics below use only non-probe requests.
        natural_items = [record for record in items if not record.get("probe")]
        results = [record["protocol_result"] for record in natural_items]
        completed = [
            record
            for record in natural_items
            if record["protocol_result"] == "completed"
        ]
        capacity = [
            record
            for record in natural_items
            if record["protocol_result"] == "capacity"
        ]
        disconnects = [
            record
            for record in natural_items
            if record["protocol_result"] == "client_disconnect"
        ]
        upstream_errors = [
            record
            for record in natural_items
            if record["protocol_result"] == "upstream_error"
        ]
        stream_durations = _values(completed, "stream_duration_s")
        total_durations = [
            record["total_duration_ms"] / 1000
            for record in completed
            if record.get("total_duration_ms") is not None
        ]
        queue_ms = [
            float(record["waited_ms"])
            for record in completed
            if record.get("waited_ms") is not None
        ]
        # upstream_first_byte_ms already starts at the upstream request (after
        # admission).  Do not subtract waited_ms again; otherwise a request
        # that waited longer than 5s is counted as an upstream stall even when
        # the upstream answered promptly.
        upstream_ttft_ms = [
            float(record["first_byte_ms"])
            for record in completed
            if record.get("first_byte_ms") is not None
        ]
        decode_ms = [
            max(0.0, record["total_duration_ms"] - record["first_output_ms"])
            for record in completed
            if record.get("first_output_ms") is not None
            and record.get("total_duration_ms") is not None
        ]
        slow_parts = {
            "queue": sum(value >= 5000 for value in queue_ms),
            "upstream": sum(value >= 5000 for value in upstream_ttft_ms),
            "decode": sum(value >= 5000 for value in decode_ms),
        }
        throughput = [
            record["sse_bytes"] / record["stream_duration_s"]
            for record in completed
            if record["sse_bytes"] > 0
            and record["stream_duration_s"] is not None
            and record["stream_duration_s"] > 0
        ]
        output_rates = []
        for record in completed:
            tokens = record["usage_out"]
            output_start = record.get("first_output_at")
            if tokens is None or tokens <= 0 or output_start is None:
                continue
            decode_seconds = (record["result_ts"] - output_start).total_seconds()
            if decode_seconds > 0.05:
                output_rates.append(tokens / decode_seconds)
        tiny_sse = [
            record
            for record in completed
            if record["sse_inspection"] == "true"
            and (record["sse_events"] <= 3 or record["sse_bytes"] < 1200)
        ]
        lane_origins = _origin_projection(origins or (), name)
        origin_states = tuple(origin.split(":", 1)[0] for origin in lane_origins)
        empty_text = [state for state in origin_states if state == _OUTPUT_EMPTY]
        truncated_text = [
            state for state in origin_states if state == _OUTPUT_TRUNCATED
        ]
        text_completed = [state for state in origin_states if state == _OUTPUT_FINISH]
        reasoning_only = [
            state for state in origin_states if state == _OUTPUT_REASONING_ONLY
        ]
        unexplained = [state for state in origin_states if state == _OUTPUT_UNKNOWN]
        text_finishes = [
            str(record["text_finish"])
            for record in completed
            if record.get("text_finish")
        ]
        reasoning_only += [
            state for state in text_finishes if state in _REASONING_ONLY_FINISH_REASONS
        ]
        # A tool-call turn is a legitimate completion with no visible text. It
        # must be counted separately from empty_text (a real defect) and from
        # reasoning_only (no answer was requested), or the operator reads every
        # agentic step as a broken answer.
        tool_call_text = [
            state for state in text_finishes if state in _TOOL_CALL_FINISH_REASONS
        ]
        empty_text += [
            state for state in text_finishes if state in _EMPTY_TEXT_FINISH_REASONS
        ]
        truncated_text += [
            state for state in text_finishes if state in _TRUNCATED_TEXT_FINISH_REASONS
        ]
        non_sse = [record for record in completed if record["sse_inspection"] == "na"]
        # Token/cache telemetry: only requests whose upstream reported usage.
        # Providers do not agree on whether `usage_in` includes cache reads.
        # Most OAuth/OpenAI-shaped responses report total input, while the
        # WorkBuddy/DeepSeek shape observed on BWG reports uncached input and
        # places cache reads in `usage_cached`.  A cached count greater than the
        # reported input is an unambiguous signal for the latter shape.  Keep
        # the existing total-input ratio for the former, but use cached+input
        # for the latter so the audit can never emit a hit ratio above 1.0.
        # Sums are observation-only; `token_reports` states the support.
        token_reports = [r for r in natural_items if r["usage_in"] is not None]
        tokens_input = sum(r["usage_in"] for r in token_reports)
        tokens_output = sum(
            r["usage_out"] for r in token_reports if r["usage_out"] is not None
        )
        tokens_cached = sum(
            r["usage_cached"] for r in token_reports if r["usage_cached"] is not None
        )
        uncached_parts = []
        additive_cache_shape = False
        for record in token_reports:
            reported_input = record["usage_in"]
            reported_cached = record["usage_cached"]
            if reported_cached is not None and reported_cached > reported_input:
                # The provider's input count excludes cache reads.
                uncached_parts.append(reported_input)
                additive_cache_shape = True
            else:
                # The provider's input count includes cache reads.
                uncached_parts.append(max(0, reported_input - (reported_cached or 0)))
        tokens_uncached = sum(uncached_parts)
        cost_reports = [r for r in natural_items if r.get("usage_cost_usd") is not None]
        cost_usd = sum(r["usage_cost_usd"] for r in cost_reports)
        zero_cost_reports = sum(1 for r in cost_reports if r["usage_cost_usd"] == 0)
        admission_times: list[datetime] = []
        for record in natural_items:
            admission_ts = record.get("admission_ts")
            if isinstance(admission_ts, datetime):
                admission_times.append(admission_ts)
        admit_peak_60s = (
            _peak_in_window(admission_times, 60) if admission_times else None
        )
        cache_denominator = tokens_cached + tokens_uncached
        cache_hit_ratio = (
            tokens_cached / cache_denominator if cache_denominator > 0 else None
        )
        span_minutes = 0.0
        if len(natural_items) > 1:
            result_times = [record["result_ts"] for record in natural_items]
            span_minutes = (max(result_times) - min(result_times)).total_seconds() / 60
        lanes.append(
            {
                "lane_model": name,
                "requests": len(items),
                "natural_requests": len(natural_items),
                "avg_rpm": len(natural_items) / (window_hours * 60)
                if window_hours > 0
                else None,
                "observed_span_minutes": round(span_minutes, 1),
                "peak_60s": _peak_in_window(
                    [record["result_ts"] for record in natural_items], 60
                ),
                "admit_peak_60s": admit_peak_60s,
                "admission_paired": len(admission_times),
                "probe_requests": sum(1 for record in items if record.get("probe")),
                "completed": len(completed),
                "capacity": len(capacity),
                "client_disconnect": len(disconnects),
                "upstream_error": len(upstream_errors),
                "other": len(natural_items)
                - len(completed)
                - len(capacity)
                - len(disconnects)
                - len(upstream_errors),
                "header_completed_p50_ms": _percentile(
                    _values(completed, "header_ms"), 0.5
                ),
                "header_completed_p90_ms": _percentile(
                    _values(completed, "header_ms"), 0.9
                ),
                "first_byte_p50_ms": _percentile(
                    _values(completed, "first_byte_ms"), 0.5
                ),
                "first_output_p50_ms": _percentile(upstream_ttft_ms, 0.5),
                "first_output_p90_ms": _percentile(upstream_ttft_ms, 0.9),
                "first_byte_p90_ms": _percentile(
                    _values(completed, "first_byte_ms"), 0.9
                ),
                "first_text_p50_ms": _percentile(
                    _values(completed, "first_text_ms"), 0.5
                ),
                "first_text_p90_ms": _percentile(
                    _values(completed, "first_text_ms"), 0.9
                ),
                "queue_ms_p50": _percentile(queue_ms, 0.5),
                "queue_ms_p90": _percentile(queue_ms, 0.9),
                "decode_ms_p50": _percentile(decode_ms, 0.5),
                "decode_ms_p90": _percentile(decode_ms, 0.9),
                "slow_5s_attribution": slow_parts,
                "stream_duration_p50_s": _percentile(stream_durations, 0.5),
                "stream_duration_p90_s": _percentile(stream_durations, 0.9),
                "total_duration_p50_s": _percentile(total_durations, 0.5),
                "total_duration_p90_s": _percentile(total_durations, 0.9),
                "stream_paired_completed": len(stream_durations),
                "stream_coverage": (
                    len(stream_durations) / len(completed) if completed else 0.0
                ),
                "sse_bytes_per_s_p50": _percentile(throughput, 0.5),
                "sse_bytes_per_s_p90": _percentile(throughput, 0.9),
                "output_tokens_per_s_p50": _percentile(output_rates, 0.5),
                "output_tokens_per_s_p90": _percentile(output_rates, 0.9),
                "output_rate_reports": len(output_rates),
                "tiny_sse_completed": len(tiny_sse),
                "output_origins": len(origin_states),
                "text_finish_reports": len(text_finishes),
                "empty_text_completed": len(empty_text),
                "truncated_text_completed": len(truncated_text),
                "text_output_completed": len(text_completed),
                "reasoning_only_completed": len(reasoning_only),
                "tool_call_completed": len(tool_call_text),
                "unexplained_output_origins": len(unexplained),
                "non_sse_completed": len(non_sse),
                "token_reports": len(token_reports),
                "tokens_input": tokens_input if token_reports else None,
                "tokens_cached": tokens_cached if token_reports else None,
                "tokens_uncached": tokens_uncached if token_reports else None,
                "tokens_output": tokens_output if token_reports else None,
                "cache_hit_ratio": cache_hit_ratio,
                "cache_accounting": (
                    "cached_plus_reported_input"
                    if additive_cache_shape
                    else "reported_input_total"
                ),
                "cost_reports": len(cost_reports),
                "cost_usd": cost_usd if cost_reports else None,
                "zero_cost_reports": zero_cost_reports,
                "protocol_results": {
                    result: results.count(result) for result in sorted(set(results))
                },
            }
        )
    warnings: list[str] = []
    for lane in lanes:
        name = lane["lane_model"]
        admit_peak = lane["admit_peak_60s"]
        if (
            name.startswith("workbuddy-oauth/")
            and admit_peak is not None
            and admit_peak >= 13
        ):
            warnings.append(
                f"{name} admit_peak_60s={admit_peak} is at/above the community "
                "14003 range (13-19 rpm); reduce burstiness, do not rotate "
                "accounts or egress IPs"
            )
        if (
            name == "chatgpt-oauth/gpt-6-luna"
            and admit_peak is not None
            and admit_peak >= 15
        ):
            warnings.append(
                f"{name} admit_peak_60s={admit_peak} crossed the local 15/min "
                "review threshold; prefer fewer parallel title/summary requests. "
                "This is a local risk-review threshold, not a provider limit"
            )
        if lane["tiny_sse_completed"]:
            warnings.append(
                f"{name} tiny_sse_completed={lane['tiny_sse_completed']}; "
                "inspect credits/protocol before attributing it to an account "
                "or treating it as proof of empty output"
            )
        cache_ratio = lane["cache_hit_ratio"]
        if lane["cache_accounting"] == "cached_plus_reported_input":
            warnings.append(
                f"{name} cache_usage_shape=cached_plus_reported_input; "
                "the provider reports cache reads separately from usage_in, "
                "so the hit ratio uses cached/(cached+usage_in)"
            )
        if lane["zero_cost_reports"]:
            warnings.append(
                f"{name} zero_cost_reports={lane['zero_cost_reports']}; provider-"
                "reported zero spend does not establish free capacity or a safe route"
            )
        if lane["unexplained_output_origins"]:
            warnings.append(
                f"{name} unexplained_output_origins="
                f"{lane['unexplained_output_origins']}; the explicit canary "
                "response lacked a recognizable text or terminal marker"
            )
        if lane["empty_text_completed"]:
            warnings.append(
                f"{name} empty_text_completed={lane['empty_text_completed']}; "
                "completed text events had no visible text. Do not treat cache "
                "or quality as healthy until protocol and output limits are checked"
            )
        if lane["truncated_text_completed"]:
            warnings.append(
                f"{name} truncated_text_completed={lane['truncated_text_completed']}; "
                "the model stopped on a token/length limit, not a complete answer"
            )
        if (
            lane["token_reports"] >= _CACHE_HEALTH_MIN_REPORTS
            and (lane["tokens_input"] or 0) >= _CACHE_HEALTH_MIN_INPUT_TOKENS
            and cache_ratio is not None
            and cache_ratio < _CACHE_HEALTH_WARN_RATIO
        ):
            warnings.append(
                f"{name} cache_hit_ratio={cache_ratio:.2f} across "
                f"{lane['tokens_input']} input tokens "
                f"(review threshold {_CACHE_HEALTH_WARN_RATIO:.2f}); inspect "
                "prompt-prefix stability and session affinity before changing "
                "cache keys"
            )
    return {
        "window_hours": window_hours,
        "records": len(records),
        "header_logs": header_logs,
        "lanes": lanes,
        "warnings": warnings,
        "origins_tracked": len(origins or ()),
    }


def _format_metric(value: float | None, unit: str = "") -> str:
    if value is None:
        return "na"
    if unit == "ms":
        return f"{value:.0f}ms"
    if unit == "s":
        return f"{value:.2f}s"
    if unit == "bps":
        return f"{value:.0f}B/s"
    return f"{value:.2f}"


def _optional_int(value: int | None) -> str:
    return "na" if value is None else str(value)


def render(summary: dict[str, Any]) -> list[str]:
    lines = [
        "== CPA natural traffic audit ==",
        f"window_hours={summary['window_hours']:g} "
        f"records={summary['records']} header_logs={summary['header_logs']}",
    ]
    if not summary["lanes"]:
        lines.append("NO_TRAFFIC: the journal window contains no upstream_result lines")
    for warning in summary.get("warnings", []):
        lines.append(f"WARN: {warning}")
    for lane in summary["lanes"]:
        lines.append(
            f"{lane['lane_model']} n={lane['requests']} "
            f"natural={lane['natural_requests']} "
            f"avg_rpm={_format_metric(lane['avg_rpm'])} "
            f"peak_completed_60s={lane['peak_60s']} "
            f"admit_peak_60s={_optional_int(lane['admit_peak_60s'])} "
            f"admission_paired={lane['admission_paired']} "
            f"probes={lane['probe_requests']} "
            f"completed={lane['completed']} capacity={lane['capacity']} "
            f"client_disconnect={lane['client_disconnect']} "
            f"upstream_error={lane['upstream_error']}"
        )
        lines.append(
            "  header "
            f"p50={_format_metric(lane['header_completed_p50_ms'], 'ms')} "
            f"p90={_format_metric(lane['header_completed_p90_ms'], 'ms')} "
            "first_byte "
            f"p50={_format_metric(lane['first_byte_p50_ms'], 'ms')} "
            f"p90={_format_metric(lane['first_byte_p90_ms'], 'ms')}"
        )
        attribution = lane["slow_5s_attribution"]
        lines.append(
            "  first_output_ttft "
            f"p50={_format_metric(lane['first_output_p50_ms'], 'ms')} "
            f"p90={_format_metric(lane['first_output_p90_ms'], 'ms')} "
            "first_text "
            f"p50={_format_metric(lane['first_text_p50_ms'], 'ms')} "
            f"p90={_format_metric(lane['first_text_p90_ms'], 'ms')} "
            f"queue p50={_format_metric(lane['queue_ms_p50'], 'ms')} "
            f"p90={_format_metric(lane['queue_ms_p90'], 'ms')} "
            f"decode p50={_format_metric(lane['decode_ms_p50'], 'ms')} "
            f"p90={_format_metric(lane['decode_ms_p90'], 'ms')} "
            f"slow_5s=queue:{attribution['queue']},"
            f"upstream:{attribution['upstream']},decode:{attribution['decode']}"
        )
        lines.append(
            "  stream_duration "
            f"p50={_format_metric(lane['stream_duration_p50_s'], 's')} "
            f"p90={_format_metric(lane['stream_duration_p90_s'], 's')} "
            "total_duration "
            f"p50={_format_metric(lane['total_duration_p50_s'], 's')} "
            f"p90={_format_metric(lane['total_duration_p90_s'], 's')} "
            f"paired={lane['stream_paired_completed']}/{lane['completed']} "
            "sse_bytes_per_s "
            f"p50={_format_metric(lane['sse_bytes_per_s_p50'], 'bps')} "
            f"p90={_format_metric(lane['sse_bytes_per_s_p90'], 'bps')} "
            "output_tokens_per_s "
            f"p50={_format_metric(lane['output_tokens_per_s_p50'])} "
            f"p90={_format_metric(lane['output_tokens_per_s_p90'])} "
            f"reports={lane['output_rate_reports']} "
            f"tiny_sse_completed={lane['tiny_sse_completed']} "
            f"empty_text_completed={lane['empty_text_completed']} "
            f"truncated_text_completed={lane['truncated_text_completed']} "
            f"text_output_completed={lane['text_output_completed']} "
            f"reasoning_only_completed={lane['reasoning_only_completed']} "
            f"tool_call_completed={lane['tool_call_completed']} "
            f"unexplained_output_origins={lane['unexplained_output_origins']} "
            f"non_sse_completed={lane['non_sse_completed']}"
        )
        lines.append(
            "  tokens "
            f"reports={lane['token_reports']} "
            f"in={_optional_int(lane['tokens_input'])} "
            f"cached={_optional_int(lane['tokens_cached'])} "
            f"uncached={_optional_int(lane['tokens_uncached'])} "
            f"out={_optional_int(lane['tokens_output'])} "
            f"cache_hit_ratio={_format_metric(lane['cache_hit_ratio'])} "
            f"accounting={lane['cache_accounting']}"
        )
        if lane["cost_reports"]:
            lines.append(
                "  cost "
                f"reports={lane['cost_reports']} "
                f"usd={lane['cost_usd']:.8f} "
                f"zero_cost_reports={lane['zero_cost_reports']}"
            )
    lines.append(
        "-- coverage: admission journal only; aggregates only; no prompts, "
        "request IDs, client keys, or credentials; sse_bytes_per_s is a "
        "transport proxy, not tokens/s; token sums come from upstream usage "
        "fields only when the provider reported them (reports=N is the support "
        "for cache_hit_ratio; providers may report usage_in as total input or "
        "uncached input, and the audit switches to cached+usage_in when "
        "cached_tokens exceeds usage_in); cost_usd is provider-reported numeric "
        "spend when present, not proof of free capacity; empty_text/truncated_text come from "
        "the additive sse_text_finish allowlist field when deployed; "
        "text_output comes only from explicit --origin canary dumps; natural "
        "reasoning_only_completed and tool_call_completed use admission's "
        "bounded sse_text_finish shape and never retain response bodies; "
        "tool_call_completed includes tool_call, tool_calls, function_call, "
        "and tool_use (an agentic turn that ended on a tool call, not an empty "
        "answer); peak_60s is a half-open "
        "rolling window on completion timestamps; slow_5s counts one request "
        "in each stage that exceeded five seconds (categories can overlap); "
        "admit_peak_60s uses lane_admit when present and a bounded derivation "
        "otherwise; output_tokens_per_s is a stream-window estimate, not a "
        "provider benchmark; its denominator starts at the first upstream byte "
        "and excludes admission queue and TTFB; first_text is the first "
        "non-empty visible text event when the deployed build reports it; "
        "health-probe counts remain visible but probe records are excluded "
        "from natural latency/protocol/cache/cost aggregates --"
    )
    return lines


def _remote_upload_command(source: bytes) -> str:
    """Build one bounded compressed source upload.

    Plain base64 is too large for Windows ``CreateProcess`` arguments. The
    compressed payload stays below the launcher budget and is decoded directly
    into the private remote temp file before the journal reader starts.
    """

    payload = base64.b64encode(zlib.compress(source, level=9)).decode("ascii")
    if len(payload) > 24000:
        raise ValueError(
            "compressed remote audit payload exceeds the SSH command budget"
        )
    return (
        "printf %s '"
        + payload
        + "' | base64 -d | python3 -c 'import sys,zlib;sys.stdout.buffer.write("
        + 'zlib.decompress(sys.stdin.buffer.read()))\' > "$tmp"'
    )


REMOTE_TRANSFER_HARD_TIMEOUT_SECONDS = 900
_STAGED_PREAMBLE_MARKER = b"from __future__ import annotations\n"
_STAGED_IMPORT_BLOCK = (
    b"try:\n"
    b"    from scripts.audit_cli import make_streams_lossy_safe\n"
    b"except ModuleNotFoundError:\n"
    b"    if __package__:\n"
    b"        raise\n"
    b"    from bootstrap import ensure_repo_imports  # type: ignore[import-not-found]\n"
    b"\n"
    b"    ensure_repo_imports()\n"
    b"    from scripts.audit_cli import (  # type: ignore[import-not-found]\n"
    b"        make_streams_lossy_safe,\n"
    b"    )\n"
)


def staged_remote_source(script_path: pathlib.Path) -> bytes:
    """Build a one-file audit upload without an editable local checkout."""

    script_path = script_path.resolve()
    audit_source = script_path.read_bytes()
    if (
        audit_source.count(_STAGED_PREAMBLE_MARKER) != 1
        or audit_source.count(_STAGED_IMPORT_BLOCK) != 1
    ):
        raise RuntimeError("remote audit import block is missing or ambiguous")
    preamble = (
        _STAGED_PREAMBLE_MARKER.decode()
        + '__doc__ = "Read-only natural-traffic audit for the CPA admission journal."\n'
    )
    adapter = (
        "def make_streams_lossy_safe() -> None:\n"
        "    sys.stdin.reconfigure(errors='replace')\n"
        "    sys.stdout.reconfigure(errors='replace')\n"
        "    sys.stderr.reconfigure(errors='replace')\n"
    )
    source = preamble.encode("utf-8") + audit_source.replace(
        _STAGED_PREAMBLE_MARKER, b"", 1
    ).replace(_STAGED_IMPORT_BLOCK, adapter.encode("utf-8"), 1)
    if str(script_path.parent) in source.decode("utf-8"):
        raise RuntimeError("staged upload contains a local checkout path")
    compile(source, str(script_path), "exec")
    return source


def staged_remote_bootstrap_check(script_path: pathlib.Path) -> str:
    """Parse the staged upload without importing or executing its main path."""

    source = staged_remote_source(script_path)
    syntax = ast.parse(source, filename=str(script_path))
    imports = [
        node
        for node in ast.walk(syntax)
        if isinstance(node, ast.ImportFrom)
        and node.module in {"bootstrap", "scripts", "scripts.audit_cli"}
    ]
    if imports:
        raise RuntimeError("staged audit depends on a local package import")
    if not any(
        isinstance(node, ast.FunctionDef)
        and node.name == "make_streams_lossy_safe"
        and not node.decorator_list
        for node in syntax.body
    ):
        raise RuntimeError("staged audit stream adapter is missing")
    return "STAGED_BOOTSTRAP_PARSE_OK"


def _analyse_remotely(profile: str, hours: float) -> tuple[int, str, str]:
    source = staged_remote_source(pathlib.Path(__file__))
    ssh_tool = pathlib.Path(__file__).resolve().parent.parent / "ssh_tool.py"
    appdata = os.environ.get("APPDATA")
    config = (
        pathlib.Path(appdata) / "vps-ssh-launcher" / "target.json" if appdata else None
    )
    config_args = (
        ["--config", str(config)] if config is not None and config.exists() else []
    )
    with tempfile.TemporaryDirectory() as temporary:
        response_path = pathlib.Path(temporary) / "response.json"
        with response_path.open("w", encoding="utf-8") as response:
            upload = _remote_upload_command(source)
            final = (
                "set -o pipefail; umask 077; "
                "tmp=$(mktemp /tmp/cpa-natural-traffic.XXXXXXXXXX.py) || exit 1; "
                "trap 'rm -f -- \"$tmp\"' EXIT; "
                + upload
                + " && "
                + f"journalctl -u cpa-admission --since '{hours:g} hours ago' "
                + "-o short-iso-precise --no-pager | "
                + f'python3 "$tmp" --remote --hours {hours:g} --json'
            )
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ssh_tool),
                        *config_args,
                        "--profile",
                        profile,
                        "run",
                        "--command",
                        final,
                        "--command-timeout",
                        str(REMOTE_TRANSFER_HARD_TIMEOUT_SECONDS),
                        "--command-hard-timeout",
                        str(REMOTE_TRANSFER_HARD_TIMEOUT_SECONDS),
                    ],
                    stdout=response,
                    # Keep stderr out of the response file: ssh warnings (banner,
                    # "Permanently added") merged into stdout used to make
                    # json.loads fail below even on a successful exit
                    # (cpa_error_dump_forensics.py separates them the same way).
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=REMOTE_TRANSFER_HARD_TIMEOUT_SECONDS + 30,
                )
            except subprocess.TimeoutExpired:
                return (
                    2,
                    "",
                    (
                        "remote audit exceeded its local hard timeout "
                        f"({REMOTE_TRANSFER_HARD_TIMEOUT_SECONDS + 30}s)"
                    ),
                )
        return (
            completed.returncode,
            response_path.read_text(encoding="utf-8", errors="replace"),
            completed.stderr or "",
        )


def _origin_files(values: Sequence[str] | None) -> list[tuple[str, pathlib.Path]]:
    """Resolve one explicit response dump per ``lane/model:path`` argument."""

    parsed: list[tuple[str, pathlib.Path]] = []
    for value in values or ():
        label, separator, raw_path = value.partition(":")
        if not separator or "/" not in label or not raw_path:
            raise ValueError(
                "--origin must be lane/model:/absolute/path/to/response.dump"
            )
        parsed.append((label, pathlib.Path(raw_path)))
    return parsed


def origins_from_dumps(
    files: Sequence[tuple[str, pathlib.Path]],
) -> dict[str, tuple[str, ...]]:
    """Return bounded output-origin observations grouped by explicit lane/model."""

    grouped: dict[str, tuple[str, ...]] = {}
    for label, path in files:
        grouped[label] = (
            _merge_origins(
                grouped.get(label),
                _response_origin(_dump_response_section(path.read_bytes())),
            )
            or ()
        )
    return grouped


def _read_lines(paths: Sequence[pathlib.Path]) -> Iterable[str]:
    """Stream saved journal lines lazily; windows span hundreds of MB."""
    for path in paths:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            yield from handle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--file",
        type=pathlib.Path,
        action="append",
        default=[],
        help="saved journal file; may be repeated; omit to read stdin",
    )
    parser.add_argument(
        "--ssh-profile",
        help="read the live journal through ssh_tool.py and print aggregates",
    )
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--origin",
        action="append",
        default=[],
        metavar="LANE/MODEL:PATH",
        help=(
            "explicit controlled-canary response dump; classifies visible text "
            "without printing or retaining it"
        ),
    )
    parser.add_argument("--remote", action="store_true", help=argparse.SUPPRESS)
    return parser


def _main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    make_streams_lossy_safe()
    if args.hours <= 0:
        parser.error("--hours must be positive")
    if args.ssh_profile and args.origin:
        parser.error(
            "--origin dumps are local-only and are silently ignored by "
            "--ssh-profile; run the canary against --file/stdin instead"
        )
    try:
        origin_files = _origin_files(args.origin)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        origin_sets = origins_from_dumps(origin_files)
    except OSError as exc:
        print(f"origin read error: {exc}", file=sys.stderr)
        return 2
    origins = tuple(
        f"{label}:{state}" for label, states in origin_sets.items() for state in states
    )

    if args.remote:
        records, header_logs = parse_journal(sys.stdin)
        print(
            json.dumps(
                summarize(records, args.hours, header_logs, origins),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.ssh_profile:
        returncode, output, remote_stderr = _analyse_remotely(
            args.ssh_profile, args.hours
        )
        if returncode != 0:
            sys.stderr.write(remote_stderr or output)
            return returncode or 2
        if args.json:
            sys.stdout.write(output)
            return 0
        try:
            print("\n".join(render(json.loads(output))))
        except json.JSONDecodeError as exc:
            print(f"remote audit produced invalid JSON: {exc}", file=sys.stderr)
            sys.stderr.write(remote_stderr)
            return 2
        return 0

    try:
        lines = _read_lines(args.file) if args.file else sys.stdin
        records, header_logs = parse_journal(lines)
    except OSError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    summary = summarize(records, args.hours, header_logs, origins)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("\n".join(render(summary)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

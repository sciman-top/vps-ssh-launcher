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
only.  It never prints prompts, response text, request IDs, client keys, or
credential material.  ``sse_bytes_per_s`` is a transport proxy for output
speed; it is not a token-throughput measurement.

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
import base64
import json
import math
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

_TIMESTAMP = re.compile(r"^(\S+)")
_FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
_EMPTY_TEXT_FINISH_REASONS = frozenset({"empty_text"})
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
    """Return the bounded finish reason attached to a user-visible text part."""

    observed = dict(_FIELD.findall(line)).get("sse_text_finish")
    if observed in _EMPTY_TEXT_FINISH_REASONS | _TRUNCATED_TEXT_FINISH_REASONS:
        return observed
    return None


def _token_triplet(container: Any) -> tuple[int | None, int | None, int | None]:
    """Extract numeric input/cached/output counts from one usage mapping."""

    if not isinstance(container, dict):
        return None, None, None
    prompt_details = container.get("prompt_tokens_details")
    input_details = container.get("input_tokens_details")
    candidates = [container]
    candidates.extend(
        item for item in (prompt_details, input_details) if isinstance(item, dict)
    )

    def number(names: tuple[str, ...]) -> int | None:
        for mapping in candidates:
            for name in names:
                value = mapping.get(name)
                if type(value) is int and value >= 0:
                    return value
        return None

    return (
        number(("input_tokens", "prompt_tokens", "promptTokenCount")),
        number(
            (
                "cached_tokens",
                "cache_read_input_tokens",
                "cachedContentTokenCount",
                "prompt_cache_hit_tokens",
            )
        ),
        number(("output_tokens", "completion_tokens", "candidatesTokenCount")),
    )


def _usage_from_json(payload: bytes) -> tuple[int | None, int | None, int | None]:
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeError):
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    response = data.get("response")
    found = (
        _token_triplet(candidate)
        for candidate in (
            data.get("usage"),
            response.get("usage") if isinstance(response, dict) else None,
            data.get("usageMetadata"),
        )
    )
    return next(
        (item for item in found if any(value is not None for value in item)),
        (None, None, None),
    )


def _usage_from_sse(payload: bytes) -> tuple[int | None, int | None, int | None]:
    """Merge usage components carried in separate SSE events, metadata only."""

    input_tokens = cached_tokens = output_tokens = None
    for raw in payload.splitlines():
        if not raw.startswith(b"data:"):
            continue
        try:
            event = json.loads(raw[5:].strip())
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict):
            continue
        response = event.get("response")
        message = event.get("message")
        candidates = [
            event.get("usage"),
            response.get("usage") if isinstance(response, dict) else None,
            message.get("usage") if isinstance(message, dict) else None,
            event.get("usageMetadata"),
            response.get("usage_metadata") if isinstance(response, dict) else None,
        ]
        for candidate in candidates:
            cin, ccached, cout = _token_triplet(candidate)
            input_tokens = cin if cin is not None else input_tokens
            cached_tokens = ccached if ccached is not None else cached_tokens
            output_tokens = cout if cout is not None else output_tokens
    return input_tokens, cached_tokens, output_tokens


def _recover_usage_from_dump(
    path: pathlib.Path,
) -> tuple[int | None, int | None, int | None]:
    """Best-effort usage recovery from one short diagnostic request dump.

    This is not a default source.  CPA's request dumps may retain prompts and
    credentials, so only an explicitly supplied single dump is read, and only
    response-side usage fields leave this function.
    """

    try:
        raw = path.read_bytes()
    except OSError:
        return None, None, None
    body = _dump_response_section(raw)
    usage = _usage_from_sse(body)
    if any(value is not None for value in usage):
        return usage
    return _usage_from_json(body)


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
            # Pairing the result to this line therefore measures the stream
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
        sse_inspection = fields.get("sse_inspection_complete") or "na"
        admission_ts = admission_logs.get(request_id) if request_id else None
        admission_source = "lane_admit" if admission_ts is not None else "unavailable"
        if admission_ts is None and header_at is not None and header_ms is not None:
            if waited_ms is not None and sse_inspection != "na":
                # A lane-owned SSE request commits its downstream headers before
                # queueing, so the admit instant is the logged header time plus
                # the measured queue wait.
                admission_ts = header_at + timedelta(milliseconds=waited_ms)
                admission_source = "derived_sse"
            else:
                # A non-SSE request logs downstream headers only after the
                # upstream has answered; subtract that upstream header time.
                admission_ts = header_at - timedelta(milliseconds=header_ms)
                admission_source = "derived_non_sse"
        if admission_ts is None or admission_ts > timestamp:
            admission_ts = None
            admission_source = "unavailable"
        record = {
            "waited_ms": waited_ms,
            "header_ms": header_ms,
            "first_byte_ms": first_byte_ms,
            "header_at": header_at,
            "sse_inspection": sse_inspection,
            "lane": fields.get("lane") or "unknown",
            "observed_model": fields.get("model") or "unknown",
        }
        total_duration_ms, first_output_ms = _metrics(record, timestamp)
        stream_duration_s = None
        if header_at is not None and timestamp >= header_at:
            stream_duration_s = (timestamp - header_at).total_seconds()
        first_output_at = (
            timestamp - timedelta(milliseconds=first_output_ms)
            if first_output_ms is not None
            else None
        )
        records.append(
            {
                "lane": record["lane"],
                "model": record["observed_model"],
                "status": _parse_int(fields.get("status")),
                "capacity": fields.get("capacity") == "true",
                "successful": fields.get("successful") == "true",
                "protocol_result": fields.get("protocol_result") or "unknown",
                "waited_ms": waited_ms,
                "header_ms": header_ms,
                "first_byte_ms": first_byte_ms,
                "sse_bytes": _parse_int(fields.get("sse_bytes")) or 0,
                "sse_events": _parse_int(fields.get("sse_events")) or 0,
                "sse_inspection": sse_inspection,
                "terminal_seen": fields.get("sse_terminal_seen") == "true",
                "text_finish": _text_finish_reason(line),
                "first_output_ms": first_output_ms,
                "total_duration_ms": total_duration_ms,
                "first_output_at": first_output_at,
                "usage_in": _parse_int(fields.get("usage_in")),
                "usage_cached": _parse_int(fields.get("usage_cached")),
                "usage_out": _parse_int(fields.get("usage_out")),
                "probe": request_id in probe_ids if request_id else False,
                "admission_ts": admission_ts,
                "admission_source": admission_source,
                "result_ts": timestamp,
                "stream_duration_s": stream_duration_s,
            }
        )
    return records, header_lines


def _values(records: Sequence[dict[str, Any]], key: str) -> list[float]:
    return [
        float(value) for record in records if (value := record.get(key)) is not None
    ]


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
    return _OUTPUT_EMPTY if b'"text":"stop"' in body else _OUTPUT_REASONING_ONLY


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
        results = [record["protocol_result"] for record in items]
        completed = [
            record for record in items if record["protocol_result"] == "completed"
        ]
        capacity = [
            record for record in items if record["protocol_result"] == "capacity"
        ]
        disconnects = [
            record
            for record in items
            if record["protocol_result"] == "client_disconnect"
        ]
        upstream_errors = [
            record for record in items if record["protocol_result"] == "upstream_error"
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
        upstream_ttft_ms = [
            max(0.0, record["first_byte_ms"] - record["waited_ms"])
            for record in completed
            if record.get("first_byte_ms") is not None
            and record.get("waited_ms") is not None
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
            admission_ts = record.get("admission_ts")
            if tokens is None or tokens <= 0 or admission_ts is None:
                continue
            first_byte_ms = record["first_byte_ms"]
            if first_byte_ms is None:
                continue
            decode_seconds = (
                record["result_ts"] - admission_ts
            ).total_seconds() - first_byte_ms / 1000
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
        empty_text += [
            state for state in text_finishes if state in _EMPTY_TEXT_FINISH_REASONS
        ]
        truncated_text += [
            state for state in text_finishes if state in _TRUNCATED_TEXT_FINISH_REASONS
        ]
        non_sse = [record for record in completed if record["sse_inspection"] == "na"]
        # Token/cache telemetry: only requests whose upstream reported usage.
        # Sums are observation-only; the ratio is cached/input over reports that
        # carried an input count, so a partially reported window cannot inflate
        # or deflate it silently (token_reports states the denominator).
        token_reports = [r for r in items if r["usage_in"] is not None]
        tokens_input = sum(r["usage_in"] for r in token_reports)
        tokens_output = sum(
            r["usage_out"] for r in token_reports if r["usage_out"] is not None
        )
        tokens_cached = sum(
            r["usage_cached"] for r in token_reports if r["usage_cached"] is not None
        )
        tokens_uncached = max(0, tokens_input - tokens_cached)
        admission_times: list[datetime] = []
        for record in items:
            admission_ts = record.get("admission_ts")
            if isinstance(admission_ts, datetime) and not record.get("probe"):
                admission_times.append(admission_ts)
        admit_peak_60s = (
            _peak_in_window(admission_times, 60) if admission_times else None
        )
        cache_hit_ratio = tokens_cached / tokens_input if tokens_input > 0 else None
        span_minutes = 0.0
        if len(items) > 1:
            result_times = [record["result_ts"] for record in items]
            span_minutes = (max(result_times) - min(result_times)).total_seconds() / 60
        lanes.append(
            {
                "lane_model": name,
                "requests": len(items),
                "avg_rpm": len(items) / (window_hours * 60)
                if window_hours > 0
                else None,
                "observed_span_minutes": round(span_minutes, 1),
                "peak_60s": _peak_in_window(
                    [record["result_ts"] for record in items], 60
                ),
                "admit_peak_60s": admit_peak_60s,
                "admission_paired": len(admission_times),
                "probe_requests": sum(1 for record in items if record.get("probe")),
                "completed": len(completed),
                "capacity": len(capacity),
                "client_disconnect": len(disconnects),
                "upstream_error": len(upstream_errors),
                "other": len(items)
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
                "unexplained_output_origins": len(unexplained),
                "non_sse_completed": len(non_sse),
                "token_reports": len(token_reports),
                "tokens_input": tokens_input if token_reports else None,
                "tokens_cached": tokens_cached if token_reports else None,
                "tokens_uncached": tokens_uncached if token_reports else None,
                "tokens_output": tokens_output if token_reports else None,
                "cache_hit_ratio": cache_hit_ratio,
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
            lane["token_reports"] >= 10
            and (lane["tokens_input"] or 0) >= 1_000_000
            and cache_ratio is not None
            and cache_ratio < 0.2
        ):
            warnings.append(
                f"{name} cache_hit_ratio={cache_ratio:.2f} across "
                f"{lane['tokens_input']} input tokens; inspect prompt-prefix "
                "stability and session affinity before changing cache keys"
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


def _token(value: int | None) -> str:
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
            f"unexplained_output_origins={lane['unexplained_output_origins']} "
            f"non_sse_completed={lane['non_sse_completed']}"
        )
        lines.append(
            "  tokens "
            f"reports={lane['token_reports']} "
            f"in={_token(lane['tokens_input'])} "
            f"cached={_token(lane['tokens_cached'])} "
            f"uncached={_token(lane['tokens_uncached'])} "
            f"out={_token(lane['tokens_output'])} "
            f"cache_hit_ratio={_format_metric(lane['cache_hit_ratio'])}"
        )
    lines.append(
        "-- coverage: admission journal only; aggregates only; no prompts, "
        "request IDs, client keys, or credentials; sse_bytes_per_s is a "
        "transport proxy, not tokens/s; token sums come from upstream usage "
        "fields only when the provider reported them (reports=N is the "
        "denominator for cache_hit_ratio); empty_text/truncated_text come from "
        "the additive sse_text_finish allowlist field when deployed; "
        "text_output/reasoning_only come only from explicit --origin canary "
        "dumps, never from natural response bodies; peak_60s is a half-open "
        "rolling window on completion timestamps; slow_5s counts one request "
        "in each stage that exceeded five seconds (categories can overlap); "
        "admit_peak_60s uses lane_admit when present and a bounded derivation "
        "otherwise; output_tokens_per_s is a stream-window estimate, not a "
        "provider benchmark --"
    )
    return lines


def _remote_payload() -> str:
    return base64.b64encode(pathlib.Path(__file__).read_bytes()).decode("ascii")


def _analyse_remotely(profile: str, hours: float) -> tuple[int, str]:
    command = (
        "set -o pipefail; umask 077; "
        "tmp=$(mktemp /tmp/cpa-natural-traffic.XXXXXXXXXX.py) || exit 1; "
        "trap 'rm -f -- \"$tmp\"' EXIT; "
        f"printf %s '{_remote_payload()}' | base64 -d > \"$tmp\" && "
        f"journalctl -u cpa-admission --since '{hours:g} hours ago' "
        "-o short-iso-precise --no-pager | "
        f'python3 "$tmp" --remote --hours {hours:g} --json'
    )
    ssh_tool = pathlib.Path(__file__).resolve().parent.parent / "ssh_tool.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(ssh_tool),
            "--profile",
            profile,
            "run",
            "--command",
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout + completed.stderr


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


def _read_lines(paths: Sequence[pathlib.Path]) -> list[str]:
    lines: list[str] = []
    for path in paths:
        lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return lines


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.hours <= 0:
        parser.error("--hours must be positive")
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
        returncode, output = _analyse_remotely(args.ssh_profile, args.hours)
        if returncode != 0:
            sys.stderr.write(output)
            return returncode or 2
        if args.json:
            sys.stdout.write(output)
        else:
            print("\n".join(render(json.loads(output))))
        return 0

    try:
        lines = _read_lines(args.file) if args.file else list(sys.stdin)
    except OSError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    records, header_logs = parse_journal(lines)
    summary = summarize(records, args.hours, header_logs, origins)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("\n".join(render(summary)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

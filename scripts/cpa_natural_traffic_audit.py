#!/usr/bin/env python3
"""Read-only natural-traffic audit for the CPA admission journal.

The admission journal already records per-request latency, capacity scope,
protocol completion, and SSE byte counts.  What it did not have was a compact
way to answer the operational questions that keep coming back:

* Which lane/model is currently carrying a burst close to a provider's limit?
* Is a slow turn waiting in admission, or is the upstream header itself slow?
* How much output bytes per second does a completed stream actually deliver?

This tool reads the journal on the host (or a saved copy) and emits aggregates
only.  It never prints prompts, response text, request IDs, client keys, or
credential material.  ``sse_bytes_per_s`` is a transport proxy for output
speed; it is not a token-throughput measurement.

Examples::

    ./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --hours 24
    ./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --ssh-profile bwg --json
    journalctl -u cpa-admission -o short-iso-precise --no-pager > /tmp/cpa.log
    ./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --file /tmp/cpa.log
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
from datetime import datetime
from typing import Any, Iterable, Sequence

_TIMESTAMP = re.compile(r"^(\S+)")
_FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")


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

    ordered = sorted(timestamps)
    peak = 0
    start = 0
    for end, current in enumerate(ordered):
        while (current - ordered[start]).total_seconds() > seconds:
            start += 1
        peak = max(peak, end - start + 1)
    return peak


def parse_journal(
    lines: Iterable[str],
) -> tuple[list[dict[str, Any]], int]:
    """Pair request-start lines with upstream-result lines.

    The result line is the authoritative completion record.  A start line is
    used only to derive total duration; if it is outside the captured window,
    duration-based metrics stay ``None`` rather than guessing.
    """

    header_logs: dict[str, datetime] = {}
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
        stream_duration_s = None
        if header_at is not None and timestamp >= header_at:
            stream_duration_s = (timestamp - header_at).total_seconds()
        records.append(
            {
                "lane": fields.get("lane") or "unknown",
                "model": fields.get("model") or "unknown",
                "status": _parse_int(fields.get("status")),
                "capacity": fields.get("capacity") == "true",
                "successful": fields.get("successful") == "true",
                "protocol_result": fields.get("protocol_result") or "unknown",
                "waited_ms": _parse_int(fields.get("waited_ms")),
                "header_ms": _parse_int(fields.get("upstream_header_ms")),
                "first_byte_ms": _parse_int(fields.get("upstream_first_byte_ms")),
                "sse_bytes": _parse_int(fields.get("sse_bytes")) or 0,
                "sse_events": _parse_int(fields.get("sse_events")) or 0,
                "sse_inspection": fields.get("sse_inspection_complete") or "na",
                "terminal_seen": fields.get("sse_terminal_seen") == "true",
                "result_ts": timestamp,
                "stream_duration_s": stream_duration_s,
            }
        )
    return records, header_lines


def _values(
    records: Sequence[dict[str, Any]], key: str
) -> list[float]:
    return [float(value) for record in records if (value := record.get(key)) is not None]


def summarize(
    records: Sequence[dict[str, Any]], window_hours: float, header_logs: int = 0
) -> dict[str, Any]:
    """Aggregate by lane/model without retaining request-level identifiers."""

    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(f"{record['lane']}/{record['model']}", []).append(record)

    lanes: list[dict[str, Any]] = []
    for name in sorted(groups):
        items = groups[name]
        results = [record["protocol_result"] for record in items]
        completed = [record for record in items if record["protocol_result"] == "completed"]
        capacity = [record for record in items if record["protocol_result"] == "capacity"]
        disconnects = [
            record for record in items if record["protocol_result"] == "client_disconnect"
        ]
        upstream_errors = [
            record for record in items if record["protocol_result"] == "upstream_error"
        ]
        stream_durations = _values(completed, "stream_duration_s")
        total_durations = [
            record["header_ms"] / 1000 + record["stream_duration_s"]
            for record in completed
            if record["header_ms"] is not None
            and record["stream_duration_s"] is not None
        ]
        throughput = [
            record["sse_bytes"] / record["stream_duration_s"]
            for record in completed
            if record["sse_bytes"] > 0
            and record["stream_duration_s"] is not None
            and record["stream_duration_s"] > 0
        ]
        tiny_sse = [
            record
            for record in completed
            if record["sse_inspection"] == "true"
            and (record["sse_events"] <= 3 or record["sse_bytes"] < 1200)
        ]
        non_sse = [record for record in completed if record["sse_inspection"] == "na"]
        span_minutes = 0.0
        if len(items) > 1:
            result_times = [record["result_ts"] for record in items]
            span_minutes = (
                max(result_times) - min(result_times)
            ).total_seconds() / 60
        lanes.append(
            {
                "lane_model": name,
                "requests": len(items),
                "avg_rpm": len(items) / (window_hours * 60) if window_hours > 0 else None,
                "observed_span_minutes": round(span_minutes, 1),
                "peak_60s": _peak_in_window(
                    [record["result_ts"] for record in items], 60
                ),
                "completed": len(completed),
                "capacity": len(capacity),
                "client_disconnect": len(disconnects),
                "upstream_error": len(upstream_errors),
                "other": len(items) - len(completed) - len(capacity) - len(disconnects)
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
                "first_byte_p90_ms": _percentile(
                    _values(completed, "first_byte_ms"), 0.9
                ),
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
                "tiny_sse_completed": len(tiny_sse),
                "non_sse_completed": len(non_sse),
                "protocol_results": {
                    result: results.count(result) for result in sorted(set(results))
                },
            }
        )
    warnings: list[str] = []
    for lane in lanes:
        name = lane["lane_model"]
        if name.startswith("workbuddy-oauth/") and lane["peak_60s"] >= 13:
            warnings.append(
                f"{name} peak_60s={lane['peak_60s']} is at/above the community "
                "14003 range (13-19 rpm); reduce burstiness, do not rotate "
                "accounts or egress IPs"
            )
        if name == "chatgpt-oauth/gpt-6-luna" and lane["peak_60s"] >= 15:
            warnings.append(
                f"{name} peak_60s={lane['peak_60s']} is at/above OpenAI's "
                "~15/min prompt-cache routing guidance; prefer fewer parallel "
                "title/summary requests"
            )
        if lane["tiny_sse_completed"]:
            warnings.append(
                f"{name} tiny_sse_completed={lane['tiny_sse_completed']}; "
                "inspect credits/protocol before attributing it to an account "
                "or treating it as proof of empty output"
            )
    return {
        "window_hours": window_hours,
        "records": len(records),
        "header_logs": header_logs,
        "lanes": lanes,
        "warnings": warnings,
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
            f"peak_60s={lane['peak_60s']} "
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
            f"tiny_sse_completed={lane['tiny_sse_completed']} "
            f"non_sse_completed={lane['non_sse_completed']}"
        )
    lines.append(
        "-- coverage: admission journal only; aggregates only; no prompts, "
        "request IDs, client keys, or credentials; sse_bytes_per_s is a "
        "transport proxy, not tokens/s; peak_60s counts upstream_result "
        "timestamps --"
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
        f"python3 \"$tmp\" --remote --hours {hours:g} --json"
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
    parser.add_argument("--remote", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.hours <= 0:
        parser.error("--hours must be positive")

    if args.remote:
        records, header_logs = parse_journal(sys.stdin)
        print(
            json.dumps(
                summarize(records, args.hours, header_logs),
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
    summary = summarize(records, args.hours, header_logs)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("\n".join(render(summary)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

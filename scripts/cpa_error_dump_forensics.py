#!/usr/bin/env python3
"""Read-only forensics on retained CLIProxyAPI error dumps.

Answers one recurring question with evidence instead of a hypothesis: **does an
upstream capacity response carry a signal the deployed admission gate does not
recognise?**

That question decides whether the admission contract should change, and it has
been answered wrongly before by reasoning from a client-visible symptom instead
of from the bytes on the wire. Two candidate widenings were proposed and both
died here on 2026-10-04: the ChatGPT backend sends neither ``x-ratelimit-*`` nor
a ``resets_at`` body field, and every retained capacity response is a plain
``503`` + ``Retry-After`` + ``server_is_overloaded`` -- all three already
handled. No admission change was warranted, and this tool is the reason that is
a measurement rather than an opinion.

Design rules:

* **Response-side sections only.** A dump also contains the request body, which
  carries user prompt text; that contamination produced phantom markers in the
  2026-09-21 doctor review. Nothing outside the ``=== ... ===`` response sections
  is parsed, and only header *names* plus a bounded value prefix are emitted.
* **The marker vocabulary comes from the admission config**, so the verdict is
  "would the deployed gate have counted this?", not a second opinion.
* **Remote mode analyses on the host and prints only aggregates**, so no prompt
  text ever leaves the machine.

Exit codes: 0 every capacity response is recognised, 1 an unrecognised signal was
found, 2 usage/IO error **or an input with no response section at all** (a file
without the `=== ... ===` banners is not a raw dump, and reporting it as "nothing
to see" would read as a clean bill of health).
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import subprocess
import sys
from typing import Any, Sequence

DEFAULT_ADMISSION_CONFIG = (
    pathlib.Path(__file__).parent / "remote" / "cpa-admission.json"
)
REMOTE_LOG_DIR = pathlib.Path("/opt/cliproxyapi/auth/logs")
REMOTE_ADMISSION_CONFIG = pathlib.Path("/opt/cliproxyapi/cpa-admission.json")

# The dump marks each section with a `=== <name> ===` banner. Only these three
# carry the upstream's response; everything else may echo the request body.
RESPONSE_SECTIONS = frozenset(
    {"=== api error response ===", "=== api response ===", "=== response ==="}
)

# Header names that can express a capacity or backoff signal. Everything else is
# noise, and some of it (request ids, installation ids) is client-identifying, so
# the allow-list is deliberate rather than convenient.
INTERESTING_HEADERS = frozenset(
    {
        "retry-after",
        "x-retry-metadata",
        "x-should-retry",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-tokens",
    }
)
HEADER_VALUE_LIMIT = 40

# Tokens that mean "the shared account is out of capacity" in an upstream's own
# vocabulary. A hit that the configured markers and statuses do not already cover
# is exactly the finding this tool exists to produce.
CANDIDATE_MARKERS = (
    "auth_unavailable",
    "insufficient_quota",
    "quota exceeded",
    "too many requests",
    "overloaded",
    "at capacity",
)

_STATUS_LINE = re.compile(r"(?im)^\s*(?:Status:\s*|HTTP/\d(?:\.\d)?\s+)(\d{3})")
_HEADER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9-]{1,40}):\s*(.*)$")
# The underlying cause, when CPA wraps an upstream failure. Measured shapes:
#   ... last upstream error: server_is_overloaded: Our servers are currently overloaded.
#   ... last upstream error: upstream_error: Upstream access forbidden, please contact administrator
# Both arrive as 503 and are therefore *counted* by the admission, but only the
# first is account capacity; the second is the provider refusing service. Without
# this field a `recognised` verdict reads as "the account is overloaded".
_LAST_UPSTREAM = re.compile(r"last upstream error:\s*([^)\"]{1,120})", re.I)
_ERROR_TYPE = re.compile(r'"type"\s*:\s*"([a-z_]{1,40})"', re.I)


def response_text(raw: str) -> str:
    """Return only the response-side sections of a dump."""

    kept: list[str] = []
    keep = False
    for line in raw.splitlines():
        marker = line.strip().lower()
        if marker.startswith("=== ") and marker.endswith(" ==="):
            keep = marker in RESPONSE_SECTIONS
            continue
        if keep:
            kept.append(line)
    return "\n".join(kept)


def admission_vocabulary(
    config: dict[str, Any],
) -> tuple[frozenset[str], frozenset[int]]:
    """Union of the markers and capacity statuses the deployed lanes recognise."""

    markers: set[str] = set()
    statuses: set[int] = set()
    for lane in config.get("lanes") or []:
        markers.update(
            str(item).strip().lower() for item in (lane.get("capacity_markers") or [])
        )
        statuses.update(
            int(item)
            for item in (lane.get("capacity_statuses") or [])
            if isinstance(item, int)
        )
    return frozenset(markers), frozenset(statuses)


def census(text: str) -> dict[str, Any]:
    statuses = sorted({int(value) for value in _STATUS_LINE.findall(text)})
    headers: dict[str, str] = {}
    for line in text.splitlines():
        match = _HEADER_LINE.match(line.strip())
        if match is None:
            continue
        name = match.group(1).lower()
        if name in INTERESTING_HEADERS and name not in headers:
            headers[name] = match.group(2).strip()[:HEADER_VALUE_LIMIT]
    lowered = text.lower()
    cause = _LAST_UPSTREAM.search(text)
    return {
        "response_bytes": len(text),
        "statuses": statuses,
        "headers": headers,
        "candidates": sorted(
            {token for token in CANDIDATE_MARKERS if token in lowered}
        ),
        "upstream_cause": cause.group(1).strip() if cause else "",
        "error_types": sorted(set(_ERROR_TYPE.findall(text))),
    }


def verdict(
    facts: dict[str, Any],
    statuses: frozenset[int],
) -> tuple[str, list[str]]:
    """Decide whether the admission gate would have counted this response."""

    covered_by_status = [status for status in facts["statuses"] if status in statuses]
    recognised_markers = [token for token in facts["markers"] if token]
    if covered_by_status or recognised_markers:
        reasons: list[str] = []
        if covered_by_status:
            reasons.append(
                "status " + ",".join(str(item) for item in covered_by_status)
            )
        if recognised_markers:
            reasons.append("marker " + ",".join(recognised_markers))
        return "recognised", reasons
    if facts["candidates"]:
        return "unrecognised", list(facts["candidates"])
    return "other", []


def analyse(
    text: str, markers: frozenset[str], statuses: frozenset[int]
) -> dict[str, Any]:
    facts = census(text)
    if not text.strip():
        # No `=== ... ===` response banner: this is not a raw CPA dump (an
        # already-extracted response, a differently-formatted future dump, or the
        # wrong file). Reporting "not a capacity response; correctly ignored"
        # would read as a clean bill of health for a file that was never analysed
        # at all, so it gets its own verdict and its own exit code.
        facts["markers"] = []
        facts["verdict"] = "unreadable"
        facts["reasons"] = ["no response section"]
        return facts
    lowered = text.lower()
    facts["markers"] = sorted(
        {marker for marker in markers if marker and marker in lowered}
    )
    state, reasons = verdict(facts, statuses)
    facts["verdict"] = state
    facts["reasons"] = reasons
    return facts


def _analyse_files(
    paths: Sequence[pathlib.Path], markers: frozenset[str], statuses: frozenset[int]
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in paths:
        text = response_text(path.read_text(encoding="utf-8", errors="replace"))
        entry = analyse(text, markers, statuses)
        entry["file"] = path.name
        results.append(entry)
    return results


def _remote_payload() -> str:
    source = pathlib.Path(__file__).read_bytes()
    return base64.b64encode(source).decode("ascii")


def _analyse_remotely(profile: str) -> tuple[int, str]:
    command = (
        f"echo {_remote_payload()} | base64 -d > /tmp/cpa-dump-forensics.py && "
        "python3 /tmp/cpa-dump-forensics.py --remote --json; rc=$?; "
        "rm -f /tmp/cpa-dump-forensics.py; exit $rc"
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


def _load_vocabulary(path: pathlib.Path) -> tuple[frozenset[str], frozenset[int]]:
    return admission_vocabulary(json.loads(path.read_text(encoding="utf-8")))


def render(
    results: Sequence[dict[str, Any]], marker_count: int, statuses: frozenset[int]
) -> list[str]:
    lines = ["== CPA error dump forensics =="]
    lines.append(
        f"  vocabulary: {marker_count} configured marker(s), "
        f"capacity_statuses {','.join(str(item) for item in sorted(statuses))}"
    )
    if not results:
        lines.append("  no dump files found")
    for entry in results:
        lines.append(f"  {entry['file']} response_bytes={entry['response_bytes']}")
        if entry["statuses"]:
            lines.append(
                "    statuses: " + ",".join(str(item) for item in entry["statuses"])
            )
        for name, value in sorted(entry["headers"].items()):
            lines.append(f"    header {name}: {value}")
        if entry["markers"]:
            lines.append("    markers(configured): " + ",".join(entry["markers"]))
        if entry["candidates"]:
            lines.append("    candidate tokens: " + ",".join(entry["candidates"]))
        if entry.get("upstream_cause"):
            lines.append("    upstream cause: " + entry["upstream_cause"])
        elif entry.get("error_types"):
            lines.append("    error types: " + ",".join(entry["error_types"]))
        if entry["verdict"] == "recognised":
            lines.append("    verdict: RECOGNISED via " + " + ".join(entry["reasons"]))
            if not entry["markers"]:
                # Recognised on the status code alone: the body carries no
                # configured capacity marker, so "recognised" means "the gate
                # would cool the lane", not "the account is out of capacity".
                lines.append(
                    "    note: recognised by status only, with no configured capacity "
                    "marker in the body -- read the upstream cause before treating this "
                    "as account capacity"
                )
        elif entry["verdict"] == "unrecognised":
            lines.append(
                "    verdict: UNRECOGNISED - the admission gate would count this as a "
                "success; candidate signal(s) " + ",".join(entry["reasons"])
            )
        elif entry["verdict"] == "unreadable":
            lines.append(
                "    verdict: NO RESPONSE SECTION - this file carries no "
                "'=== ... ===' response banner, so it is not a raw CPA dump and "
                "nothing was analysed; do not read this as a clean result"
            )
        else:
            lines.append("    verdict: not a capacity response; correctly ignored")
    recognised = sum(1 for entry in results if entry["verdict"] == "recognised")
    unrecognised = sum(1 for entry in results if entry["verdict"] == "unrecognised")
    unreadable = sum(1 for entry in results if entry["verdict"] == "unreadable")
    lines.append(
        f"-- dumps={len(results)} recognised={recognised} "
        f"unrecognised={unrecognised} unreadable={unreadable} --"
    )
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=pathlib.Path, action="append", default=[])
    parser.add_argument(
        "--ssh-profile",
        help="analyse the newest retained dumps on the host via ssh_tool.py",
    )
    parser.add_argument("--remote", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--admission-config", type=pathlib.Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.remote:
        config_path = REMOTE_ADMISSION_CONFIG
        files = sorted(REMOTE_LOG_DIR.glob("error-*.log"))
    else:
        config_path = args.admission_config or DEFAULT_ADMISSION_CONFIG
        files = list(args.file)

    if args.ssh_profile:
        returncode, output = _analyse_remotely(args.ssh_profile)
        if returncode != 0 and not output.strip().startswith("{"):
            print(output.strip(), file=sys.stderr)
            return 2
        print(output.strip())
        return returncode if returncode in (0, 1, 2) else 1

    try:
        markers, statuses = _load_vocabulary(config_path)
    except (OSError, ValueError) as exc:
        print(f"cannot read admission config: {exc}", file=sys.stderr)
        return 2
    try:
        results = _analyse_files(files, markers, statuses)
    except OSError as exc:
        print(f"cannot read dump: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"markers": sorted(markers), "results": results}, indent=2))
    else:
        for line in render(results, len(markers), statuses):
            print(line)
    if any(entry["verdict"] == "unreadable" for entry in results):
        return 2
    return 1 if any(entry["verdict"] == "unrecognised" for entry in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

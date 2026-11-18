#!/usr/bin/env python3
r"""Attribute cold cache restarts inside one multi-turn conversation.

The admission journal records usage_in / usage_cached per turn and a
client_request_hash derived from the client's request-id header. Two turns
that share that hash belong to one upstream conversation, so their input
lengths should grow monotonically and their cached share should stay high.

On 2026-11-15 a read-only review found conversations where a 240k-token prefix
was established and the next turn reported usage_cached=0. That costs a full
re-prefill (~240k uncached input tokens) and is exactly the shape users report
as "generation suddenly got slow". The cause had not been isolated, and the
obvious candidates pull in opposite directions:

* the gateway session-affinity-ttl (1h) expiring and re-picking a credential;
  the new backend does not share the old backend cache;
* the client switching models inside the same conversation;
* the provider evicting its own prompt cache.

This tool separates those cases from the journal alone. It never sends a
request, never prints prompt text, and never emits a credential or request id.

Classification is deliberately conservative. A cold turn is labelled with the
strongest mechanism the surrounding records can prove:

rebind
    The conversation moved to a different instance_hash. Only a client that
    reports X-Cockpit-Instance-Id can be classified this way.
model_switch
    The previous turn in the same conversation used a different model.
lane_switch
    The previous turn in the same conversation used a different lane.
cache_eviction
    Same instance, same lane, same model, so nothing the gateway chose changed
    and the loss is upstream or a client prefix rewrite.
unattributed
    The preceding turn reported no usage at all, so there is nothing to compare.

A lane_switch turn is the case the gateway can actually fix: the provider
answers the base model name while the route asks for a variant (flash-n for
flash-high, hy4-flash-free for hy4flash-free). Without normalisation those
count as extra model switches and inflate the eviction bucket.

Examples:

    ./.venv/Scripts/python.exe scripts/cpa_cache_reset_probe.py --file adm48.log
    ./.venv/Scripts/python.exe scripts/cpa_cache_reset_probe.py --hours 48 --json
    ./.venv/Scripts/python.exe scripts/cpa_cache_reset_probe.py --ssh-profile bwg

Exit codes: 0 ran (findings are informational), 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Tuple

_UPSTREAM_RESULT = "upstream_result"
_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
_FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")

_MIN_PREFIX_TOKENS_DEFAULT = 20000
_DEFAULT_REMOTE_HOURS = 48.0

_PROVIDER_MODEL_ALIASES = (
    (re.compile(r"^(gemini-3\.8-flash)(?:-n)?$"), r"\1-high"),
    (re.compile(r"^(gemini-4-hydrogen)$"), r"\1-xhigh"),
    (re.compile(r"^(gemini-4-carbon)$"), r"\1-high"),
    (re.compile(r"^(gemini-4-argon)(?:-trial)?$"), r"\1-high"),
    # 2026-11-17: the luode cutover put hy4flash-free upstream (echoed bare);
    # the hy4-free line stays for journal rows from the retired hy4free
    # upstream so historical windows keep a stable label.
    (re.compile(r"^hy4-flash-free$"), "hy4flash-free"),
    (re.compile(r"^hy4-free$"), "hy4free"),
)

_VALUE_ABSENT = "-"


def normalise_model(name: str) -> str:
    """Fold a provider-echoed variant onto the name the route asked for."""

    for pattern, replacement in _PROVIDER_MODEL_ALIASES:
        folded = pattern.sub(replacement, name)
        if folded != name:
            return folded
    return name


def _int_or_none(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _absent(value: Optional[str]) -> Optional[str]:
    if value is None or value == _VALUE_ABSENT:
        return None
    return value


@dataclass(frozen=True)
class Turn:
    """One completed upstream result, reduced to cache-relevant fields."""

    at: str
    lane: str
    model: str
    conversation: Optional[str]
    instance: Optional[str]
    status: Optional[str]
    usage_in: Optional[int]
    usage_cached: Optional[int]
    finish: Optional[str]

    @property
    def has_usage(self) -> bool:
        return self.usage_in is not None or self.usage_cached is not None

    @property
    def prompt_tokens(self) -> Optional[int]:
        """Total prompt tokens across inclusive and additive provider shapes.

        Most OpenAI-shaped responses report total input in ``usage_in``, while
        WorkBuddy and several Antigravity/Gemini shapes report uncached input
        in ``usage_in`` and cache reads in ``usage_cached``. ``usage_cached >
        usage_in`` is the same discriminant used by ``cpa-admission.py`` and
        ``cpa_natural_traffic_audit.py``.
        """

        if self.usage_in is None and self.usage_cached is None:
            return None
        reported_in = self.usage_in or 0
        reported_cached = self.usage_cached or 0
        if reported_cached > reported_in:
            return reported_cached + reported_in
        return reported_in

    @property
    def uncached_tokens(self) -> Optional[int]:
        """Uncached prompt tokens for cache-ratio denominators."""

        if self.usage_in is None and self.usage_cached is None:
            return None
        reported_in = self.usage_in or 0
        reported_cached = self.usage_cached or 0
        if reported_cached > reported_in:
            return reported_in
        return max(0, reported_in - reported_cached)

    def cold(self, min_prefix_tokens: int = _MIN_PREFIX_TOKENS_DEFAULT) -> bool:
        """A turn that had a prefix but got no cache credit for it."""

        if self.usage_in is None or self.usage_cached is None:
            return False
        return self.usage_cached == 0 and self.usage_in >= min_prefix_tokens


@dataclass
class Finding:
    """One cold turn plus the strongest mechanism its neighbours prove."""

    turn: Turn
    previous: Optional[Turn]
    mechanism: str
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "at": self.turn.at,
            "lane": self.turn.lane,
            "model": self.turn.model,
            "conversation": self.turn.conversation,
            "usage_in": self.turn.usage_in,
            "mechanism": self.mechanism,
            "detail": self.detail,
        }


def parse_journal(lines: Iterable[str]) -> list[Turn]:
    """Extract cache-relevant turns from admission journal text."""

    turns = []
    for line in lines:
        if _UPSTREAM_RESULT not in line:
            continue
        stamp = _TIMESTAMP.match(line)
        fields = dict(_FIELD.findall(line))
        if "lane" not in fields:
            continue
        turns.append(
            Turn(
                at=stamp.group(1).replace("T", " ") if stamp else "",
                lane=fields.get("lane", ""),
                model=normalise_model(fields.get("model", "")),
                conversation=_absent(fields.get("client_request_hash")),
                instance=_absent(fields.get("instance_hash")),
                status=fields.get("status"),
                usage_in=_int_or_none(fields.get("usage_in")),
                usage_cached=_int_or_none(fields.get("usage_cached")),
                finish=_absent(fields.get("sse_text_finish")),
            )
        )
    return turns


def _parse_stamp(stamp: str) -> Optional[datetime]:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


def filter_hours(turns: Sequence[Turn], hours: Optional[float]) -> list[Turn]:
    """Restrict turns to the trailing ``hours`` window ending at newest row."""

    if hours is None:
        return list(turns)
    stamps = [parsed for turn in turns if (parsed := _parse_stamp(turn.at)) is not None]
    if not stamps:
        return list(turns)
    cutoff = max(stamps) - timedelta(hours=hours)
    kept: list[Turn] = []
    for turn in turns:
        parsed = _parse_stamp(turn.at)
        if parsed is None or parsed >= cutoff:
            kept.append(turn)
    return kept


def classify(turn: Turn, previous: Optional[Turn]) -> Tuple[str, str]:
    """Return (mechanism, detail) for a cold turn."""

    if previous is None or not previous.has_usage:
        return "unattributed", "no preceding turn with usage in this conversation"
    if turn.instance and previous.instance and turn.instance != previous.instance:
        return "rebind", "instance {0} -> {1}".format(previous.instance, turn.instance)
    if previous.model != turn.model:
        return "model_switch", "{0} -> {1}".format(previous.model, turn.model)
    if previous.lane != turn.lane:
        return "lane_switch", "{0} -> {1}".format(previous.lane, turn.lane)
    if previous.usage_cached and turn.usage_in is not None:
        return "cache_eviction", "{0} tokens re-prefilled".format(turn.usage_in)
    return "cache_eviction", "cached prefix dropped with no identifying change"


def attribute(
    turns: Sequence[Turn], min_prefix_tokens: int = _MIN_PREFIX_TOKENS_DEFAULT
) -> list[Finding]:
    """Group turns into conversations and attribute every cold restart."""

    # Input order is the journal's own order. Sorting by (at, lane, model) put
    # a single-timestamp fixture's turns in lane order and misread a model
    # switch as unattributed; it would do the same to any same-second burst.
    by_conversation = defaultdict(list)
    for turn in turns:
        if turn.conversation:
            by_conversation[turn.conversation].append(turn)

    findings = []
    for members in by_conversation.values():
        previous = None
        for turn in members:
            if turn.cold(min_prefix_tokens):
                mechanism, detail = classify(turn, previous)
                findings.append(Finding(turn, previous, mechanism, detail))
            if turn.has_usage:
                previous = turn
    findings.sort(key=lambda item: item.turn.at)
    return findings


def summarise(
    turns: Sequence[Turn],
    findings: Sequence[Finding],
    min_prefix_tokens: int = _MIN_PREFIX_TOKENS_DEFAULT,
) -> dict[str, object]:
    """Aggregate the census: population, hit ratio, and mechanism split."""

    conversations = defaultdict(list)
    for turn in turns:
        if turn.conversation:
            conversations[turn.conversation].append(turn)
    multi_turn = {k: v for k, v in conversations.items() if len(v) > 1}

    mechanism_counts: dict[str, int] = defaultdict(int)
    lost_tokens = 0
    for finding in findings:
        mechanism_counts[finding.mechanism] += 1
        lost_tokens += finding.turn.usage_in or 0

    cached = sum(t.usage_cached or 0 for t in turns if t.has_usage)
    uncached = sum(t.uncached_tokens or 0 for t in turns if t.has_usage)
    total = cached + uncached
    # Probe and title requests carry usage but no reusable prefix. They are
    # legitimate traffic, yet including them in the denominator is what made a
    # review read a healthy lane as a 12% hit ratio.
    cache_turns = [
        t
        for t in turns
        if t.prompt_tokens is not None and t.prompt_tokens >= min_prefix_tokens
    ]
    eligible_cached = sum(t.usage_cached or 0 for t in cache_turns)
    eligible_total = eligible_cached + sum(t.uncached_tokens or 0 for t in cache_turns)

    return {
        "turns": len(turns),
        "turns_with_usage": sum(1 for t in turns if t.has_usage),
        "cache_eligible_turns": len(cache_turns),
        "eligible_cache_hit_ratio": (
            round(eligible_cached / eligible_total, 4)
            if cache_turns and eligible_total
            else None
        ),
        "conversations": len(conversations),
        "multi_turn_conversations": len(multi_turn),
        "cold_conversations": len({f.turn.conversation for f in findings}),
        "restarts": len(findings),
        "restart_lost_tokens": lost_tokens,
        "cache_hit_ratio": round(cached / total, 4) if total else None,
        "mechanisms": dict(sorted(mechanism_counts.items())),
    }


def render(summary: Mapping[str, object], findings: Sequence[Finding]) -> str:
    """Human-readable census; never prints prompts or credentials."""

    lines = ["==cache-reset-probe=="]
    for key in (
        "turns",
        "turns_with_usage",
        "conversations",
        "multi_turn_conversations",
        "cold_conversations",
        "restarts",
        "restart_lost_tokens",
        "cache_hit_ratio",
        "cache_eligible_turns",
        "eligible_cache_hit_ratio",
    ):
        lines.append("{0}={1}".format(key, summary[key]))
    raw_mechanisms = summary.get("mechanisms")
    mechanisms: dict[str, int] = (
        raw_mechanisms if isinstance(raw_mechanisms, dict) else {}
    )
    if mechanisms:
        lines.append(
            "mechanisms="
            + ",".join("{0}:{1}".format(n, c) for n, c in mechanisms.items())
        )
    else:
        lines.append("mechanisms=none")

    lines.append("==restarts==")
    if not findings:
        lines.append("none")
    for finding in findings:
        lines.append(
            "restart at={0} lane={1} model={2} conversation={3} "
            "usage_in={4} mechanism={5} detail={6!r}".format(
                finding.turn.at,
                finding.turn.lane,
                finding.turn.model,
                finding.turn.conversation,
                finding.turn.usage_in,
                finding.mechanism,
                finding.detail,
            )
        )
    return "\n".join(lines)


def _read_source(args: argparse.Namespace) -> list[str]:
    if not args.file:
        raise ValueError(
            "pass --file or --ssh-profile; save the journal first "
            '(journalctl -u cpa-admission --since "48 hours ago" --no-pager -o cat > adm48.log)'
        )
    if args.file == "-":
        return sys.stdin.read().splitlines()
    return Path(args.file).read_text(encoding="utf-8", errors="replace").splitlines()


def _run_remote_probe(
    profile: str,
    hours: float,
    min_prefix_tokens: int,
    as_json: bool,
) -> tuple[int, str, str]:
    try:
        from scripts import launcher_transport
        from vps_ssh_launcher import config as target_config
    except ImportError:
        if __package__:
            raise
        from bootstrap import ensure_repo_imports  # type: ignore[import-not-found]

        ensure_repo_imports()
        from scripts import launcher_transport  # type: ignore[import-not-found,no-redef]
        from vps_ssh_launcher import (  # type: ignore[import-not-found,no-redef]
            config as target_config,
        )

    repo_root = Path(__file__).resolve().parents[1]
    config_path = target_config.resolve_default_config_path(repo_root)
    payload = launcher_transport.encode_source_payload(Path(__file__).read_bytes())
    json_flag = " --json" if as_json else ""
    action = (
        f"journalctl -u cpa-admission --since '{hours:g} hours ago' "
        f'--no-pager -o cat | python3 "$tmp" --file - '
        f"--min-prefix-tokens {int(min_prefix_tokens)}{json_flag}"
    )
    command = launcher_transport.remote_temp_python_command(
        payload=payload,
        temp_prefix="cpa-cache-reset-probe",
        action=action,
    )
    argv = launcher_transport.launcher_argv(
        repo_root=repo_root,
        command=command,
        profile=profile,
        config=config_path,
        idle_timeout_seconds=60,
        hard_timeout_seconds=180,
    )
    completed = subprocess.run(  # nosec B603
        [sys.executable, *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attribute cold cache restarts in the CPA admission journal."
    )
    parser.add_argument(
        "--file",
        default=None,
        help="saved admission journal text (or '-' for stdin); dump with journalctl -o cat",
    )
    parser.add_argument(
        "--ssh-profile",
        default=None,
        help="read the live admission journal on the named ssh_tool.py profile",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help="trailing journal window in hours (defaults to 48 with --ssh-profile)",
    )
    parser.add_argument(
        "--min-prefix-tokens",
        type=int,
        default=_MIN_PREFIX_TOKENS_DEFAULT,
        help="input size below which a turn is not a real prefix",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.hours is not None and args.hours <= 0:
        parser.error("--hours must be positive")
    if args.file and args.ssh_profile:
        parser.error("pass either --file or --ssh-profile, not both")
    if not args.file and not args.ssh_profile:
        parser.error("one of --file or --ssh-profile is required")

    if args.ssh_profile:
        window_hours = args.hours if args.hours is not None else _DEFAULT_REMOTE_HOURS
        try:
            returncode, stdout, stderr = _run_remote_probe(
                args.ssh_profile,
                window_hours,
                args.min_prefix_tokens,
                args.json,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print("input-error: {0}".format(error), file=sys.stderr)
            return 2
        if returncode != 0:
            sys.stderr.write(stderr or stdout)
            return returncode or 2
        sys.stdout.write(stdout)
        return 0

    try:
        lines = _read_source(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("input-error: {0}".format(error), file=sys.stderr)
        return 2

    turns = filter_hours(parse_journal(lines), args.hours)
    findings = attribute(turns, args.min_prefix_tokens)
    summary = summarise(turns, findings, args.min_prefix_tokens)
    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary,
                    "restarts": [finding.as_dict() for finding in findings],
                },
                indent=2,
            )
        )
    else:
        print(render(summary, findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

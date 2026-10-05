#!/usr/bin/env python3
"""Read-only admission risk-posture and contract audit for the BWG CPA gateway.

This answers a question none of the other CPA tools answer: is the shared-account
admission gate *configured* in a way that is safe and self-consistent with respect
to provider ban / throttle / degradation risk, and is anything it still advertises
demonstrably failing?

Two halves, deliberately kept apart:

* **Contract** -- read from repository artefacts only (``cpa-admission.json`` and
  ``cpa_provider_routes.json``), so it runs offline and is fully deterministic.
  It pins the invariants that are easy to break silently: the lane cooldown
  ladder, the upstream capacity signals each lane recognises, lane/model
  membership, the route/exclusion set algebra, and that every route naming a
  shared-account lane is fully gated by that lane.
* **Posture** -- joined against the authoritative local client view
  (``codex_local_access_logs.sqlite``, opened read-only). A route that keeps
  answering 5xx while still being advertised to clients is a defect the manifest
  alone cannot show, because the manifest has no idea the upstream refuses.

Division of labour with the neighbouring tools (do not duplicate them):

* ``cpa_failure_triage.py`` -- per-request attribution of *this machine's*
  failures (local gate vs admission vs upstream vs dead route).
* ``cpa-health.py`` / ``cpa_policy.py`` (remote) -- live generation probes and
  the deployed config's semantics.
* ``cockpit_provider_health.py`` -- the local Cockpit sidecar/provider wiring.
* this script -- the admission *contract* plus the advertised-vs-observed join.

Exit codes: 0 clean, 1 findings at or above the failure bar, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any, Sequence

DEFAULT_ADMISSION_CONFIG = (
    pathlib.Path(__file__).parent / "remote" / "cpa-admission.json"
)
DEFAULT_ROUTES = pathlib.Path(__file__).parent / "remote" / "cpa_provider_routes.json"
DEFAULT_DB = (
    pathlib.Path.home() / ".antigravity_cockpit" / "codex_local_access_logs.sqlite"
)

# A lane whose cooldown ladder ends below retry_after_max_seconds can still be
# pinned for the full server-advertised window (see the `cooldown-server-cap`
# finding). That is deliberate -- honouring the upstream's own Retry-After is the
# ban-safe choice -- but it must never be a surprise, so the audit reports the
# exposure explicitly instead of leaving it to whoever reads the code.
REQUIRED_CAPACITY_STATUSES = (429, 503)
REQUIRED_CAPACITY_MARKERS = ("server_is_overloaded", "usage_limit_reached")

# The upstream signals that matter are parsed out of the first `probe_bytes` of
# the response body. Anything past that window is invisible to the breaker, so a
# stream that degrades late never opens the lane cooldown and the client keeps
# retrying into an overloaded account. Below this floor the blind spot is wider
# than the reviewed baseline.
PROBE_BYTES_MIN = 262144

# A still-advertised model must both fail often enough to be a pattern and fail
# often enough *relative to its own traffic* to be a route defect rather than
# upstream weather. The OAuth lane answers 503 whenever the single subscription
# account is busy, which is expected behaviour and must not be reported as a
# broken route; a route that fails most of the time is a different animal.
ADVERTISED_FAILURE_MIN = 5
ADVERTISED_FAILURE_RATE_FAIL = 0.5
ADVERTISED_FAILURE_RATE_WARN = 0.2

SEVERITY_FAIL = "fail"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"
_SEVERITY_RANK = {SEVERITY_INFO: 0, SEVERITY_WARN: 1, SEVERITY_FAIL: 2}

# Bucket for rows whose requested model the client log could not resolve. It is
# excluded from the per-model posture findings because it is not a model name.
UNPARSED_MODEL = "other"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def advertised_aliases(routes: dict[str, Any]) -> dict[str, str]:
    """Map every alias a client can name to the provider slot that serves it.

    A name advertised in more than one slot is a routing ambiguity: the client
    cannot tell which upstream it will land on, so the first slot wins in the
    output while the collision is reported separately.
    """

    owners: dict[str, str] = {}
    for provider in routes.get("providers", []):
        label = f"slot{provider.get('slot')}:{provider.get('name')}"
        for model in provider.get("models", []):
            alias = model.get("alias")
            if isinstance(alias, str) and alias:
                owners.setdefault(alias, label)
    for route in routes.get("oauth_routes", []):
        label = f"oauth:{route.get('name')}"
        for model in route.get("models", []):
            alias = model.get("alias")
            if isinstance(alias, str) and alias:
                owners.setdefault(alias, label)
    return owners


def _duplicate_aliases(routes: dict[str, Any]) -> list[tuple[str, list[str]]]:
    seen: dict[str, list[str]] = {}
    for provider in routes.get("providers", []):
        label = f"slot{provider.get('slot')}:{provider.get('name')}"
        for model in provider.get("models", []):
            alias = model.get("alias")
            if isinstance(alias, str) and alias:
                seen.setdefault(alias, []).append(label)
    for route in routes.get("oauth_routes", []):
        label = f"oauth:{route.get('name')}"
        for model in route.get("models", []):
            alias = model.get("alias")
            if isinstance(alias, str) and alias:
                seen.setdefault(alias, []).append(label)
    return [(alias, owners) for alias, owners in seen.items() if len(owners) > 1]


def _declared_lane_coverage(
    routes: dict[str, Any],
) -> list[tuple[str, str, list[str]]]:
    """Every route that declares ``admission_lane``, with its advertised aliases.

    A route pointing at a *shared* account (one subscription, one API key) is only
    protected if every name it advertises is gated by the lane it names. The
    manifest declares the intent; the admission config implements it. Nothing
    else joins the two, so a model added to the route without being added to the
    lane would reach the shared account with no concurrency bound and no
    capacity breaker -- the exact shape of a self-inflicted ban.
    """

    entries: list[tuple[str, str, list[str]]] = []
    for provider in routes.get("providers", []):
        lane = provider.get("admission_lane")
        if not isinstance(lane, str) or not lane:
            continue
        aliases = [
            model["alias"]
            for model in provider.get("models", [])
            if isinstance(model.get("alias"), str) and model["alias"]
        ]
        entries.append(
            (f"slot{provider.get('slot')}:{provider.get('name')}", lane, aliases)
        )
    for route in routes.get("oauth_routes", []):
        lane = route.get("admission_lane")
        if not isinstance(lane, str) or not lane:
            continue
        aliases = [
            model["alias"]
            for model in route.get("models", [])
            if isinstance(model.get("alias"), str) and model["alias"]
        ]
        entries.append((f"oauth:{route.get('name')}", lane, aliases))
    return entries


def _has_success_column(cursor: sqlite3.Cursor) -> bool:
    """Whether the client log carries the app's own ``success`` verdict.

    ``success`` is the authority for "did this request fail", and the
    distinction matters: 37k historical rows carry **no** ``http_status`` while
    being successful, so treating a missing status as a failure inflates the
    error rate on any window that reaches back far enough. The status band is
    only a fallback for a schema that predates the column.
    """

    cursor.execute("pragma table_info(request_logs)")
    return "success" in {str(row[1]) for row in cursor.fetchall()}


def observed_by_model(
    db_path: pathlib.Path, hours: float
) -> dict[str, tuple[int, int]]:
    """Map requested model -> (failed requests, total requests) in the window."""

    if not db_path.exists():
        return {}
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = connection.cursor()
        if _has_success_column(cursor):
            query = (
                "select requested_model, count(*), "
                "sum(case when success = 0 then 1 else 0 end) "
                "from request_logs where timestamp >= ? group by requested_model"
            )
        else:
            query = (
                "select requested_model, count(*), "
                "sum(case when http_status is null or http_status >= 400 then 1 else 0 end) "
                "from request_logs where timestamp >= ? group by requested_model"
            )
        cursor.execute("select max(timestamp) from request_logs")
        newest = cursor.fetchone()[0]
        if newest is None:
            return {}
        since_ms = int(newest) - int(hours * 3600 * 1000)
        cursor.execute(query, (since_ms,))
        return {
            str(model or UNPARSED_MODEL): (int(failed_count), int(total))
            for model, total, failed_count in cursor.fetchall()
        }
    finally:
        connection.close()


def observed_totals(db_path: pathlib.Path, hours: float) -> tuple[int, int]:
    if not db_path.exists():
        return (0, 0)
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = connection.cursor()
        if _has_success_column(cursor):
            query = (
                "select count(*) from request_logs where timestamp >= ? and success = 0"
            )
        else:
            query = (
                "select count(*) from request_logs where timestamp >= ? "
                "and (http_status is null or http_status >= 400)"
            )
        cursor.execute("select max(timestamp) from request_logs")
        newest = cursor.fetchone()[0]
        if newest is None:
            return (0, 0)
        since_ms = int(newest) - int(hours * 3600 * 1000)
        cursor.execute(
            "select count(*) from request_logs where timestamp >= ?", (since_ms,)
        )
        total = int(cursor.fetchone()[0])
        cursor.execute(query, (since_ms,))
        errors = int(cursor.fetchone()[0])
        return (total, errors)
    finally:
        connection.close()


def observed_late_stream_failures(db_path: pathlib.Path, hours: float) -> int:
    """Requests that answered 2xx and still failed (the stream reported an error).

    This is the sample that decides whether the admission proxy's bounded body
    probe is wide enough: a capacity marker arriving after the probe window is
    invisible to the breaker, and the only evidence of that happening is a
    successful HTTP status paired with a failed request.
    """

    if not db_path.exists():
        return 0
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = connection.cursor()
        if not _has_success_column(cursor):
            return 0
        cursor.execute("select max(timestamp) from request_logs")
        newest = cursor.fetchone()[0]
        if newest is None:
            return 0
        since_ms = int(newest) - int(hours * 3600 * 1000)
        cursor.execute(
            "select count(*) from request_logs where timestamp >= ? and success = 0 "
            "and http_status between 200 and 299",
            (since_ms,),
        )
        return int(cursor.fetchone()[0])
    finally:
        connection.close()


def observed_last_failure_age_minutes(
    db_path: pathlib.Path, hours: float
) -> dict[str, int]:
    """Map requested model -> minutes since its most recent failure.

    A window can span a configuration change, so a high failure count alone does
    not say whether the route is still failing. Measured case: a name reported at
    "95% failed over 720h" had its last failure days earlier and was serving
    normally; another reported at 71% over 24h stopped failing hours before the
    audit ran. The age turns "is this live?" into a number instead of a guess.
    """

    if not db_path.exists():
        return {}
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = connection.cursor()
        if _has_success_column(cursor):
            query = (
                "select requested_model, max(timestamp) from request_logs "
                "where timestamp >= ? and success = 0 group by requested_model"
            )
        else:
            query = (
                "select requested_model, max(timestamp) from request_logs "
                "where timestamp >= ? and (http_status is null or http_status >= 400) "
                "group by requested_model"
            )
        cursor.execute("select max(timestamp) from request_logs")
        newest = cursor.fetchone()[0]
        if newest is None:
            return {}
        newest = int(newest)
        since_ms = newest - int(hours * 3600 * 1000)
        cursor.execute(query, (since_ms,))
        return {
            str(model or UNPARSED_MODEL): max(0, round((newest - int(last)) / 60000))
            for model, last in cursor.fetchall()
            if last is not None
        }
    finally:
        connection.close()


def observed_live_failure_by_model(
    db_path: pathlib.Path, hours: float
) -> dict[str, bool]:
    """Map requested model -> whether its *newest* row in the window failed.

    A window can span a configuration change, so a failure count alone cannot say
    whether a route is still failing. Measured case (2026-10-04): a name reported
    at 71% over 24h had its last failure 656 minutes before the newest row, and
    its newest request -- 79 minutes before that row -- succeeded. The route was
    healthy; only the window still remembered the outage, yet the audit printed a
    FAIL and exited 1. So a failure verdict now requires the model's most recent
    request to have actually failed; a route that has served since is reported as
    stale instead.
    """

    if not db_path.exists():
        return {}
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = connection.cursor()
        if _has_success_column(cursor):
            failed_expr = "success = 0"
        else:
            failed_expr = "(http_status is null or http_status >= 400)"
        cursor.execute("select max(timestamp) from request_logs")
        newest = cursor.fetchone()[0]
        if newest is None:
            return {}
        since_ms = int(newest) - int(hours * 3600 * 1000)
        # failed_expr is one of the two constant literals chosen just above;
        # every caller-supplied value is bound as a ? parameter.
        cursor.execute(
            "select l.requested_model, " + failed_expr + " from request_logs l "  # nosec B608
            "where l.timestamp >= ? and l.timestamp = ("
            "  select max(r.timestamp) from request_logs r "
            "  where r.timestamp >= ? and r.requested_model is l.requested_model)",
            (since_ms, since_ms),
        )
        newest_failed: dict[str, bool] = {}
        for model, failed in cursor.fetchall():
            key = str(model or UNPARSED_MODEL)
            # Rows can share the newest timestamp; the model is only "live
            # failing" when none of them succeeded.
            newest_failed[key] = newest_failed.get(key, True) and bool(failed)
        return newest_failed
    finally:
        connection.close()


def audit_contract(
    admission: dict[str, Any],
    routes: dict[str, Any],
) -> list[Finding]:
    findings: list[Finding] = []
    lanes = admission.get("lanes") or []
    if not lanes:
        findings.append(
            Finding(
                "admission-no-lanes",
                SEVERITY_FAIL,
                "admission config declares no lanes",
            )
        )
    server_cap = admission.get("retry_after_max_seconds")
    probe_bytes = admission.get("probe_bytes")

    owners = advertised_aliases(routes)
    duplicates = _duplicate_aliases(routes)
    for alias, holders in sorted(duplicates):
        findings.append(
            Finding(
                "alias-multi-slot",
                SEVERITY_FAIL,
                f"alias {alias!r} is advertised by more than one route "
                f"({', '.join(holders)}); clients cannot predict the upstream",
            )
        )

    gpt_routes = {
        model["alias"]
        for provider in routes.get("providers", [])
        for model in provider.get("models", [])
        if isinstance(model.get("name"), str)
        and model["name"].startswith("gpt-")
        and isinstance(model.get("alias"), str)
    }
    oauth_aliases = {
        model["alias"]
        for route in routes.get("oauth_routes", [])
        for model in route.get("models", [])
        if isinstance(model.get("alias"), str)
    }
    oauth_exclusions = set(routes.get("oauth_exclusions") or [])
    key_exclusions = set(routes.get("codex_api_key_exclusions") or [])

    for alias in sorted(gpt_routes - oauth_exclusions):
        findings.append(
            Finding(
                "oauth-exclusion-violation",
                SEVERITY_FAIL,
                f"gpt route alias {alias!r} is missing from oauth_exclusions; "
                "it could be served by the subscription OAuth account",
            )
        )
    for alias in sorted((gpt_routes | oauth_aliases) - key_exclusions):
        findings.append(
            Finding(
                "codex-key-exclusion-violation",
                SEVERITY_FAIL,
                f"alias {alias!r} is missing from codex_api_key_exclusions; "
                "it could be served by the shared API-key account",
            )
        )

    lane_membership: dict[str, str] = {}
    lane_models_by_name: dict[str, set[str]] = {}
    server_capped_lanes: list[str] = []
    for lane in lanes:
        name = str(lane.get("name"))
        lane_models_by_name[name] = {str(model) for model in lane.get("models") or []}
        schedule = list(lane.get("cooldown_schedule_seconds") or [])
        ladder_cap = lane.get("cooldown_cap_seconds")
        if not schedule:
            findings.append(
                Finding(
                    "lane-cooldown-ladder-missing",
                    SEVERITY_FAIL,
                    f"lane {name} has no cooldown_schedule_seconds",
                )
            )
        elif ladder_cap is not None and schedule[-1] != ladder_cap:
            findings.append(
                Finding(
                    "lane-cooldown-ladder-mismatch",
                    SEVERITY_FAIL,
                    f"lane {name} cooldown ladder ends at {schedule[-1]}s but "
                    f"cooldown_cap_seconds is {ladder_cap}s",
                )
            )
        if (
            isinstance(server_cap, int)
            and isinstance(ladder_cap, int)
            and server_cap > ladder_cap
        ):
            server_capped_lanes.append(name)

        statuses = set(lane.get("capacity_statuses") or [])
        for status in REQUIRED_CAPACITY_STATUSES:
            if status not in statuses:
                findings.append(
                    Finding(
                        "lane-capacity-status-missing",
                        SEVERITY_FAIL,
                        f"lane {name} does not treat HTTP {status} as a capacity signal",
                    )
                )
        markers = {str(marker).lower() for marker in lane.get("capacity_markers") or []}
        for marker in REQUIRED_CAPACITY_MARKERS:
            if marker not in markers:
                findings.append(
                    Finding(
                        "lane-capacity-marker-missing",
                        SEVERITY_FAIL,
                        f"lane {name} does not recognise the upstream marker "
                        f"{marker!r}; a 200-with-overload body would go uncounted",
                    )
                )

        for model in lane.get("models") or []:
            model_name = str(model)
            previous = lane_membership.get(model_name)
            if previous is not None:
                findings.append(
                    Finding(
                        "lane-model-in-multiple-lanes",
                        SEVERITY_FAIL,
                        f"model {model_name!r} is a member of lanes {previous!r} and "
                        f"{name!r}; a failure in one would open the other",
                    )
                )
            lane_membership[model_name] = name
            if model_name not in owners:
                findings.append(
                    Finding(
                        "lane-model-not-advertised",
                        SEVERITY_FAIL,
                        f"lane {name} gates model {model_name!r}, which no route "
                        "advertises; a stale lane member opens the whole lane's "
                        "cooldown on a failure no client can even trigger",
                    )
                )

    for label, lane_name, aliases in _declared_lane_coverage(routes):
        if lane_name not in lane_models_by_name:
            findings.append(
                Finding(
                    "lane-route-coverage-missing",
                    SEVERITY_FAIL,
                    f"route {label} declares admission_lane {lane_name!r}, which no lane "
                    "defines; every model on that route would reach the shared account "
                    "ungated",
                )
            )
            continue
        for alias in sorted(set(aliases) - lane_models_by_name[lane_name]):
            findings.append(
                Finding(
                    "lane-route-coverage-missing",
                    SEVERITY_FAIL,
                    f"route {label} declares admission_lane {lane_name!r} but advertises "
                    f"{alias!r}, which that lane does not gate; the alias would reach the "
                    "shared account with no concurrency bound and no capacity breaker",
                )
            )

    if server_capped_lanes:
        findings.append(
            Finding(
                "lane-cooldown-server-cap",
                SEVERITY_WARN,
                f"lanes {', '.join(sorted(server_capped_lanes))}: an upstream "
                f"Retry-After can pin the lane for up to {server_cap}s even though the "
                "failure ladder caps far lower; honouring it is the ban-safe choice, "
                "but expect a multi-hour outage and read /healthz before blaming the "
                "client",
            )
        )
    if isinstance(probe_bytes, int) and probe_bytes < PROBE_BYTES_MIN:
        findings.append(
            Finding(
                "capacity-probe-window",
                SEVERITY_WARN,
                f"probe_bytes={probe_bytes} is below the reviewed baseline "
                f"{PROBE_BYTES_MIN}; capacity markers arriving later in a stream are "
                "invisible to the breaker, so late degradation never opens the lane",
            )
        )
    return findings


def audit_posture(
    routes: dict[str, Any],
    db_path: pathlib.Path,
    hours: float,
) -> list[Finding]:
    findings: list[Finding] = []
    owners = advertised_aliases(routes)
    total, errors = observed_totals(db_path, hours)
    if total == 0:
        findings.append(
            Finding(
                "posture-no-traffic",
                SEVERITY_INFO,
                f"no client traffic in the last {hours:g}h; the advertised-route join "
                "is vacuous, so a clean result here proves nothing about routing",
            )
        )
        return findings
    findings.append(
        Finding(
            "posture-summary",
            SEVERITY_INFO,
            f"{errors}/{total} requests failed in the last {hours:g}h "
            f"({(100.0 * errors / total):.1f}%)",
        )
    )
    late = observed_late_stream_failures(db_path, hours)
    findings.append(
        Finding(
            "late-stream-failures",
            SEVERITY_INFO,
            f"{late} request(s) answered 2xx and still failed in the last {hours:g}h; "
            "this is the sample that decides whether the admission body-probe window "
            "is wide enough — zero means there is no evidence to widen it, and any "
            "nonzero count must be traced before touching probe_bytes",
        )
    )
    flagged = False
    last_failure_age = observed_last_failure_age_minutes(db_path, hours)
    live_failure = observed_live_failure_by_model(db_path, hours)
    for model, (failed, seen) in observed_by_model(db_path, hours).items():
        # `other` is the bucket for requests whose model the log could not parse
        # (image calls, helper requests, non-chat endpoints). It is not a name a
        # route could advertise, so reporting it as an unresolvable model would
        # bury the real findings under a permanent warning.
        if model == UNPARSED_MODEL or failed < ADVERTISED_FAILURE_MIN or seen == 0:
            continue
        rate = failed / seen
        if rate < ADVERTISED_FAILURE_RATE_WARN:
            severity, code = SEVERITY_INFO, "advertised-degraded-route"
        elif rate < ADVERTISED_FAILURE_RATE_FAIL:
            severity, code = SEVERITY_WARN, "advertised-degraded-route"
        elif live_failure.get(model, True):
            severity, code = SEVERITY_FAIL, "advertised-failing-route"
        else:
            # The failures are real but historical: the model's newest request in
            # the window succeeded. Reporting this as FAIL made the standard 24h
            # verification command exit 1 in the steady state, which is exactly
            # how a gate stops being read.
            severity, code = SEVERITY_WARN, "advertised-failing-route-stale"
        owner = owners.get(model)
        if owner is None:
            findings.append(
                Finding(
                    "failing-unadvertised-model",
                    SEVERITY_WARN,
                    f"{model!r} produced {failed}/{seen} failed requests but no route "
                    "advertises it; the client is naming a model the gateway "
                    "cannot resolve",
                )
            )
            continue
        flagged = True
        age = last_failure_age.get(model)
        recency = (
            f", last failure {age} min before the newest row" if age is not None else ""
        )
        staleness = (
            "; its newest request in the window succeeded, so this is a "
            "historical outage rather than a live one"
            if code == "advertised-failing-route-stale"
            else ""
        )
        findings.append(
            Finding(
                code,
                severity,
                f"{model!r} failed {failed}/{seen} requests ({rate:.0%}) in the last "
                f"{hours:g}h{recency}{staleness}; the route manifest advertises this "
                f"name via {owner}",
            )
        )
    if flagged:
        # The manifest maps a *name* to a slot; it does not say which upstream
        # actually served the failure. A client-side alias layer can rewrite the
        # name before it reaches the gateway -- measured case: a client asked for
        # one alias and the gateway journal recorded a different model name for
        # the exact same requests. Changing routes off this finding alone would
        # therefore aim at the wrong slot.
        findings.append(
            Finding(
                "attribution-boundary",
                SEVERITY_INFO,
                "the name->slot mapping above is a manifest projection, not proof of "
                "which upstream served the failure; confirm the served model in the "
                "gateway journal (upstream_result ... model=...) before changing routes",
            )
        )
    return findings


def render(findings: Sequence[Finding], hours: float, strict: bool) -> list[str]:
    lines = [f"== CPA admission risk audit (posture window {hours:g}h) =="]
    order = sorted(
        findings,
        key=lambda item: (-_SEVERITY_RANK[item.severity], item.code),
    )
    if not order:
        lines.append("  no findings")
    for finding in order:
        lines.append(
            f"  [{finding.severity.upper():4}] {finding.code}: {finding.message}"
        )
    fails = [item for item in findings if item.severity == SEVERITY_FAIL]
    warns = [item for item in findings if item.severity == SEVERITY_WARN]
    lines.append(f"-- fail={len(fails)} warn={len(warns)} --")
    if fails:
        lines.append(
            "  verdict: FAIL - fix the failing contract items before trusting "
            "(a failure here is live: the model's newest request in the window also "
            "failed; a route that has served since is reported as stale instead)"
        )
    elif warns and strict:
        lines.append("  verdict: FAIL (strict) - warnings are fatal in strict mode")
    elif warns:
        lines.append("  verdict: PASS with warnings - warnings are accepted risk")
    else:
        lines.append("  verdict: PASS")
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--admission-config", type=pathlib.Path, default=DEFAULT_ADMISSION_CONFIG
    )
    parser.add_argument("--routes", type=pathlib.Path, default=DEFAULT_ROUTES)
    parser.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings as failures"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable output"
    )
    parser.add_argument(
        "--skip-posture",
        action="store_true",
        help="contract checks only; do not read the local client database",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        admission = load_json(args.admission_config)
        routes = load_json(args.routes)
    except (OSError, ValueError) as exc:
        print(f"cannot read audit inputs: {exc}", file=sys.stderr)
        return 2

    findings = audit_contract(admission, routes)
    if not args.skip_posture:
        # The client database is read while the sidecar may be writing it, so
        # a locked or corrupt file is a live possibility, not a hypothetical.
        # Contract findings must survive it, and the skipped posture half has
        # to stay visible instead of reading as a clean "no traffic".
        try:
            findings += audit_posture(routes, args.db, args.hours)
        except sqlite3.Error as exc:
            findings.append(
                Finding(
                    "posture-unavailable",
                    SEVERITY_WARN,
                    f"the client database could not be read ({type(exc).__name__}); "
                    "the advertised-vs-observed join was skipped and this run "
                    "proves nothing about live routing",
                )
            )

    if args.json:
        print(
            json.dumps(
                {
                    "hours": args.hours,
                    "findings": [finding.to_json() for finding in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for line in render(findings, args.hours, args.strict):
            print(line)

    fails = any(finding.severity == SEVERITY_FAIL for finding in findings)
    warns = any(finding.severity == SEVERITY_WARN for finding in findings)
    if fails or (args.strict and warns):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import time
from dataclasses import dataclass
from typing import Any, Sequence

DEFAULT_ADMISSION_CONFIG = (
    pathlib.Path(__file__).parent / "remote" / "cpa-admission.json"
)
DEFAULT_ROUTES = pathlib.Path(__file__).parent / "remote" / "cpa_provider_routes.json"
DEFAULT_DB = (
    pathlib.Path.home() / ".antigravity_cockpit" / "codex_local_access_logs.sqlite"
)
DEFAULT_CLIENT_CATALOG = (
    pathlib.Path.home() / ".codex" / ".cockpit-experimental-model-catalog-config.json"
)

# A lane whose cooldown ladder ends below retry_after_max_seconds can still be
# pinned for the full server-advertised window (see the `*-server-cap` findings).
# That is deliberate -- honouring the upstream's own Retry-After is the ban-safe
# choice -- but it must never be a surprise. A model-scoped upstream signal can
# enter the model cooldown map through `release()` even when the lane ladder is
# shorter, so lane-only accounting would understate the outage.
REQUIRED_CAPACITY_STATUSES = (429, 503)
REQUIRED_CAPACITY_MARKERS = ("server_is_overloaded", "usage_limit_reached")
# Mirrors STRICT_OAUTH_LANES in cpa-admission.py. These lanes honour a positive
# provider Retry-After from the first refusal without applying the global cap;
# Antigravity may therefore remain shut beyond retry_after_max_seconds.
STRICT_COOLDOWN_LANES = frozenset(
    {"antigravity-oauth", "claude-oauth", "workbuddy-oauth", "workbuddy-intl-oauth"}
)
# The ChatGPT OAuth lane serves several models from one subscription account and
# the upstream names the model it cannot serve (`Selected model is at capacity`)
# while still serving its siblings -- measured 2026-10-10, when that marker kept
# lane-wide refused the next `gpt-6.1-sol` request with `retry_after=471`. Which
# markers are model-scoped is therefore a reviewed contract, not a config value
# someone may quietly move back to the lane-wide set.
REQUIRED_MODEL_CAPACITY_MARKERS: dict[str, tuple[str, ...]] = {
    "chatgpt-oauth": ("selected model is at capacity", "model_at_capacity"),
}

# Non-stream bodies use a bounded prefix; SSE streams inspect every event with
# the same per-event bound. Oversized events cannot establish successful recovery.
PROBE_BYTES_MIN = 262144

# A still-advertised model must both fail often enough to be a pattern and fail
# often enough *relative to its own traffic* to be a route defect rather than
# upstream weather. The OAuth lane answers 503 whenever the single subscription
# account is busy, which is expected behaviour and must not be reported as a
# broken route; a route that fails most of the time is a different animal.
ADVERTISED_FAILURE_MIN = 5
ADVERTISED_FAILURE_RATE_FAIL = 0.5
ADVERTISED_FAILURE_RATE_WARN = 0.2

# The posture window is anchored at the newest request_logs row, not wall-clock
# time. When the client log itself has stopped updating (measured 2026-10-07:
# the desktop switched to direct mode on 10/5 01:04 and the local gate database
# went silent, yet the audit kept reporting two-day-old failures as "live" and
# exited 1), the live-vs-historical classification is unfalsifiable. Past this
# age the failing-route verdict is downgraded to a warning instead. Future
# timestamps (clock skew, fixture data) count as fresh so they never downgrade.
POSTURE_DATA_STALE_HOURS = 24.0

SEVERITY_FAIL = "fail"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"
_SEVERITY_RANK = {SEVERITY_INFO: 0, SEVERITY_WARN: 1, SEVERITY_FAIL: 2}
CLIENT_CATALOG_SCHEMA_VERSION = 4

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


def audit_live_health(snapshot: Any) -> list[Finding]:
    """Check admission's authoritative live state without pinging an account.

    The caller may capture localhost /healthz while the server is running.
    This function is pure so the same checked bytes can be archived as a
    redacted receipt. It never infers provider eligibility, no OAuth traffic is
    generated, and every lane must be present and non-cooling for a clean run.
    """

    if not isinstance(snapshot, dict) or snapshot.get("status") != "ok":
        return [
            Finding(
                "live-health-invalid",
                SEVERITY_FAIL,
                "the admission health snapshot is missing or does not report status=ok",
            )
        ]
    required = {
        "chatgpt-oauth",
        "zhipu-coding-plan",
        "zcode-start-plan",
        "antigravity-oauth",
        "workbuddy-oauth",
        "workbuddy-intl-oauth",
    }
    lanes = snapshot.get("lanes")
    if not isinstance(lanes, dict) or set(lanes) != required:
        found = sorted(lanes) if isinstance(lanes, dict) else []
        return [
            Finding(
                "live-health-lane-set",
                SEVERITY_FAIL,
                f"admission lanes are {found!r}; expected the five reviewed lanes",
            )
        ]
    findings: list[Finding] = []
    expected_rpm = {
        "chatgpt-oauth": 24,
        "zhipu-coding-plan": 30,
        "zcode-start-plan": 20,
        "antigravity-oauth": 12,
        "workbuddy-oauth": 12,
        "workbuddy-intl-oauth": 12,
    }
    for name in sorted(required):
        lane = lanes[name]
        state = lane.get("state") if isinstance(lane, dict) else None
        if not isinstance(state, dict):
            findings.append(
                Finding(
                    "live-health-invalid",
                    SEVERITY_FAIL,
                    f"{name} has no readable admission state",
                )
            )
            continue
        if lane.get("requests_per_minute") != expected_rpm[name]:
            findings.append(
                Finding(
                    "live-rate-guard-drift",
                    SEVERITY_WARN,
                    f"{name} RPM is {lane.get('requests_per_minute')!r}; "
                    f"reviewed routing uses {expected_rpm[name]}",
                )
            )
        if state.get("hold_reason"):
            findings.append(
                Finding(
                    "live-lane-held",
                    SEVERITY_FAIL,
                    f"{name} is held for operator review ({state['hold_reason']!r})",
                )
            )
        if state.get("cooldown_active") or state.get("cooldown_remaining"):
            findings.append(
                Finding(
                    "live-lane-cooling",
                    SEVERITY_WARN,
                    f"{name} has an active cooldown "
                    f"({state.get('cooldown_remaining')!r}s); this is a wait, not a ban finding",
                )
            )
        model_cooldowns = state.get("model_cooldowns")
        if isinstance(model_cooldowns, dict):
            cooling_models = sorted(
                str(model)
                for model, remaining in model_cooldowns.items()
                if type(remaining) in (int, float) and remaining > 0
            )
            if cooling_models:
                findings.append(
                    Finding(
                        "live-model-cooling",
                        SEVERITY_WARN,
                        f"{name} has model-scoped cooldowns for "
                        f"{', '.join(cooling_models)}; healthy siblings may "
                        "still run, and this is a wait rather than a ban finding",
                    )
                )
        for field in ("inflight", "pending", "rpm_window_used"):
            value = state.get(field)
            if type(value) is not int or value < 0:
                findings.append(
                    Finding(
                        "live-health-invalid",
                        SEVERITY_FAIL,
                        f"{name}.{field} is not a non-negative integer",
                    )
                )
    return findings


def advertised_aliases(routes: dict[str, Any]) -> dict[str, str]:
    """Map every alias a client can name to the provider slot that serves it.

    A name advertised in more than one slot is a routing ambiguity: the client
    cannot tell which upstream it will land on, so the first slot wins in the
    output while the collision is reported separately.
    """

    owners: dict[str, str] = {}
    for provider in routes.get("providers", []):
        if not isinstance(provider, dict):
            continue
        label = f"slot{provider.get('slot')}:{provider.get('name')}"
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias")
            if isinstance(alias, str) and alias:
                owners.setdefault(alias, label)
    for route in routes.get("codex_api_key_routes") or []:
        if not isinstance(route, dict):
            continue
        label = f"codex-key:{route.get('name', route.get('base-url'))}"
        for model in route.get("models") or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias") or model.get("name")
            if isinstance(alias, str) and alias:
                owners.setdefault(alias, label)
    for route in routes.get("claude_api_key_routes") or []:
        if not isinstance(route, dict):
            continue
        label = f"claude-key:{route.get('name', route.get('base-url'))}"
        for model in route.get("models") or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias") or model.get("name")
            if isinstance(alias, str) and alias:
                owners.setdefault(alias, label)
    for route in routes.get("oauth_routes") or []:
        if not isinstance(route, dict):
            continue
        label = f"oauth:{route.get('name')}"
        for model in route.get("models") or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias")
            if isinstance(alias, str) and alias:
                owners.setdefault(alias, label)
    for route in routes.get("plugin_routes", []):
        label = f"plugin:{route.get('provider')}:{route.get('realm')}"
        for alias in _plugin_route_aliases(routes, route):
            if isinstance(alias, str) and alias:
                owners.setdefault(alias, label)
    return owners


def _duplicate_aliases(routes: dict[str, Any]) -> list[tuple[str, list[str]]]:
    seen: dict[str, list[str]] = {}
    for provider in routes.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        label = f"slot{provider.get('slot')}:{provider.get('name')}"
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias")
            if isinstance(alias, str) and alias:
                seen.setdefault(alias, []).append(label)
    for route in routes.get("codex_api_key_routes") or []:
        if not isinstance(route, dict):
            continue
        label = f"codex-key:{route.get('name', route.get('base-url'))}"
        for model in route.get("models") or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias") or model.get("name")
            if isinstance(alias, str) and alias:
                seen.setdefault(alias, []).append(label)
    for route in routes.get("claude_api_key_routes") or []:
        if not isinstance(route, dict):
            continue
        label = f"claude-key:{route.get('name', route.get('base-url'))}"
        for model in route.get("models") or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias") or model.get("name")
            if isinstance(alias, str) and alias:
                seen.setdefault(alias, []).append(label)
    for route in routes.get("oauth_routes") or []:
        if not isinstance(route, dict):
            continue
        label = f"oauth:{route.get('name')}"
        for model in route.get("models") or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias")
            if isinstance(alias, str) and alias:
                seen.setdefault(alias, []).append(label)
    for route in routes.get("plugin_routes", []):
        label = f"plugin:{route.get('provider')}:{route.get('realm')}"
        for alias in _plugin_route_aliases(routes, route):
            if isinstance(alias, str) and alias:
                seen.setdefault(alias, []).append(label)
    return [(alias, owners) for alias, owners in seen.items() if len(owners) > 1]


def _pooled_aliases(routes: dict[str, Any]) -> set[str]:
    """Return aliases whose intentional pooling is explicit in the manifest."""

    raw = routes.get("pooled_aliases", [])
    if not isinstance(raw, list):
        return set()
    return {alias for alias in raw if isinstance(alias, str) and alias}


def _plugin_route_aliases(routes: dict[str, Any], route: dict[str, Any]) -> list[str]:
    """Return the client-visible names for one plugin realm.

    The WorkBuddy plugin keeps its upstream pin as ``deepseek-v4.1-flash`` on
    both realms. The INTL client-facing name is a manifest alias, so the
    admission and audit contracts must translate only that realm while
    leaving the CN bare name untouched.

    A ``fork: true`` alias exposes **both** names to clients: the alias and the
    bare upstream pin. cpa-workbuddy-plugin.md pins that contract as two rows
    for the INTL realm (``deepseek-v4.1-flash-intl`` and
    ``deepseek-v4.1-flash-sg``, both on ``workbuddy-intl-oauth``), and the
    admission lane must gate both. Reporting only the alias made the audit read
    the lane's own pin as a member no client can name. A non-fork alias still
    hides the pin, which is when the stale-member rule must fire.
    """

    provider = route.get("provider")
    realm = route.get("realm")
    aliases: dict[str, tuple[str, bool]] = {}
    raw_aliases = routes.get("oauth_model_aliases")
    if isinstance(raw_aliases, dict):
        entries = raw_aliases.get(provider)
        if isinstance(entries, list):
            for entry in entries:
                if (
                    isinstance(entry, dict)
                    and isinstance(entry.get("name"), str)
                    and isinstance(entry.get("alias"), str)
                ):
                    aliases[entry["name"].strip()] = (
                        entry["alias"].strip(),
                        entry.get("fork") is True,
                    )
    result: list[str] = []
    for model in route.get("models", []) or []:
        if not isinstance(model, str):
            continue
        if realm == "intl" and model in aliases:
            alias, forked = aliases[model]
            if forked and model not in result:
                result.append(model)
            if alias not in result:
                result.append(alias)
        elif model not in result:
            result.append(model)
    return result


def _plaintext_provider_findings(routes: dict[str, Any]) -> list[Finding]:
    """Report providers whose transport exposes credentials and payloads.

    The BWG manifest deliberately retains one plaintext HTTP relay. That is a
    user-accepted transport risk, not a routing defect, so it must not fail the
    audit. It must stay visible on every run: an on-path observer can read the
    provider key, prompts and responses, and no amount of account-side risk
    control changes that.
    """
    findings: list[Finding] = []
    for provider in routes.get("providers", []):
        if not isinstance(provider, dict):
            continue
        scheme = str(provider.get("scheme", "https")).lower()
        insecure = provider.get("allow_insecure_http") is True
        if scheme != "http" and not insecure:
            continue
        label = (
            provider.get("name")
            or provider.get("host")
            or f"slot{provider.get('slot')}"
        )
        findings.append(
            Finding(
                "plaintext-http-provider",
                SEVERITY_WARN,
                f"provider {label!r} uses plaintext HTTP or allow_insecure_http; "
                "API keys, prompts and responses are exposed to on-path observers. "
                "Retain only as an explicitly accepted transport risk and never "
                "send sensitive data through it",
            )
        )
    return findings


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
    for provider in routes.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        lane = provider.get("admission_lane")
        if not isinstance(lane, str) or not lane:
            continue
        aliases = [
            model["alias"]
            for model in provider.get("models") or []
            if isinstance(model, dict)
            and isinstance(model.get("alias"), str)
            and model["alias"]
        ]
        entries.append(
            (f"slot{provider.get('slot')}:{provider.get('name')}", lane, aliases)
        )
    for route in routes.get("codex_api_key_routes") or []:
        if not isinstance(route, dict):
            continue
        lane = route.get("admission_lane")
        if not isinstance(lane, str) or not lane:
            continue
        aliases = [
            model["alias"] or model["name"]
            for model in route.get("models") or []
            if isinstance(model, dict)
            and isinstance(model.get("alias") or model.get("name"), str)
        ]
        entries.append(
            (f"codex-key:{route.get('name', route.get('base-url'))}", lane, aliases)
        )
    for route in routes.get("claude_api_key_routes") or []:
        if not isinstance(route, dict):
            continue
        lane = route.get("admission_lane")
        if not isinstance(lane, str) or not lane:
            continue
        aliases = [
            model["alias"] or model["name"]
            for model in route.get("models") or []
            if isinstance(model, dict)
            and isinstance(model.get("alias") or model.get("name"), str)
        ]
        entries.append(
            (f"claude-key:{route.get('name', route.get('base-url'))}", lane, aliases)
        )
    for route in routes.get("oauth_routes") or []:
        if not isinstance(route, dict):
            continue
        lane = route.get("admission_lane")
        if not isinstance(lane, str) or not lane:
            continue
        aliases = [
            model["alias"]
            for model in route.get("models") or []
            if isinstance(model, dict)
            and isinstance(model.get("alias"), str)
            and model["alias"]
        ]
        entries.append((f"oauth:{route.get('name')}", lane, aliases))
    for route in routes.get("plugin_routes", []):
        lane = route.get("admission_lane")
        aliases = _plugin_route_aliases(routes, route)
        if isinstance(lane, str) and lane:
            entries.append(
                (f"plugin:{route.get('provider')}:{route.get('realm')}", lane, aliases)
            )
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


def newest_row_age_hours(db_path: pathlib.Path) -> float | None:
    """Hours between wall-clock now and the newest request_logs row.

    None means the database is absent or empty. A negative value (future
    timestamp) is returned as-is and counts as fresh: the check exists to catch
    a log that stopped updating, not to police clock skew.
    """
    if not db_path.exists():
        return None
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = connection.cursor()
        cursor.execute("select max(timestamp) from request_logs")
        newest = cursor.fetchone()[0]
        if newest is None:
            return None
        now_ms = time.time() * 1000.0
        return (now_ms - float(newest)) / 3_600_000.0
    finally:
        connection.close()


def catalog_routable_names(
    routes: dict[str, Any], *, include_retired: bool = False
) -> set[str]:
    """Return client-visible aliases, with tombstones removed unless requested.

    Retired names may still be served by a different route kind (for example,
    Antigravity advertises Claude aliases). A provider's tombstone therefore
    retires only that provider's copy of the name, not the name globally.
    :func:`audit_client_catalog` uses a global retired set for diagnostics.
    """
    names = _catalog_set(routes, include_retired=include_retired, provider_only=True)
    names.update(_catalog_set(routes, include_retired=include_retired))
    return names


def _retired_catalog_names(routes: dict[str, Any]) -> set[str]:
    tombstones = {
        name
        for provider in routes.get("providers") or []
        if isinstance(provider, dict)
        for name in provider.get("retired_models") or []
        if isinstance(name, str) and name and name != UNPARSED_MODEL
    }
    tombstones.update(
        name
        for name in routes.get("oauth_exclusions") or []
        if isinstance(name, str) and name and name != UNPARSED_MODEL
    )
    retired = tombstones.copy()
    for provider in routes.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        provider_retired = set(provider.get("retired_models") or ())
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias")
            if isinstance(alias, str) and alias and alias not in provider_retired:
                retired.discard(alias)
    for key in ("oauth_routes", "plugin_routes"):
        for route in routes.get(key) or []:
            if not isinstance(route, dict):
                continue
            aliases = (
                _plugin_route_aliases(routes, route)
                if key == "plugin_routes"
                else [
                    model.get("alias")
                    for model in route.get("models") or []
                    if isinstance(model, dict)
                ]
            )
            for alias in aliases:
                if isinstance(alias, str) and alias in tombstones:
                    retired.discard(alias)
    return retired


def _catalog_set(
    routes: dict[str, Any], *, include_retired: bool, provider_only: bool = False
) -> set[str]:
    names: set[str] = set()
    for provider in routes.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        retired = {
            name
            for name in provider.get("retired_models") or []
            if isinstance(name, str) and name
        }
        if include_retired and provider_only:
            names.update(retired)
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            alias = model.get("alias")
            if (
                isinstance(alias, str)
                and alias
                and alias != UNPARSED_MODEL
                and (include_retired or alias not in retired)
            ):
                names.add(alias)
    if provider_only:
        return names
    for key in ("oauth_routes", "plugin_routes"):
        for route in routes.get(key) or []:
            if not isinstance(route, dict):
                continue
            aliases = (
                _plugin_route_aliases(routes, route)
                if key == "plugin_routes"
                else [
                    model.get("alias")
                    for model in route.get("models") or []
                    if isinstance(model, dict)
                ]
            )
            names.update(
                name
                for name in aliases
                if isinstance(name, str) and name and name != UNPARSED_MODEL
            )
    return names


def audit_client_catalog(catalog: Any, routes: dict[str, Any]) -> list[Finding]:
    """Fail on client catalog names that the current routes cannot serve."""
    if not isinstance(catalog, dict):
        return [
            Finding(
                "client-catalog-unreadable",
                SEVERITY_WARN,
                "local client catalog is malformed; no client/model routing "
                "comparison was made",
            )
        ]
    models = catalog.get("models")
    default_model = catalog.get("default_model_id")
    if catalog.get("version") != CLIENT_CATALOG_SCHEMA_VERSION or not isinstance(
        models, list
    ):
        return [
            Finding(
                "client-catalog-schema",
                SEVERITY_WARN,
                "local client catalog schema differs from the reviewed writer; "
                "the client-to-gateway comparison is unavailable",
            )
        ]

    routable = catalog_routable_names(routes)
    retired = _retired_catalog_names(routes)
    seen: dict[str, int] = {}
    malformed: list[str] = []
    for position, model in enumerate(models):
        name = model.get("model_id") if isinstance(model, dict) else None
        if not isinstance(name, str) or not name:
            malformed.append(f"#{position}")
            continue
        seen[name] = seen.get(name, 0) + 1
    malformed.extend(
        f"{name!r}x{count}" for name, count in sorted(seen.items()) if count > 1
    )
    if malformed:
        return [
            Finding(
                "client-catalog-invalid-models",
                SEVERITY_FAIL,
                "local client catalog has malformed or duplicate model IDs: "
                + ", ".join(malformed[:12]),
            )
        ]

    findings = [
        Finding(
            "client-catalog-retired-model",
            SEVERITY_WARN,
            f"local client catalog advertises retired model {name!r}; remove "
            "that selection from the client rather than reviving the route or "
            "rotating credentials",
        )
        for name in sorted(seen.keys() & retired)
    ]
    findings.extend(
        Finding(
            "client-catalog-unroutable-model",
            SEVERITY_WARN,
            f"local client catalog advertises {name!r}, which is absent from "
            "the current route manifest; its selection cannot be admission-gated "
            "as the intended route",
        )
        for name in sorted(seen.keys() - routable - retired)
    )
    if default_model is not None and (
        not isinstance(default_model, str) or default_model not in routable
    ):
        findings.append(
            Finding(
                "client-catalog-default-unroutable",
                SEVERITY_FAIL,
                f"local client default model {default_model!r} is not currently "
                "routable",
            )
        )
    return findings


def observed_late_stream_failures(db_path: pathlib.Path, hours: float) -> int:
    """Requests that answered 2xx and still failed (the stream reported an error).

    This client-side count does not identify capacity or prove the admission
    breaker missed an event. SSE is inspected per event, while non-stream bodies
    use a bounded prefix. Correlate failures with the current admission journal.
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


def api_key_fallback_projection(routes: dict[str, Any]) -> list[Finding]:
    """Expose OAuth names without an official API-key route.

    This is a read-only capability projection, not an exclusion check. The
    manifest deliberately prevents subscription OAuth aliases from being
    silently served by a shared API-key route, so current exclusions remain
    correct even when they produce this list. It identifies names for which an
    operator may choose a separately reviewed official API route; it never
    adds credentials or changes routing.
    """

    providers = routes.get("providers")
    oauth_routes = routes.get("oauth_routes")
    if not isinstance(providers, list) or not isinstance(oauth_routes, list):
        return [
            Finding(
                "oauth-api-key-fallback-unreadable",
                SEVERITY_INFO,
                "provider or OAuth route inventory is malformed; official API-key "
                "fallback coverage was not assessed",
            )
        ]

    def records(value: list[Any]) -> list[dict[str, Any]]:
        return [item for item in value if isinstance(item, dict)]

    def route_aliases(route: dict[str, Any]) -> set[str]:
        models = route.get("models")
        if not isinstance(models, list):
            return set()
        return {
            model["alias"]
            for model in records(models)
            if isinstance(model.get("alias"), str) and model["alias"]
        }

    findings: list[Finding] = []
    provider_owners: dict[str, str] = {}
    oauth_names: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict):
            findings.append(
                Finding(
                    "oauth-api-key-fallback-unreadable",
                    SEVERITY_INFO,
                    "a provider route is malformed; official API-key fallback "
                    "coverage was not assessed",
                )
            )
            continue
        models = provider.get("models")
        if not isinstance(models, list):
            findings.append(
                Finding(
                    "oauth-api-key-fallback-unreadable",
                    SEVERITY_INFO,
                    "a provider route has a malformed model inventory; official "
                    "API-key fallback coverage was not assessed for that route",
                )
            )
            continue
        for alias in route_aliases(provider):
            provider_owners.setdefault(alias, "provider slot")
    for route in oauth_routes:
        if not isinstance(route, dict):
            findings.append(
                Finding(
                    "oauth-api-key-fallback-unreadable",
                    SEVERITY_INFO,
                    "an OAuth route is malformed; official API-key fallback "
                    "coverage was not assessed",
                )
            )
            continue
        owner = str(route.get("name") or "unnamed-oauth-route")
        if not isinstance(route.get("models"), list):
            findings.append(
                Finding(
                    "oauth-api-key-fallback-unreadable",
                    SEVERITY_INFO,
                    f"OAuth route {owner!r} has a malformed model inventory; "
                    "official API-key fallback coverage was not assessed for it",
                )
            )
            continue
        route_names = route_aliases(route)
        oauth_names.update(route_names)
        missing = sorted(route_names - set(provider_owners))
        if not missing:
            continue
        found = ", ".join(missing[:8])
        if len(missing) > 8:
            found += f", and {len(missing) - 8} more"
        findings.append(
            Finding(
                "oauth-api-key-fallback-missing",
                SEVERITY_INFO,
                f"OAuth route {owner!r} advertises {found} without a provider-slot "
                "route; no separately authorized official API-key fallback is "
                "configured. This is a capability projection, not an exclusion "
                "violation. Adding one requires an explicitly authorized official "
                "API project and budget; do not relabel OAuth as API billing or "
                "switch accounts automatically",
            )
        )
    for alias in sorted(oauth_names & set(provider_owners)):
        findings.append(
            Finding(
                "oauth-provider-name-overlap",
                SEVERITY_INFO,
                f"name {alias!r} is declared on both an OAuth route and a provider "
                "slot; duplicate-route rules decide which route serves it",
            )
        )
    return findings


def audit_contract(
    admission: dict[str, Any],
    routes: dict[str, Any],
) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(api_key_fallback_projection(routes))
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
    pooled_aliases = _pooled_aliases(routes)
    for alias, holders in sorted(duplicates):
        if alias in pooled_aliases:
            findings.append(
                Finding(
                    "alias-multi-slot-pooled",
                    SEVERITY_INFO,
                    f"alias {alias!r} is intentionally pooled across "
                    f"{', '.join(holders)}; the admission lane still owns the "
                    "shared-account budget; max-retry-credentials=1 forbids "
                    "rotating to another upstream credential within one "
                    "failed request, while request-retry=0 forbids extra "
                    "retry rounds",
                )
            )
            continue
        findings.append(
            Finding(
                "alias-multi-slot",
                SEVERITY_FAIL,
                f"alias {alias!r} is advertised by more than one route "
                f"({', '.join(holders)}); clients cannot predict the upstream",
            )
        )
    duplicate_names = {alias for alias, _ in duplicates}
    for alias in sorted(pooled_aliases - duplicate_names):
        findings.append(
            Finding(
                "pooled-alias-not-duplicate",
                SEVERITY_FAIL,
                f"pooled alias {alias!r} is declared without multiple route "
                "holders; remove the stale exception or restore the missing route",
            )
        )
    findings.extend(_plaintext_provider_findings(routes))

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
        for route in routes.get("oauth_routes") or []
        if isinstance(route, dict)
        for model in route.get("models") or []
        if isinstance(model, dict) and isinstance(model.get("alias"), str)
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
    # Mirror of the check above. A name advertised on a subscription OAuth
    # route but also pinned into oauth_exclusions is a silent dead route: the
    # manifest keeps offering it while the projected provider exclusion removes
    # it, so it consumes a lane slot but answers nowhere. The API-key direction
    # (a GPT route missing from oauth_exclusions) is the violation above; this
    # is the opposite, equally self-contradictory shape.
    for alias in sorted(oauth_aliases & oauth_exclusions):
        findings.append(
            Finding(
                "oauth-route-alias-excluded",
                SEVERITY_FAIL,
                f"OAuth route alias {alias!r} is also listed in oauth_exclusions; "
                "the manifest advertises it on the subscription lane but the "
                "projected exclusion removes it, leaving a dead advertised route",
            )
        )

    lane_membership: dict[str, str] = {}
    lane_models_by_name: dict[str, set[str]] = {}
    server_capped_lanes: list[str] = []
    server_capped_model_lanes: list[str] = []
    unreadable_cooldown_lanes: list[str] = []
    for lane in lanes:
        name = str(lane.get("name"))
        lane_models_by_name[name] = {str(model) for model in lane.get("models") or []}
        schedule = list(lane.get("cooldown_schedule_seconds") or [])
        ladder_cap = lane.get("cooldown_cap_seconds")
        if type(server_cap) is not int or server_cap < 1 or type(ladder_cap) is not int:
            unreadable_cooldown_lanes.append(name)
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
        model_markers = lane.get("model_capacity_markers")
        if model_markers is not None and not isinstance(model_markers, list):
            findings.append(
                Finding(
                    "lane-model-capacity-marker-type",
                    SEVERITY_FAIL,
                    f"lane {name} model_capacity_markers must be a list; "
                    "the runtime can otherwise reject the configuration",
                )
            )
        if (
            isinstance(server_cap, int)
            and isinstance(ladder_cap, int)
            and server_cap > ladder_cap
        ):
            server_capped_lanes.append(name)
            # Model-scoped markers and 429/403 affinity rebinding reach
            # `_record_model_capacity()` before the lane-wide release path.
            # The same Retry-After value is bounded by retry_after_max_seconds.
            if isinstance(model_markers, list) and model_markers:
                server_capped_model_lanes.append(name)

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
        normalized_model_markers = {
            str(marker).strip().lower() for marker in model_markers or []
        }
        if not normalized_model_markers <= markers:
            findings.append(
                Finding(
                    "lane-model-capacity-marker-scope",
                    SEVERITY_FAIL,
                    f"lane {name} scopes "
                    f"{sorted(normalized_model_markers - markers)!r} to one "
                    "model, but they are not lane capacity markers; a "
                    "credential-level signal would stop protecting the siblings",
                )
            )
        for reviewed_lane, required in REQUIRED_MODEL_CAPACITY_MARKERS.items():
            if reviewed_lane != name:
                continue
            for marker in required:
                if marker not in normalized_model_markers:
                    findings.append(
                        Finding(
                            "lane-model-capacity-scope-missing",
                            SEVERITY_FAIL,
                            f"lane {name} treats {marker!r} as a lane-wide signal; "
                            "one capped model would refuse its siblings on the "
                            "same credential",
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

    strict_capped_lanes = set(server_capped_lanes) & STRICT_COOLDOWN_LANES
    bounded_capped_lanes = set(server_capped_lanes) - strict_capped_lanes
    if server_capped_lanes:
        scope = ""
        if strict_capped_lanes:
            scope += (
                f"; strict lanes {', '.join(sorted(strict_capped_lanes))} honour "
                "the full positive upstream window without this cap"
            )
        if bounded_capped_lanes:
            scope += (
                f"; bounded lanes {', '.join(sorted(bounded_capped_lanes))} cap "
                f"the wait at {server_cap}s"
            )
        findings.append(
            Finding(
                "lane-cooldown-server-cap",
                SEVERITY_WARN,
                f"lanes {', '.join(sorted(server_capped_lanes))}: an upstream "
                "Retry-After can pin the whole lane beyond its failure ladder; "
                f"honouring it is the ban-safe choice. Gate bound={server_cap}s"
                f"{scope}. Read /healthz and the live policy before predicting the "
                "outage length",
            )
        )
    if server_capped_model_lanes:
        findings.append(
            Finding(
                "model-cooldown-server-cap",
                SEVERITY_WARN,
                f"lanes {', '.join(sorted(server_capped_model_lanes))}: a "
                f"model-scoped upstream Retry-After can park one model for up to "
                f"{server_cap}s, above its lane cooldown ladder; healthy sibling "
                "models remain eligible. Read "
                "model_cooldowns in /healthz, honour the provider's wait, and do not "
                "retry another account or shorten the window merely to restore it",
            )
        )
    if unreadable_cooldown_lanes:
        findings.append(
            Finding(
                "cooldown-window-unreadable",
                SEVERITY_WARN,
                f"lanes {', '.join(sorted(unreadable_cooldown_lanes))}: "
                "retry_after_max_seconds or cooldown_cap_seconds is not a positive "
                "integer; the audit cannot establish the maximum provider wait",
            )
        )
    if isinstance(probe_bytes, int) and probe_bytes < PROBE_BYTES_MIN:
        findings.append(
            Finding(
                "capacity-probe-window",
                SEVERITY_WARN,
                f"probe_bytes={probe_bytes} is below the reviewed baseline "
                f"{PROBE_BYTES_MIN}; protocol errors exceeding the per-event "
                "bound may be uninspectable",
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
    # retired_models is a per-provider tombstone list in the manifest (matching
    # the exclusion sets each provider block carries), not a top-level key.
    retired_models = {
        model
        for provider in routes.get("providers", [])
        if isinstance(provider, dict)
        for model in provider.get("retired_models") or ()
        if isinstance(model, str) and model
    }
    total, errors = observed_totals(db_path, hours)
    window = f"the {hours:g}h window ending at the newest client row"
    if total == 0:
        findings.append(
            Finding(
                "posture-no-traffic",
                SEVERITY_INFO,
                f"no client traffic in {window}; the advertised-route join "
                "is vacuous, so a clean result here proves nothing about routing",
            )
        )
        return findings
    findings.append(
        Finding(
            "posture-summary",
            SEVERITY_INFO,
            f"{errors}/{total} requests failed in {window} "
            f"({(100.0 * errors / total):.1f}%)",
        )
    )
    late = observed_late_stream_failures(db_path, hours)
    findings.append(
        Finding(
            "late-stream-failures",
            SEVERITY_INFO,
            f"{late} request(s) answered 2xx and still failed in {window}; "
            "client-log coverage only, not current gateway evidence. This count "
            "does not establish capacity or a missed breaker event; correlate with "
            "admission protocol_result/capacity records. SSE inspection is per event; "
            "non-stream inspection uses a bounded prefix. Do not change probe_bytes "
            "from this count alone",
        )
    )
    flagged = False
    last_failure_age = observed_last_failure_age_minutes(db_path, hours)
    live_failure = observed_live_failure_by_model(db_path, hours)
    data_age = newest_row_age_hours(db_path)
    data_stale = data_age is not None and data_age > POSTURE_DATA_STALE_HOURS
    if data_stale:
        findings.append(
            Finding(
                "posture-data-stale",
                SEVERITY_WARN,
                f"the newest client row is {data_age:.1f}h old "
                f"(> {POSTURE_DATA_STALE_HOURS:g}h); the log has stopped updating, so "
                "live-vs-historical classification in this run is unfalsifiable and "
                "failing routes are reported as warnings, not live failures",
            )
        )
    for model, (failed, seen) in observed_by_model(db_path, hours).items():
        # `other` is the bucket for requests whose model the log could not parse
        # (image calls, helper requests, non-chat endpoints). It is not a name a
        # route could advertise, so reporting it as an unresolvable model would
        # bury the real findings under a permanent warning.
        if model == UNPARSED_MODEL or failed < ADVERTISED_FAILURE_MIN or seen == 0:
            continue
        if model in retired_models:
            # A retired alias remains in both exclusion sets as a tombstone so
            # it cannot be revived silently. Those exclusions deliberately stop
            # routing it, so its retained failures are not an advertised route.
            continue
        rate = failed / seen
        if rate < ADVERTISED_FAILURE_RATE_WARN:
            severity, code = SEVERITY_INFO, "advertised-degraded-route"
        elif rate < ADVERTISED_FAILURE_RATE_FAIL:
            severity, code = SEVERITY_WARN, "advertised-degraded-route"
        elif data_stale:
            # The log itself is stale, so even the model's newest row cannot
            # prove the route is failing *now*; measured case (2026-10-07): the
            # desktop went direct on 10/5, the local gate database went silent,
            # and two-day-old failures kept printing a live FAIL verdict.
            severity, code = SEVERITY_WARN, "advertised-failing-route-stale-data"
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
                f"{model!r} failed {failed}/{seen} requests ({rate:.0%}) in "
                f"{window}{recency}{staleness}; the route manifest advertises this "
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
    parser.add_argument(
        "--client-catalog",
        type=pathlib.Path,
        default=DEFAULT_CLIENT_CATALOG,
        help="local Cockpit client catalog; compared with routable manifest names",
    )
    parser.add_argument(
        "--health-snapshot",
        type=pathlib.Path,
        help="saved localhost admission /healthz JSON for the live contract",
    )
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
    if args.health_snapshot is not None:
        try:
            findings += audit_live_health(load_json(args.health_snapshot))
        except (OSError, ValueError) as exc:
            findings.append(
                Finding(
                    "live-health-unreadable",
                    SEVERITY_FAIL,
                    f"the supplied admission health snapshot is unreadable ({type(exc).__name__})",
                )
            )
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
        if args.client_catalog.exists():
            try:
                findings += audit_client_catalog(load_json(args.client_catalog), routes)
            except (OSError, ValueError) as exc:
                findings.append(
                    Finding(
                        "client-catalog-unreadable",
                        SEVERITY_WARN,
                        f"local client catalog could not be read ({type(exc).__name__}); "
                        "client/model routing was not compared",
                    )
                )

    if args.json:
        print(
            json.dumps(
                {
                    "hours": args.hours,
                    "client_catalog": (
                        str(args.client_catalog)
                        if not args.skip_posture and args.client_catalog.exists()
                        else None
                    ),
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

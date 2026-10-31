"""Validate the BWG CPA semantic safety policy without exposing secrets."""

from __future__ import annotations

import json
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


# CPA runs inside Docker. The host-side Compose binding is the security
# boundary and is asserted separately by the guardrail script; the container
# must bind all interfaces so Nginx can reach it through the published
# loopback port. Kept as its own single-line statement so the bandit exemption
# below stays scoped to this value instead of the whole mapping literal.
_EXPECTED_CONTAINER_BIND_HOST = "0.0.0.0"  # nosec B104

# A claude-family group authenticates upstream with ``x-api-key``, a header CPA's
# ClaudeExecutor only emits for api.anthropic.com; every custom gateway gets
# ``Authorization: Bearer`` instead. The manifest therefore marks the
# credential's header with this placeholder instead of committing the secret,
# the projector substitutes the named slot's key, and the config check below
# resolves the same placeholder against the group's own configured key.
SLOT_API_KEY_HEADER_TOKEN = "<slot-api-key>"

# Expected values keyed by their canonical CPA v8 config-file paths
# (internal/config/config_v8.go buildV8Paths in the v8.0.23 source). The file
# layout is v8 since the 2026-10-11 management-API write migrated it
# (config-version: 8); the legacy top-level spellings are boundary aliases.
EXPECTED_CONFIG_PATHS: dict[tuple[str, ...], Any] = {
    ("server", "host"): _EXPECTED_CONTAINER_BIND_HOST,
    ("server", "port"): 8317,
    ("routing", "force-model-prefix"): True,
    ("routing", "retry", "request-retry"): 0,
    # Traverse every eligible credential in the current mixed-provider round.
    # CPA's zero value means "no fixed credential cap"; it does not add a
    # retry round. request-retry=0 still prevents another round after this
    # pass. This is required for the dual-homed DeepSeek route: WorkBuddy's
    # three credentials must be exhausted before the independent OpenCode
    # credential can be selected. The provider/plugin concurrency and session
    # affinity guards remain the request-amplification controls.
    ("routing", "retry", "max-retry-credentials"): 0,
    ("routing", "cooldown", "disable-cooling"): False,
    ("routing", "cooldown", "save-cooldown-status"): False,
    # 2026-10-31: 60 -> 30. Codex (single OAuth credential) turns every upstream
    # `server_is_overloaded` into "codex lane fully cooled, zero-egress
    # 503 auth_unavailable" for the whole window (gateway.md / failure-triage
    # #6851,#6852). Upstream sends `retry-after` 6-323s; the 60s floor overshot
    # short bursts. 30s keeps a real cool-off and still honors an explicit
    # upstream Retry-After. It is a request-amplification/dispersal knob, NOT a
    # ban-avoidance control; it never clears a quota cooldown or rotates.
    ("routing", "cooldown", "transient-error-cooldown-seconds"): 30,
    # Streaming SSE keep-alives (2026-10-13): heartbeats are downstream-only
    # ": keep-alive" comments (plus WS pings); they add zero upstream traffic.
    # 15s keeps slow-TTFB reasoning turns and silent mid-stream gaps alive for
    # clients whose idle read timeout is shorter than Nginx's 300s lane, so a
    # slow upstream costs a warm wait instead of a client abort plus a retry.
    ("requests", "streaming", "keepalive-seconds"): 15,
    ("requests", "streaming", "send-keepalive-before-ft"): True,
    # Error request dumps can contain prompts, headers, and client keys. Keep
    # CPA's own retained-file cap small; the updater also enforces a 24h age
    # bound independently.
    ("observability", "logs", "error-logs-max-files"): 5,
    # Bound aggregate log storage as well as the number of retained dumps.
    ("observability", "logs", "logs-max-total-size-mb"): 32,
    # Legacy quota-exceeded.antigravity-credits; v8 moves it under the
    # antigravity OAuth provider block (config_v8.go buildV8Paths).
    ("oauth", "providers", "antigravity", "antigravity-credits"): False,
    # Claude subscription OAuth lane (2026-10-17): claims only opus-5-5 /
    # sonnet-5-5 / haiku-5-5 per user decision; every other model the
    # credential exposes stays a provider-level exclusion tombstone. The
    # apply derives this list from the manifest claude_oauth_exclusions.
    ("oauth", "excluded-models", "claude"): [
        "claude-fable-5",
        "claude-fable-5-1",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-5-20251101",
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
        "claude-3-5-haiku-20241022",
        "claude-3-7-sonnet-20250219",
        "claude-opus-4-1-20250805",
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
    ],
    # Client-only catalog names are served by renaming an existing OAuth
    # upstream model, not by inventing a provider. gpt-reserve is the Codex
    # "GPT-5.6 Reserve" client slug; map the gpt-5.6 upstream to it while
    # keeping the bare gpt-5.6 name out of the OAuth catalog.
    ("oauth", "model-alias", "codex"): [
        {
            "name": "gpt-5.6-luna",
            "alias": "gpt-reserve",
            "display-name": "GPT-5.6 Reserve",
            "fork": True,
            "force-mapping": True,
        }
    ],
    # Retain the existing compatibility setting while this OAuth route remains
    # in use (CLIProxyAPI#5848). Payload-sensitive 429s were observed; the
    # upstream mechanism and any effect on account risk are unproven. This
    # rewrite is not a ban-prevention control; retire it with API migration.
    ("oauth", "providers", "antigravity", "sensitive-words"): ["gpt", "openai"],
    # Stabilized device profile: confirmed Claude Code callers contribute their
    # real software profile, persisted per credential (KV 7d TTL), avoiding
    # restart churn. Deliberately no UA/OS/timezone overrides set. Location is
    # the v8-canonical path (config_v8.go maps the legacy top-level
    # claude-header-defaults here); management-UI reserialization writes the
    # canonical form, so the check follows it.
    (
        "upstream",
        "claude",
        "header-defaults",
        "stabilize-device-profile",
    ): True,
}

EXPECTED_ROUTING = {
    "strategy": "fill-first",
    "session-affinity": True,
    "session-affinity-ttl": "1h",
    # CPA v8.0.45+ defaults this option to "any" when omitted.  Keep an
    # established session on the same credential for transient failures so a
    # short 5xx/empty-stream blip does not fan out across the account pool or
    # destroy prompt-cache locality.  Rebind only for quota/auth/model-support
    # and permanent-unavailability signals.
    "session-affinity-rebind-on": "quota",
    # Keep each child task eligible for a separate credential. Binding every
    # subagent to the parent improves cache locality but concentrates bursts on
    # a single account, which is the less safe default for this public gateway.
    "session-affinity-subagents": False,
}

EXPECTED_QUOTA = {
    "switch-project": False,
    "switch-preview-model": False,
}

EXPECTED_CODEX = {
    # Disabled on 2026-09-28 after measuring what the hold actually cost. While
    # bootstrap buffering is on, CPA keeps the downstream response headers
    # uncommitted until the upstream starts generating, so a turn whose model
    # reasons before its first token showed 9.4-10.3s of dead air and then
    # released the whole handshake as one burst (chunk-gap p50 = 0ms). The
    # overload classification it bought is redundant here: the credential pool
    # holds exactly one Codex account, so there is no alternate credential to
    # fail over to, and `cpa-admission` already classifies a capacity marker
    # inside an HTTP 200 body on its own (observed 13x/6h as
    # `status=200 capacity=true`). Off is also the upstream default. The
    # ceiling stays explicit at "0" so re-enabling needs a deliberate edit.
    "stream-bootstrap-buffering": False,
    "stream-bootstrap-timeout": "0",
    # Codex quota cooldowns: by default CPA cools the WHOLE credential for every
    # sibling model when one model hits usage_limit_reached. sol/luna/terra/
    # gpt-reserve share the single Plus account here, so one model reaching its
    # quota would black out the others. model-level-cooling scopes the cooldown
    # to the requested model only (config_types.go CodexConfig.ModelLevelCooling,
    # config_v8 maps oauth.providers.codex.model-level-cooling here). This is a
    # blast-radius control for capacity signals, not a ban/quota bypass.
    "model-level-cooling": True,
}

ROUTE_MANIFEST_PATH = Path(__file__).with_name("cpa_provider_routes.json")
try:
    ROUTE_MANIFEST: Any = json.loads(ROUTE_MANIFEST_PATH.read_text(encoding="utf-8"))
    ROUTE_MANIFEST_ERROR: str | None = None
except Exception as exc:  # fail closed if the projected route contract is absent
    ROUTE_MANIFEST = {}
    ROUTE_MANIFEST_ERROR = type(exc).__name__
if not isinstance(ROUTE_MANIFEST, dict):
    ROUTE_MANIFEST = {}
    ROUTE_MANIFEST_ERROR = ROUTE_MANIFEST_ERROR or "InvalidManifestType"

ADMISSION_CONFIG_PATH = Path(__file__).with_name("cpa-admission.json")
try:
    ADMISSION_CONFIG: Any = json.loads(
        ADMISSION_CONFIG_PATH.read_text(encoding="utf-8")
    )
    ADMISSION_CONFIG_ERROR: str | None = None
except Exception as exc:  # fail closed if the shared-account gate is absent
    ADMISSION_CONFIG = {}
    ADMISSION_CONFIG_ERROR = type(exc).__name__
if not isinstance(ADMISSION_CONFIG, dict):
    ADMISSION_CONFIG = {}
    ADMISSION_CONFIG_ERROR = ADMISSION_CONFIG_ERROR or "InvalidAdmissionConfigType"

EXPECTED_ADMISSION_MAX_INFLIGHT = {
    # Two, not one. A measured desktop turn runs 39-216s, so single-flight
    # starved every concurrent request for the whole turn and then rejected it
    # at the 120s queue budget -- the client abandons at ~45s, so those rejects
    # were guaranteed. Two slots let the desktop's usual main+auxiliary pair
    # overlap without opening the account to a fan-out; the other official
    # coding-plan lane has independent consumption and keeps its own bound.
    "chatgpt-oauth": 2,
    "zhipu-coding-plan": 3,
    # Antigravity (Google AI Pro) mirrors the ChatGPT OAuth bound: same
    # shared-subscription shape, manual low-frequency usage today.
    "antigravity-oauth": 2,
    # Claude (Anthropic Pro) mirrors the same shared-subscription bound.
    "claude-oauth": 2,
    "workbuddy-oauth": 1,
}

# Which capacity markers name a *model* rather than the shared credential.
# The ChatGPT OAuth lane serves several models from one subscription account,
# and the upstream answers `Selected model is at capacity` for one of them
# while still serving its siblings (measured 2026-10-10). That marker must be
# model-scoped: kept lane-wide it parked the whole lane, so the next
# `gpt-6.1-sol` request was refused with `retry_after=471` and the desktop
# reported a retry-limit 429 for a model that was never at capacity. The other
# lanes carry no model-named marker, and their capacity text stays
# credential-wide.
EXPECTED_MODEL_CAPACITY_MARKERS = {
    "chatgpt-oauth": frozenset({"selected model is at capacity", "model_at_capacity"}),
}

# Runtime state written by the guardrail quarantine transaction. It lives next
# to the deployed policy file and is never part of the projected source set.
QUARANTINE_MARKER_PATH = Path(__file__).with_name("oauth-quarantine.json")
ANTIGRAVITY_QUARANTINE_MARKER_PATH = Path(__file__).with_name(
    "antigravity-quarantine.json"
)
CLAUDE_OAUTH_QUARANTINE_MARKER_PATH = Path(__file__).with_name(
    "claude-oauth-quarantine.json"
)


def load_quarantine_aliases(
    marker_path: Path | None = None,
    provider: str = "codex",
) -> tuple[frozenset[str], list[str]]:
    """Return (quarantined OAuth aliases, issues) from the quarantine marker.

    Blocking a configured OAuth route alias is only legitimate while the
    operator-written marker authorizes exactly those aliases. Without a marker
    the exclusion list must keep every OAuth route available; a present but
    unreadable or inconsistent marker fails closed instead of silently
    re-exposing the subscription lane.

    ``marker_path`` defaults to the deployed runtime marker next to this file.
    It is an explicit parameter rather than a module global lookup so callers
    and tests can point at an isolated marker without mutating module state.
    """
    if provider not in ("codex", "antigravity", "claude"):
        return frozenset(), ["unknown OAuth quarantine provider"]
    default_path = (
        QUARANTINE_MARKER_PATH
        if provider == "codex"
        else (
            ANTIGRAVITY_QUARANTINE_MARKER_PATH
            if provider == "antigravity"
            else CLAUDE_OAUTH_QUARANTINE_MARKER_PATH
        )
    )
    path = default_path if marker_path is None else marker_path
    if not path.exists():
        return frozenset(), []
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # fail closed on any marker read error
        return frozenset(), [
            f"unreadable OAuth quarantine marker: {type(exc).__name__}"
        ]
    if not isinstance(marker, dict) or marker.get("version") != 1:
        return frozenset(), ["OAuth quarantine marker must be a version 1 mapping"]
    if marker.get("state") != "quarantined":
        return frozenset(), ["OAuth quarantine marker state must be 'quarantined'"]
    if marker.get("provider", "codex") != provider:
        return frozenset(), ["OAuth quarantine marker provider mismatch"]
    aliases = marker.get("aliases")
    if not isinstance(aliases, list) or not all(
        isinstance(alias, str) and alias.strip() for alias in aliases
    ):
        return frozenset(), [
            "OAuth quarantine marker aliases must be a list of non-empty strings"
        ]
    normalized = frozenset(alias.strip().lower() for alias in aliases)
    if not normalized:
        return frozenset(), ["OAuth quarantine marker must name at least one alias"]
    known_aliases = {
        "codex": EXPECTED_OAUTH_ROUTE_ALIASES,
        "antigravity": EXPECTED_ANTIGRAVITY_ROUTE_ALIASES,
        "claude": EXPECTED_CLAUDE_ROUTE_ALIASES,
    }[provider]
    known = {alias.lower() for alias in known_aliases}
    unexpected = sorted(normalized - known)
    if unexpected:
        return normalized, [
            f"OAuth quarantine marker names non-OAuth aliases: {unexpected!r}"
        ]
    if normalized != known:
        return normalized, [
            "OAuth quarantine marker must name every configured OAuth alias"
        ]
    fields = {
        "codex": ("previous_codex_exclusions", "applied_codex_exclusions"),
        "antigravity": (
            "previous_provider_exclusions",
            "applied_provider_exclusions",
        ),
        "claude": (
            "previous_claude_exclusions",
            "applied_claude_exclusions",
        ),
    }[provider]
    for field in fields:
        value = marker.get(field)
        if not isinstance(value, list) or not all(
            isinstance(pattern, str) and pattern.strip() for pattern in value
        ):
            return normalized, [
                f"OAuth quarantine marker {field} must be a list of non-empty strings"
            ]
    if provider == "claude":
        previous = {item.strip().lower() for item in marker[fields[0]]}
        applied = {item.strip().lower() for item in marker[fields[1]]}
        expected_previous = {
            item.strip().lower()
            for item in EXPECTED_CONFIG_PATHS[("oauth", "excluded-models", "claude")]
        }
        if previous != expected_previous:
            return normalized, [
                "Claude OAuth quarantine marker previous exclusions drifted"
            ]
        if applied != expected_previous | {"claude-*"}:
            return normalized, [
                "Claude OAuth quarantine marker applied exclusions must add claude-*"
            ]
    return normalized, []


def _manifest_strings(key: str) -> list[str]:
    value = ROUTE_MANIFEST.get(key, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _route_manifest_issues(manifest: Any) -> list[str]:
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("version")) is not int
        or manifest["version"] != 1
    ):
        return ["route manifest must be a version 1 mapping"]
    providers = manifest.get("providers")
    if not isinstance(providers, list) or not providers:
        return ["route manifest providers must be a non-empty list"]
    issues: list[str] = []
    raw_apply_patch_filter_models = manifest.get("apply_patch_filter_models")
    if (
        not isinstance(raw_apply_patch_filter_models, list)
        or not raw_apply_patch_filter_models
        or any(
            not isinstance(model, str) or not model
            for model in raw_apply_patch_filter_models
        )
        or len(raw_apply_patch_filter_models)
        != len(set(raw_apply_patch_filter_models))
    ):
        issues.append(
            "route manifest apply_patch_filter_models must be a non-empty list "
            "of unique non-empty model names"
        )
        raw_apply_patch_filter_models = []
    slots: set[int] = set()
    hosts: set[str] = set()
    names: set[str] = set()
    aliases: set[str] = set()
    gpt_routes: set[str] = set()
    provider_model_names: set[str] = set()
    for index, provider in enumerate(providers):
        label = f"route manifest providers[{index}]"
        if not isinstance(provider, dict):
            issues.append(f"{label} must be a mapping")
            continue
        slot = provider.get("slot")
        name = provider.get("name")
        host = provider.get("host")
        scheme = provider.get("scheme", "https")
        port = provider.get("port")
        allow_insecure_http = provider.get("allow_insecure_http", False)
        path = provider.get("path")
        models = provider.get("models")
        if type(slot) is not int or slot <= 0 or slot in slots:
            issues.append(f"{label}.slot must be a unique positive integer")
        else:
            slots.add(slot)
        if not isinstance(name, str) or not name or name in names:
            issues.append(f"{label}.name must be unique and non-empty")
        else:
            names.add(name)
        if (
            not isinstance(host, str)
            or not host
            or host.lower() != host
            or host in hosts
        ):
            issues.append(f"{label}.host must be unique, lowercase, and non-empty")
        else:
            hosts.add(host)
        if scheme == "https":
            if port is not None or allow_insecure_http is not False:
                issues.append(
                    f"{label} HTTPS routes must not set port or allow_insecure_http"
                )
        elif scheme == "http":
            if not (
                slot == 3
                and host == "35.213.82.91"
                and type(port) is int
                and port == 8003
                and allow_insecure_http is True
            ):
                issues.append(
                    f"{label} HTTP is allowed only for explicitly authorized slot 3"
                )
        else:
            issues.append(
                f"{label}.scheme must be https or the authorized slot 3 http route"
            )
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            issues.append(f"{label}.port must be a valid TCP port when present")
        if not isinstance(path, str) or (
            path and (not path.startswith("/") or "?" in path or "#" in path)
        ):
            issues.append(f"{label}.path must be empty or an absolute URL path")
        if not isinstance(models, list) or not models:
            issues.append(f"{label}.models must be a non-empty list")
            models = []
        for model_index, model in enumerate(models):
            model_label = f"{label}.models[{model_index}]"
            if not isinstance(model, dict):
                issues.append(f"{model_label} must be a mapping")
                continue
            model_name = model.get("name")
            alias = model.get("alias")
            if (
                not isinstance(model_name, str)
                or not model_name
                or not isinstance(alias, str)
                or not alias
            ):
                issues.append(f"{model_label} name and alias must be non-empty strings")
                continue
            max_context_length = model.get("max-context-length")
            if type(max_context_length) is not int or max_context_length <= 0:
                issues.append(
                    f"{model_label}.max-context-length must be a positive integer"
                )
            if alias.lower() in aliases:
                issues.append(f"route alias {alias!r} is assigned more than once")
            aliases.add(alias.lower())
            if alias.lower().startswith(("gpt-", "codex-")):
                gpt_routes.add(alias)
            provider_model_names.add(model_name)
        shared_consumption = provider.get("shared_consumption", False)
        workload_class = provider.get("workload_class")
        admission_lane = provider.get("admission_lane")
        if type(shared_consumption) is not bool:
            issues.append(f"{label}.shared_consumption must be a boolean")
        expected_shared = {
            4: ("official-coding-plan", "zhipu-coding-plan"),
            5: ("official-api", "deepseek-official"),
        }
        if slot in expected_shared:
            expected_workload, expected_lane = expected_shared[slot]
            if shared_consumption is not True:
                issues.append(
                    f"{label}.shared_consumption must be true for the shared "
                    "official-account route"
                )
            if workload_class != expected_workload:
                issues.append(f"{label}.workload_class must be {expected_workload!r}")
            if admission_lane != expected_lane:
                issues.append(f"{label}.admission_lane must be {expected_lane!r}")
        elif shared_consumption:
            issues.append(
                f"{label}.shared_consumption is only allowed on the reviewed "
                "official-account slots"
            )
        elif workload_class is not None or admission_lane is not None:
            issues.append(
                f"{label} must omit workload_class/admission_lane unless "
                "shared_consumption is true"
            )
        optional_models = provider.get("optional_models", [])
        if not isinstance(optional_models, list) or not all(
            isinstance(model, str) for model in optional_models
        ):
            issues.append(f"{label}.optional_models must be a list of model aliases")
        elif not set(optional_models) <= {
            model.get("alias") for model in models if isinstance(model, dict)
        }:
            issues.append(f"{label}.optional_models must be declared provider aliases")
        image_models = provider.get("image_models", [])
        if not isinstance(image_models, list) or not all(
            isinstance(model, str) for model in image_models
        ):
            issues.append(f"{label}.image_models must be a list of model aliases")
        elif not set(image_models) <= {
            model.get("alias") for model in models if isinstance(model, dict)
        }:
            issues.append(f"{label}.image_models must be declared provider aliases")
        # Static pinned headers are the only reviewed transport override; the
        # exact mapping is pushed to the provider config verbatim by -Apply.
        route_headers = provider.get("headers", {})
        if not isinstance(route_headers, dict) or not all(
            isinstance(header_name, str)
            and header_name
            and header_name == header_name.lower()
            and isinstance(header_value, str)
            and header_value.strip()
            for header_name, header_value in route_headers.items()
        ):
            issues.append(
                f"{label}.headers must be a mapping of lowercase header names "
                "to non-empty values when present"
            )
    if set(raw_apply_patch_filter_models) - provider_model_names:
        issues.append(
            "route manifest apply_patch_filter_models must be declared provider names"
        )
    oauth_routes = manifest.get("oauth_routes")
    if not isinstance(oauth_routes, list) or not oauth_routes:
        issues.append("route manifest oauth_routes must be a non-empty list")
        oauth_routes = []
    oauth_names: set[str] = set()
    oauth_aliases: set[str] = set()
    for index, route in enumerate(oauth_routes):
        label = f"route manifest oauth_routes[{index}]"
        if not isinstance(route, dict):
            issues.append(f"{label} must be a mapping")
            continue
        name = route.get("name")
        models = route.get("models")
        if not isinstance(name, str) or not name or name in oauth_names:
            issues.append(f"{label}.name must be unique and non-empty")
        else:
            oauth_names.add(name)
        if not isinstance(models, list) or not models:
            issues.append(f"{label}.models must be a non-empty list")
            continue
        for model_index, model in enumerate(models):
            model_label = f"{label}.models[{model_index}]"
            if not isinstance(model, dict):
                issues.append(f"{model_label} must be a mapping")
                continue
            model_name = model.get("name")
            alias = model.get("alias")
            if (
                not isinstance(model_name, str)
                or not model_name
                or not isinstance(alias, str)
                or not alias
            ):
                issues.append(f"{model_label} name and alias must be non-empty strings")
                continue
            normalized_alias = alias.lower()
            if normalized_alias in oauth_aliases or normalized_alias in aliases:
                issues.append(f"client alias {alias!r} is assigned to multiple routes")
            oauth_aliases.add(normalized_alias)
        shared_consumption = route.get("shared_consumption", False)
        workload_class = route.get("workload_class")
        admission_lane = route.get("admission_lane")
        if type(shared_consumption) is not bool:
            issues.append(f"{label}.shared_consumption must be a boolean")
        # Every shared subscription OAuth route must be gated by the admission
        # lane pinned for its reviewed name; an ungated shared credential is
        # the exact shape of a self-inflicted ban.
        expected_oauth_lanes = {
            "chatgpt-plus-oauth": "chatgpt-oauth",
            "antigravity-oauth": "antigravity-oauth",
            "claude-oauth": "claude-oauth",
        }
        if shared_consumption is True:
            if workload_class != "subscription-oauth":
                issues.append(f"{label}.workload_class must be 'subscription-oauth'")
            expected_lane = (
                expected_oauth_lanes.get(name) if isinstance(name, str) else None
            )
            if expected_lane is None:
                issues.append(f"{label}.name is not a reviewed shared OAuth route")
            elif admission_lane != expected_lane:
                issues.append(f"{label}.admission_lane must be {expected_lane!r}")
        else:
            issues.append(
                f"{label}.shared_consumption must be true for the ChatGPT OAuth lane"
            )
    oauth_settings = manifest.get("oauth_settings")
    if not isinstance(oauth_settings, dict) or not oauth_settings:
        issues.append("route manifest oauth_settings must be a non-empty mapping")
        oauth_settings = {}
    oauth_route_models: dict[str, set[str]] = {}
    for route in oauth_routes:
        if not isinstance(route, dict) or not isinstance(route.get("name"), str):
            continue
        route_models = route.get("models")
        if not isinstance(route_models, list):
            continue
        route_names = {
            model["name"]
            for model in route_models
            if isinstance(model, dict)
            and isinstance(model.get("name"), str)
            and model["name"]
        }
        oauth_route_models[route["name"]] = route_names
    for channel, settings in oauth_settings.items():
        label = f"route manifest oauth_settings.{channel}"
        if not isinstance(channel, str) or not channel.strip():
            issues.append("route manifest oauth_settings channels must be non-empty")
            continue
        if not isinstance(settings, list) or not settings:
            issues.append(f"{label} must be a non-empty list")
            continue
        seen_settings: set[tuple[str, str | None]] = set()
        for setting_index, setting in enumerate(settings):
            setting_label = f"{label}[{setting_index}]"
            if not isinstance(setting, dict):
                issues.append(f"{setting_label} must be a mapping")
                continue
            if not set(setting) <= {"name", "alias", "max-context-length"}:
                issues.append(f"{setting_label} has unsupported fields")
            setting_name = setting.get("name")
            setting_alias = setting.get("alias")
            max_context_length = setting.get("max-context-length")
            if not isinstance(setting_name, str) or not setting_name.strip():
                issues.append(f"{setting_label}.name must be a non-empty string")
            if setting_alias is not None and (
                not isinstance(setting_alias, str) or not setting_alias.strip()
            ):
                issues.append(
                    f"{setting_label}.alias must be a non-empty string when present"
                )
            if type(max_context_length) is not int or max_context_length <= 0:
                issues.append(
                    f"{setting_label}.max-context-length must be a positive integer"
                )
            key = (
                setting_name.strip() if isinstance(setting_name, str) else "",
                setting_alias.strip() if isinstance(setting_alias, str) else None,
            )
            if key in seen_settings:
                issues.append(f"{label} contains duplicate model settings")
            seen_settings.add(key)
        route_name = {
            "codex": "chatgpt-plus-oauth",
            "antigravity": "antigravity-oauth",
        }.get(channel)
        plugin_models = {
            model_name
            for route in manifest.get("plugin_routes", [])
            if isinstance(route, dict)
            and route.get("provider") == channel
            for model_name in route.get("models", [])
            if isinstance(model_name, str)
        }
        declared_models = oauth_route_models.get(route_name, set()) | plugin_models
        for setting in settings:
            if not isinstance(setting, dict):
                continue
            name = setting.get("name")
            alias = setting.get("alias")
            if (
                isinstance(name, str)
                and name.strip()
                and declared_models
                and name.strip() not in declared_models
                and (not isinstance(alias, str) or alias.strip() not in declared_models)
            ):
                issues.append(
                    f"{label} names undeclared model {name.strip()!r}"
                )
    retired = manifest.get("retired_hosts")
    if not isinstance(retired, list) or not all(
        isinstance(host, str) and host for host in retired
    ):
        issues.append("route manifest retired_hosts must be a list of non-empty hosts")
        retired = []
    if hosts.intersection(retired):
        issues.append("route manifest cannot include a retired provider host")
    static_antigravity_models = manifest.get("antigravity_upstream_static_models")
    if not isinstance(static_antigravity_models, list) or not all(
        isinstance(model, str) and model.strip() for model in static_antigravity_models
    ):
        issues.append(
            "route manifest antigravity_upstream_static_models must be a list "
            "of non-empty model IDs"
        )
    else:
        static_ids = {model.strip().lower() for model in static_antigravity_models}
        if len(static_ids) != len(static_antigravity_models):
            issues.append(
                "route manifest antigravity_upstream_static_models must be unique"
            )
        antigravity_aliases = {
            model["alias"].strip().lower()
            for route in oauth_routes
            if isinstance(route, dict) and route.get("name") == "antigravity-oauth"
            for model in route.get("models", [])
            if isinstance(model, dict)
            and isinstance(model.get("alias"), str)
            and model["alias"].strip()
        }
        raw_antigravity_exclusions = manifest.get("antigravity_oauth_exclusions")
        if not isinstance(raw_antigravity_exclusions, list) or not all(
            isinstance(model, str) and model.strip()
            for model in raw_antigravity_exclusions
        ):
            issues.append(
                "route manifest antigravity_oauth_exclusions must be a list "
                "of non-empty model IDs"
            )
        else:
            excluded_ids = {
                model.strip().lower() for model in raw_antigravity_exclusions
            }
            uncovered = sorted(static_ids - antigravity_aliases - excluded_ids)
            duplicated = sorted(static_ids & antigravity_aliases & excluded_ids)
            if uncovered:
                issues.append(
                    f"uncovered Antigravity upstream static models: {uncovered!r}"
                )
            if duplicated:
                issues.append(
                    "Antigravity upstream static models cannot be both routed "
                    f"and excluded: {duplicated!r}"
                )
    # API-key route families behind api-keys.<family> groups: the codex family
    # speaks native /responses upstream, the claude family speaks native
    # Anthropic Messages. Aliases share the global namespace, and codex-family
    # aliases must additionally stay off the GPT-/Codex- prefix so OAuth-lane
    # pinning stays unambiguous.
    for family, reserved_prefixes in (
        ("codex_api_key_routes", ("gpt-", "codex-")),
        ("claude_api_key_routes", ()),
    ):
        family_routes = manifest.get(family)
        if not isinstance(family_routes, list):
            issues.append(f"route manifest {family} must be a list")
            continue
        family_route_names: set[str] = set()
        for index, route in enumerate(family_routes):
            label = f"route manifest {family}[{index}]"
            if not isinstance(route, dict):
                issues.append(f"{label} must be a mapping")
                continue
            route_name = route.get("name")
            base_url = route.get("base-url")
            slot = route.get("api_key_slot")
            models = route.get("models")
            if (
                not isinstance(route_name, str)
                or not route_name
                or route_name in family_route_names
            ):
                issues.append(f"{label}.name must be unique and non-empty")
            else:
                family_route_names.add(route_name)
            parsed_route = None
            if isinstance(base_url, str) and base_url:
                try:
                    parsed_route = urlparse(base_url)
                except ValueError:
                    parsed_route = None
            if (
                parsed_route is None
                or parsed_route.scheme != "https"
                or not parsed_route.hostname
                or parsed_route.username is not None
                or parsed_route.password is not None
                or parsed_route.query
                or parsed_route.fragment
                or not parsed_route.path
                or parsed_route.path == "/"
            ):
                issues.append(
                    f"{label}.base-url must be an exact https URL with a path"
                )
            if (
                type(slot) is not int
                or not 1 <= slot <= 65535
                or slot
                not in {
                    provider.get("slot")
                    for provider in manifest.get("providers", [])
                    if isinstance(provider, dict)
                }
            ):
                issues.append(
                    f"{label}.api_key_slot must name an existing provider slot"
                )
            route_headers = route.get("headers", {})
            if not isinstance(route_headers, dict) or not all(
                isinstance(header_name, str)
                and header_name
                and header_name == header_name.lower()
                and isinstance(header_value, str)
                and header_value.strip()
                for header_name, header_value in route_headers.items()
            ):
                issues.append(
                    f"{label}.headers must be a mapping of lowercase header names "
                    "to non-empty values when present"
                )
            elif family != "claude_api_key_routes" and any(
                SLOT_API_KEY_HEADER_TOKEN in header_value
                for header_value in route_headers.values()
            ):
                # Only a claude-family group carries the slot credential as a
                # header; the projector substitutes the placeholder there and
                # nowhere else.
                issues.append(
                    f"{label}.headers must not use {SLOT_API_KEY_HEADER_TOKEN}; "
                    "only claude-family routes carry a slot credential header"
                )
            if not isinstance(models, list) or not models:
                issues.append(f"{label}.models must be a non-empty list")
                continue
            for model_index, model in enumerate(models):
                model_label = f"{label}.models[{model_index}]"
                if not isinstance(model, dict):
                    issues.append(f"{model_label} must be a mapping")
                    continue
                model_name = model.get("name")
                alias = model.get("alias")
                if (
                    not isinstance(model_name, str)
                    or not model_name
                    or not isinstance(alias, str)
                    or not alias
                ):
                    issues.append(
                        f"{model_label} name and alias must be non-empty strings"
                    )
                    continue
                normalized_alias = alias.lower()
                if normalized_alias in oauth_aliases or normalized_alias in aliases:
                    issues.append(
                        f"client alias {alias!r} is assigned to multiple routes"
                    )
                if reserved_prefixes and normalized_alias.startswith(reserved_prefixes):
                    issues.append(
                        f"{label} alias {alias!r} must not use the reserved GPT/Codex prefix"
                    )
                aliases.add(normalized_alias)
    oauth_exclusions = manifest.get("oauth_exclusions")
    if not isinstance(oauth_exclusions, list) or not all(
        isinstance(model, str) and model for model in oauth_exclusions
    ):
        issues.append("route manifest oauth_exclusions must be a list of model aliases")
    elif not gpt_routes <= set(oauth_exclusions):
        issues.append(
            f"route manifest oauth_exclusions must pin all GPT/Codex routes: "
            f"{sorted(gpt_routes - set(oauth_exclusions))!r}"
        )
    api_key_exclusions = manifest.get("codex_api_key_exclusions")
    if not isinstance(api_key_exclusions, list) or not all(
        isinstance(model, str) and model for model in api_key_exclusions
    ):
        issues.append(
            "route manifest codex_api_key_exclusions must be a list of model aliases"
        )
    elif not (gpt_routes | oauth_aliases) <= {
        model.lower() for model in api_key_exclusions
    }:
        issues.append(
            "route manifest must exclude every GPT/Codex and OAuth route alias "
            "from Codex API-key routes"
        )
    return issues


def _expected_admission_lanes(manifest: Any) -> dict[str, set[str]]:
    expected: dict[str, set[str]] = {}
    if not isinstance(manifest, dict):
        return expected
    providers = manifest.get("providers", [])
    if isinstance(providers, list):
        for provider in providers:
            if (
                not isinstance(provider, dict)
                or provider.get("shared_consumption") is not True
            ):
                continue
            lane = provider.get("admission_lane")
            models = provider.get("models")
            if not isinstance(lane, str) or not lane or not isinstance(models, list):
                continue
            expected.setdefault(lane, set()).update(
                model["alias"]
                for model in models
                if isinstance(model, dict) and isinstance(model.get("alias"), str)
            )
    oauth_routes = manifest.get("oauth_routes", [])
    if isinstance(oauth_routes, list):
        for route in oauth_routes:
            if (
                not isinstance(route, dict)
                or route.get("shared_consumption") is not True
            ):
                continue
            lane = route.get("admission_lane")
            models = route.get("models")
            if not isinstance(lane, str) or not lane or not isinstance(models, list):
                continue
            expected.setdefault(lane, set()).update(
                model["alias"]
                for model in models
                if isinstance(model, dict) and isinstance(model.get("alias"), str)
            )
    for route in manifest.get("plugin_routes", []):
        if isinstance(route, dict) and route.get("shared_consumption") is True:
            lane = route.get("admission_lane")
            models = route.get("models")
            if isinstance(lane, str) and isinstance(models, list):
                expected.setdefault(lane, set()).update(
                    model for model in models if isinstance(model, str) and model
                )
    return expected


def _admission_config_issues(manifest: Any, admission: Any) -> list[str]:
    issues: list[str] = []
    if ADMISSION_CONFIG_ERROR is not None:
        issues.append(
            f"unable to read shared-account admission config: {ADMISSION_CONFIG_ERROR}"
        )
    if not isinstance(admission, dict):
        return [*issues, "admission config must be a version 1 mapping"]
    if type(admission.get("version")) is not int or admission["version"] != 1:
        issues.append("admission config must be a version 1 mapping")
    if admission.get("listen_host") != "127.0.0.1":
        issues.append("admission listen_host must be 127.0.0.1")
    if admission.get("upstream_host") != "127.0.0.1":
        issues.append("admission upstream_host must be 127.0.0.1")
    if (
        type(admission.get("listen_port")) is not int
        or admission["listen_port"] != 8318
    ):
        issues.append("admission listen_port must be 8318")
    if (
        type(admission.get("upstream_port")) is not int
        or admission["upstream_port"] != 8317
    ):
        issues.append("admission upstream_port must be 8317")
    for key in ("max_body_bytes", "probe_bytes", "retry_after_max_seconds"):
        value = admission.get(key)
        if type(value) is not int or value <= 0:
            issues.append(f"admission {key} must be a positive integer")
    max_body_bytes = admission.get("max_body_bytes")
    probe_bytes = admission.get("probe_bytes")
    if (
        type(max_body_bytes) is int
        and type(probe_bytes) is int
        and probe_bytes > max_body_bytes
    ):
        issues.append("admission probe_bytes must not exceed max_body_bytes")
    early_probe_interval = admission.get("early_probe_interval_seconds")
    if type(early_probe_interval) not in (int, float) or early_probe_interval <= 0:
        issues.append(
            "admission early_probe_interval_seconds must be a positive number"
        )

    lanes = admission.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        return [*issues, "admission lanes must be a non-empty list"]
    actual: dict[str, set[str]] = {}
    for index, lane in enumerate(lanes):
        label = f"admission lanes[{index}]"
        if not isinstance(lane, dict):
            issues.append(f"{label} must be a mapping")
            continue
        name = lane.get("name")
        models = lane.get("models")
        if not isinstance(name, str) or not name:
            issues.append(f"{label}.name must be non-empty")
            continue
        if name in actual:
            issues.append(f"admission lane {name!r} is duplicated")
            continue
        if (
            not isinstance(models, list)
            or not models
            or not all(isinstance(model, str) and model.strip() for model in models)
        ):
            issues.append(f"{label}.models must be a non-empty list of aliases")
            continue
        normalized_models = {model.strip().lower() for model in models}
        if len(normalized_models) != len(models):
            issues.append(f"{label}.models must not contain duplicates")
        actual[name] = normalized_models
        # The OAuth lane's reviewed concurrency (2, not 1 -- single-flight
        # starved concurrent requests for whole 39-216s desktop turns) is
        # recorded with its measurement in EXPECTED_ADMISSION_MAX_INFLIGHT
        # above. Every lane still has a queue budget that outlasts a typical
        # desktop turn.
        max_inflight = lane.get("max_inflight")
        expected_max_inflight = EXPECTED_ADMISSION_MAX_INFLIGHT.get(name)
        if expected_max_inflight is None:
            issues.append(f"{label}.name has no reviewed max_inflight contract")
        elif type(max_inflight) is not int or max_inflight != expected_max_inflight:
            issues.append(f"{label}.max_inflight must be {expected_max_inflight}")
        max_pending = lane.get("max_pending")
        if type(max_pending) is not int or max_pending != 4:
            issues.append(f"{label}.max_pending must be 4")
        queue_timeout = lane.get("queue_timeout_seconds")
        if type(queue_timeout) is not int or queue_timeout != 120:
            issues.append(f"{label}.queue_timeout_seconds must be 120")
        schedule = lane.get("cooldown_schedule_seconds")
        if (
            not isinstance(schedule, list)
            or not schedule
            or not all(type(item) is int and item > 0 for item in schedule)
        ):
            issues.append(
                f"{label}.cooldown_schedule_seconds must be a positive integer list"
            )
        schedule_cap = lane.get("cooldown_cap_seconds")
        if type(schedule_cap) is not int or schedule_cap <= 0:
            issues.append(f"{label}.cooldown_cap_seconds must be a positive integer")
        elif isinstance(schedule, list) and schedule and schedule[-1] != schedule_cap:
            issues.append(
                f"{label}.cooldown_schedule_seconds must end at cooldown_cap_seconds"
            )
        retry_after_max = admission.get("retry_after_max_seconds")
        if (
            type(schedule_cap) is int
            and type(retry_after_max) is int
            and schedule_cap > retry_after_max
        ):
            issues.append(
                f"{label}.cooldown_cap_seconds must not exceed retry_after_max_seconds"
            )
        statuses = lane.get("capacity_statuses")
        if (
            not isinstance(statuses, list)
            or not statuses
            or not all(
                type(status) is int and 100 <= status <= 599 for status in statuses
            )
        ):
            issues.append(f"{label}.capacity_statuses must contain HTTP statuses")
        elif 429 not in statuses:
            issues.append(f"{label}.capacity_statuses must include 429")
        markers = lane.get("capacity_markers")
        if (
            not isinstance(markers, list)
            or not markers
            or not all(isinstance(marker, str) and marker.strip() for marker in markers)
        ):
            issues.append(f"{label}.capacity_markers must be non-empty strings")
        model_markers = lane.get("model_capacity_markers", [])
        if not isinstance(model_markers, list) or not all(
            isinstance(marker, str) and marker.strip() for marker in model_markers
        ):
            issues.append(f"{label}.model_capacity_markers must be non-empty strings")
        else:
            normalized_model_markers = {
                marker.strip().lower() for marker in model_markers
            }
            if isinstance(markers, list) and not normalized_model_markers <= {
                str(marker).strip().lower() for marker in markers
            }:
                # A credential-shaped signal smuggled into the model scope
                # would stop protecting the shared account on its siblings.
                issues.append(
                    f"{label}.model_capacity_markers must be a subset of "
                    "capacity_markers"
                )
            expected_model_markers = EXPECTED_MODEL_CAPACITY_MARKERS.get(name)
            if expected_model_markers is None:
                if normalized_model_markers:
                    issues.append(
                        f"{label}.model_capacity_markers must be empty for a lane "
                        "without a reviewed model-scoped marker"
                    )
            elif normalized_model_markers != set(expected_model_markers):
                issues.append(
                    f"{label}.model_capacity_markers must be "
                    f"{sorted(expected_model_markers)!r}"
                )

    expected = _expected_admission_lanes(manifest)
    expected_normalized = {
        lane: {model.lower() for model in models} for lane, models in expected.items()
    }
    for lane in sorted(set(expected_normalized) - set(actual)):
        issues.append(f"admission config is missing shared lane {lane!r}")
    for lane in sorted(set(actual) - set(expected_normalized)):
        issues.append(f"admission config contains unreviewed lane {lane!r}")
    for lane in sorted(set(expected_normalized) & set(actual)):
        if actual[lane] != expected_normalized[lane]:
            issues.append(
                f"admission lane {lane!r} models={sorted(actual[lane])!r}; "
                f"expected {sorted(expected_normalized[lane])!r}"
            )
    return issues


_MANIFEST_ISSUES = _route_manifest_issues(ROUTE_MANIFEST)
_PROVIDER_ROUTES = (
    ROUTE_MANIFEST.get("providers", [])
    if isinstance(ROUTE_MANIFEST, dict)
    and isinstance(ROUTE_MANIFEST.get("providers"), list)
    else []
)
EXPECTED_PROVIDER_ROUTES = {
    provider.get("host"): provider
    for provider in _PROVIDER_ROUTES
    if isinstance(provider, dict) and isinstance(provider.get("host"), str)
}
LEGACY_CHANNEL_HOSTS = frozenset(_manifest_strings("retired_hosts"))
_VALID_PROVIDER_ROUTES = [
    provider
    for provider in _PROVIDER_ROUTES
    if isinstance(provider, dict)
    and isinstance(provider.get("host"), str)
    and isinstance(provider.get("models"), list)
    and all(
        isinstance(model, dict) and isinstance(model.get("name"), str)
        for model in provider["models"]
    )
]
EXPECTED_PROVIDER_MODELS = {
    provider["host"]: {model["name"] for model in provider["models"]}
    for provider in _VALID_PROVIDER_ROUTES
}
EXPECTED_CODEX_OAUTH_EXCLUSIONS = frozenset(_manifest_strings("oauth_exclusions"))
_OAUTH_ROUTES = (
    ROUTE_MANIFEST.get("oauth_routes", [])
    if isinstance(ROUTE_MANIFEST, dict)
    and isinstance(ROUTE_MANIFEST.get("oauth_routes"), list)
    else []
)
EXPECTED_OAUTH_ROUTE_ALIASES = frozenset(
    model["alias"]
    for route in _OAUTH_ROUTES
    if isinstance(route, dict)
    # The quarantinable OAuth lane is the ChatGPT Plus route. Other OAuth
    # routes (antigravity-oauth since 2026-10-14) are not touched by the
    # Luna quarantine/deactivation transactions, so their aliases stay out
    # of the marker-must-name-every-alias contract.
    and route.get("name") == "chatgpt-plus-oauth"
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
)
EXPECTED_ANTIGRAVITY_ROUTE_ALIASES = frozenset(
    model["alias"]
    for route in _OAUTH_ROUTES
    if isinstance(route, dict) and route.get("name") == "antigravity-oauth"
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
)
EXPECTED_CLAUDE_ROUTE_ALIASES = frozenset(
    model["alias"]
    for route in _OAUTH_ROUTES
    if isinstance(route, dict) and route.get("name") == "claude-oauth"
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
)
EXPECTED_ANTIGRAVITY_EXCLUSIONS = frozenset(
    _manifest_strings("antigravity_oauth_exclusions")
)
EXCLUSIVE_CODEX_API_KEY_ROUTES = frozenset(
    _manifest_strings("codex_api_key_exclusions")
)
# Managed codex-family routes (native /responses upstreams). Groups are
# matched by exact base-url; everything about a managed group is pinned.
_VALID_CODEX_ROUTES = [
    route
    for route in ROUTE_MANIFEST.get("codex_api_key_routes", [])
    if isinstance(route, dict)
    and isinstance(route.get("base-url"), str)
    and isinstance(route.get("models"), list)
]
EXPECTED_CODEX_ROUTE_GROUPS = {
    route["base-url"].rstrip("/"): route for route in _VALID_CODEX_ROUTES
}
# Managed claude-family routes (native Anthropic Messages upstreams; the
# executor appends /v1/messages?beta=true). Groups are matched by exact
# base-url; everything about a managed group is pinned.
_VALID_CLAUDE_ROUTES = [
    route
    for route in ROUTE_MANIFEST.get("claude_api_key_routes", [])
    if isinstance(route, dict)
    and isinstance(route.get("base-url"), str)
    and isinstance(route.get("models"), list)
]
EXPECTED_CLAUDE_ROUTE_GROUPS = {
    route["base-url"].rstrip("/"): route for route in _VALID_CLAUDE_ROUTES
}
# Managed codex-family upstreams speak native /responses, so the Codex
# desktop tool declarations reach them verbatim. Three shapes the desktop
# always sends are rejected by the zen upstream (measured 2026-10-11):
# tool_search whose `required` omits the optional `limit` (strict-schema 400
# "Missing 'limit'"), web_search carrying `search_content_types`
# (preview-only field), and `custom` freeform tools (apply_patch grammar).
# The pinned requests.payload rules rewrite exactly those three shapes per
# managed model; dropping them silently would put desktop traffic back on
# the raw 400s, so this fails closed. cpa-health additionally probes the
# shapes behaviorally on every codex-route generation check.
_CODEX_ROUTE_MODEL_NAMES = tuple(
    sorted(
        {
            model["name"]
            for route in _VALID_CODEX_ROUTES
            for model in route["models"]
            if isinstance(model, dict) and isinstance(model.get("name"), str)
        }
    )
)
# Managed claude-family upstreams take the native Anthropic Messages face,
# which the zen proxy validates strictly: Claude Code 2.1.300 sends three
# fields that upstream does not declare, each rejected with 400
# "[invalid_request_error] <path>: Extra inputs are not permitted" while the
# same body without it is 200 (measured 2026-10-16 on the zen go endpoint):
# the top-level `safeguards` classifier block (permission-mode context), the
# top-level `context_management` block, and `output_config` (the effort
# control, which rides on the system message). The pinned requests.payload
# filter drops exactly those three for the managed claude models; dropping
# the rule silently would put full Claude Code sessions back on the raw 400s,
# so this fails closed.
_CLAUDE_ROUTE_MODEL_NAMES = tuple(
    sorted(
        {
            model["name"]
            for route in _VALID_CLAUDE_ROUTES
            for model in route["models"]
            if isinstance(model, dict) and isinstance(model.get("name"), str)
        }
    )
)
_APPLY_PATCH_FILTER_MODEL_NAMES = tuple(
    sorted(set(_manifest_strings("apply_patch_filter_models")))
)
EXPECTED_PAYLOAD_TOOL_RULES: dict[str, Any] = {
    "override": [
        {
            "models": [{"name": name}],
            "params": {
                'tools.#(type=="tool_search").parameters.required': [
                    "query",
                    "limit",
                ]
            },
        }
        for name in _CODEX_ROUTE_MODEL_NAMES
    ],
    "filter": [
        {
            "models": [{"name": name}],
            "params": [
                'tools.#(type=="web_search").search_content_types',
                'tools.#(type=="custom")',
            ],
        }
        for name in _CODEX_ROUTE_MODEL_NAMES
    ]
    + [
        {
            "models": [{"name": name}],
            "params": [
                "safeguards",
                "context_management",
                "messages.#(output_config)#.output_config",
            ],
        }
        for name in _CLAUDE_ROUTE_MODEL_NAMES
    ]
    + [
        {
            "models": [{"name": name, "protocol": "claude"}],
            # Payload filters run after upstream auto-cloaking, which can add
            # an older Opus fallback. Require the requested model or an error.
            "params": ["fallbacks"],
        }
        for name in sorted(EXPECTED_CLAUDE_ROUTE_ALIASES)
    ]
    + [
        {
            "models": [{"name": name}],
            "params": [
                'tools.#(type=="custom")',
                'input.#(type=="additional_tools")#.tools.#(name=="functions")#.tools.#(name=="apply_patch")#',
            ],
        }
        for name in _APPLY_PATCH_FILTER_MODEL_NAMES
    ],
}
# The claude-lane thinking-binding override and the conditional trailing-
# assistant prefill filter retired 2026-10-20: CLIProxyAPI v8.0.34 (deployed
# 2026-10-19) ships both behaviors natively -- verified in the v8.0.34 tag
# source, isClaudePreservedThinkingModel covers exactly the three lane
# families (claude_executor_request.go injects
# thinking.block_binding.prefix_mismatch_behavior=drop_block whenever signed
# thinking blocks exist, and applyBetaHeader appends the binding beta for any
# body carrying thinking.block_binding), and dropUnsupportedClaudeAssistant
# Prefill strips the trailing assistant message for every major>=5
# opus/sonnet/haiku (claude_openai-responses_request.go). The remaining
# claude filters stay: the zen-strict fields predate the opencode retirement
# and are harmless against the current callers, and the fallbacks filter
# guards the post-cloaking fallback path upstream does not own. cpa-health
# keeps both behavioral probes (binding/prefill shapes must not 400) as
# outcome-level regression guards over the native path; validate_config's
# exact-match semantics make any resurrected stale rule fail closed.
# Assigned after the literal above because the derivation reads
# ROUTE_MANIFEST, which loads later in the module than EXPECTED_CONFIG_PATHS.
EXPECTED_CONFIG_PATHS[("requests", "payload")] = EXPECTED_PAYLOAD_TOOL_RULES
EXPECTED_PROVIDER_MODEL_MAP = {
    provider["host"]: {
        model["name"]: model["alias"]
        for model in provider["models"]
        if isinstance(model.get("name"), str) and isinstance(model.get("alias"), str)
    }
    for provider in _VALID_PROVIDER_ROUTES
}
EXPECTED_PROVIDER_MODEL_RECORDS = {
    provider["host"]: {
        model["name"]: {
            "alias": model["alias"],
            **(
                {"input-modalities": model["input-modalities"]}
                if "input-modalities" in model
                else {}
            ),
            **(
                {"max-context-length": model["max-context-length"]}
                if "max-context-length" in model
                else {}
            ),
        }
        for model in provider["models"]
        if isinstance(model, dict)
        and isinstance(model.get("name"), str)
        and isinstance(model.get("alias"), str)
    }
    for provider in _VALID_PROVIDER_ROUTES
}
EXPECTED_OAUTH_SETTINGS = (
    ROUTE_MANIFEST.get("oauth_settings", {})
    if isinstance(ROUTE_MANIFEST.get("oauth_settings"), dict)
    else {}
)
EXPECTED_PROVIDER_URLS = {
    provider["host"]: (
        f"{provider.get('scheme', 'https')}://{provider['host']}"
        f"{':' + str(provider['port']) if provider.get('port') is not None else ''}"
        f"{provider['path']}"
    )
    for provider in _VALID_PROVIDER_ROUTES
    if isinstance(provider.get("path"), str)
}
FORBIDDEN_PROVIDER_KEYS = frozenset(
    {
        "proxy",
        "proxy-url",
        "http-proxy",
        "https-proxy",
        "socks-proxy",
        "headers",
        "header",
        "custom-headers",
        "transport",
        "tls",
        "insecure-skip-verify",
        "skip-tls-verify",
    }
)


def _canonical_key(key: Any) -> str:
    return str(key).strip().replace("_", "-")


def _same_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return type(actual) is bool and actual is expected
    if isinstance(expected, int):
        return type(actual) is int and actual == expected
    return actual == expected


def _provider_host(provider: Any) -> str | None:
    if not isinstance(provider, dict):
        return None
    try:
        parsed = urlparse(str(provider.get("base-url", "")))
    except ValueError:
        return None
    return parsed.hostname.lower() if parsed.hostname else None


def _provider_models(provider: Any) -> dict[str, str]:
    if not isinstance(provider, dict) or not isinstance(provider.get("models"), list):
        return {}
    models: dict[str, str] = {}
    for item in provider["models"]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        alias = item.get("alias")
        if isinstance(name, str) and isinstance(alias, str) and name and alias:
            models[name] = alias
    return models


def _provider_model_records(provider: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(provider, dict) or not isinstance(provider.get("models"), list):
        return {}
    models: dict[str, dict[str, Any]] = {}
    for item in provider["models"]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        alias = item.get("alias")
        if not isinstance(name, str) or not isinstance(alias, str) or not name or not alias:
            continue
        record = {"alias": alias}
        if "input-modalities" in item:
            record["input-modalities"] = item["input-modalities"]
        if "max-context-length" in item:
            record["max-context-length"] = item["max-context-length"]
        models[name] = record
    return models


def _provider_url(provider: Any) -> str | None:
    if not isinstance(provider, dict):
        return None
    try:
        parsed = urlparse(str(provider.get("base-url", "")))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            return None
        path = parsed.path.rstrip("/")
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return None
    return f"{parsed.scheme}://{parsed.hostname.lower()}{port}{path}"


def _provider_transport_issues(
    provider: Any, label: str, expected_headers: dict[str, str] | None = None
) -> list[str]:
    if not isinstance(provider, dict):
        return [f"{label} must be a mapping"]
    expected_headers = expected_headers or {}
    issues: list[str] = []
    if "api-key" in provider:
        issues.append(f"{label}.api-key legacy field must be absent")
    if "api-key-entries" in provider:
        issues.append(f"{label}.api-key-entries legacy field must be absent")
    for raw_key in provider:
        key = _canonical_key(raw_key)
        # A manifest-pinned static header is the only reviewed transport
        # carve-out; anything else (and any header mismatch) is refused.
        if key == "headers":
            continue
        if key in FORBIDDEN_PROVIDER_KEYS:
            issues.append(f"{label}.{key} transport override must be absent")
    if provider.get("headers", {}) != expected_headers:
        issues.append(f"{label}.headers transport override must be absent")

    entries = provider.get("keys")
    if not isinstance(entries, list) or len(entries) != 1:
        count = len(entries) if isinstance(entries, list) else "invalid"
        issues.append(f"{label}.keys must contain exactly one entry; count={count}")
    else:
        entry = entries[0]
        if not isinstance(entry, dict) or set(entry) != {"api-key"}:
            issues.append(f"{label}.keys must contain only one api-key field")
        elif not isinstance(entry.get("api-key"), str) or not entry["api-key"]:
            issues.append(f"{label}.keys[0].api-key must be non-empty")
    return issues


def _walk_nested_overrides(
    value: Any, path: tuple[str, ...], issues: list[str]
) -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = _canonical_key(raw_key)
            child_path = path + (key,)
            if path:
                if (
                    key == "request-retry"
                    and child is not None
                    and not (type(child) is int and child == 0)
                ):
                    issues.append("%s must be absent/null/0" % ".".join(child_path))
                elif key == "disable-cooling" and child not in (None, False):
                    issues.append("%s must be absent/null/false" % ".".join(child_path))
                elif key == "support-prompt-cache-key" and child not in (None, False):
                    issues.append("%s must be false or absent" % ".".join(child_path))
            _walk_nested_overrides(child, child_path, issues)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_nested_overrides(child, path + (f"[{index}]",), issues)


def validate_config(
    config: Any,
    marker_path: Path | None = None,
    antigravity_marker_path: Path | None = None,
    claude_marker_path: Path | None = None,
) -> list[str]:
    """Return semantic policy violations; an empty list means valid.

    Marker path overrides exist for isolated validation; production callers
    use the default paths beside this module.
    """

    issues: list[str] = []
    if not isinstance(config, dict):
        return ["config must be a mapping"]
    if ROUTE_MANIFEST_ERROR is not None:
        issues.append(f"unable to read provider route manifest: {ROUTE_MANIFEST_ERROR}")
    issues.extend(_MANIFEST_ISSUES)
    issues.extend(_admission_config_issues(ROUTE_MANIFEST, ADMISSION_CONFIG))
    quarantined_aliases, quarantine_issues = load_quarantine_aliases(marker_path)
    issues.extend(quarantine_issues)
    antigravity_quarantined, antigravity_issues = load_quarantine_aliases(
        antigravity_marker_path, provider="antigravity"
    )
    issues.extend(antigravity_issues)
    claude_quarantined, claude_issues = load_quarantine_aliases(
        claude_marker_path, provider="claude"
    )
    issues.extend(claude_issues)

    for path, expected in EXPECTED_CONFIG_PATHS.items():
        if path == ("oauth", "excluded-models", "claude"):
            # The same checked baseline applies when no marker exists. A
            # reviewed Claude quarantine adds exactly the provider-wide
            # wildcard recorded in the marker, so -Apply preserves the stop.
            continue
        node: Any = config
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if not _same_value(node, expected):
            issues.append(f"{'.'.join(path)}={node!r}; expected {expected!r}")

    routing = config.get("routing")
    if not isinstance(routing, dict):
        issues.append("routing must be a mapping")
    else:
        for key, expected in EXPECTED_ROUTING.items():
            actual = routing.get(key)
            if not _same_value(actual, expected):
                issues.append(f"routing.{key}={actual!r}; expected {expected!r}")

    quota = config.get("quota-exceeded")
    if not isinstance(quota, dict):
        issues.append("quota-exceeded must be a mapping")
    else:
        for key, expected in EXPECTED_QUOTA.items():
            actual = quota.get(key)
            if not _same_value(actual, expected):
                issues.append(f"quota-exceeded.{key}={actual!r}; expected {expected!r}")

    upstream = config.get("upstream")
    codex = upstream.get("codex") if isinstance(upstream, dict) else None
    if not isinstance(codex, dict):
        issues.append("upstream.codex must be a mapping")
    else:
        for key, expected in EXPECTED_CODEX.items():
            actual = codex.get(key)
            if not _same_value(actual, expected):
                issues.append(f"upstream.codex.{key}={actual!r}; expected {expected!r}")

    oauth_block = config.get("oauth")
    oauth_exclusions = (
        oauth_block.get("excluded-models") if isinstance(oauth_block, dict) else None
    )
    oauth_settings = (
        oauth_block.get("settings") if isinstance(oauth_block, dict) else None
    )
    if not isinstance(oauth_settings, dict):
        issues.append("oauth.settings must be a mapping")
    else:
        for channel, expected_settings in EXPECTED_OAUTH_SETTINGS.items():
            actual_settings = oauth_settings.get(channel)
            if not isinstance(actual_settings, list):
                issues.append(
                    f"oauth.settings.{channel} must be a list"
                )
                continue
            normalized_actual = []
            for entry in actual_settings:
                if not isinstance(entry, dict):
                    normalized_actual.append(entry)
                    continue
                normalized_actual.append(
                    {
                        key: value
                        for key, value in entry.items()
                        if key in {"name", "alias", "max-context-length"}
                        and value not in (None, "")
                    }
                )
            normalized_expected = [
                {
                    key: value
                    for key, value in entry.items()
                    if key in {"name", "alias", "max-context-length"}
                    and value not in (None, "")
                }
                for entry in expected_settings
                if isinstance(entry, dict)
            ]
            if normalized_actual != normalized_expected:
                issues.append(
                    f"oauth.settings.{channel} context-window drift"
                )
    if not isinstance(oauth_exclusions, dict):
        issues.append("oauth.excluded-models must be a mapping")
    else:
        claude_exclusions = oauth_exclusions.get("claude")
        if not isinstance(claude_exclusions, list) or not all(
            isinstance(pattern, str) and pattern.strip()
            for pattern in claude_exclusions
        ):
            issues.append(
                "oauth.excluded-models.claude must be a list of non-empty strings"
            )
        else:
            actual_claude = [pattern.strip().lower() for pattern in claude_exclusions]
            expected_claude = [
                pattern.strip().lower()
                for pattern in EXPECTED_CONFIG_PATHS[
                    ("oauth", "excluded-models", "claude")
                ]
            ]
            if claude_quarantined and not claude_issues:
                expected_claude.append("claude-*")
            if len(actual_claude) != len(set(actual_claude)) or set(
                actual_claude
            ) != set(expected_claude):
                issues.append(
                    "oauth.excluded-models.claude exclusion drift or unmarked quarantine"
                )
            blocked_claude_routes = {
                alias
                for alias in EXPECTED_CLAUDE_ROUTE_ALIASES
                if any(fnmatchcase(alias.lower(), pattern) for pattern in actual_claude)
            }
            if claude_quarantined and not claude_issues:
                missing_blocks = sorted(claude_quarantined - blocked_claude_routes)
                if missing_blocks:
                    issues.append(
                        "Claude OAuth quarantine marker is active but these aliases are "
                        f"still served: {missing_blocks!r}"
                    )
            elif blocked_claude_routes:
                issues.append(
                    "oauth.excluded-models.claude blocks declared OAuth routes "
                    f"without a quarantine marker: {sorted(blocked_claude_routes)!r}"
                )

        antigravity_exclusions = oauth_exclusions.get("antigravity", [])
        if not isinstance(antigravity_exclusions, list) or not all(
            isinstance(pattern, str) and pattern.strip()
            for pattern in antigravity_exclusions
        ):
            issues.append(
                "oauth.excluded-models.antigravity must be a list of non-empty strings"
            )
        else:
            antigravity_patterns = {
                pattern.strip().lower() for pattern in antigravity_exclusions
            }
            expected_antigravity = {
                pattern.lower() for pattern in EXPECTED_ANTIGRAVITY_EXCLUSIONS
            }
            if not antigravity_issues:
                expected_antigravity.update(antigravity_quarantined)
            if antigravity_patterns != expected_antigravity:
                issues.append(
                    "oauth.excluded-models.antigravity exclusion drift or unmarked quarantine"
                )
        codex_exclusions = oauth_exclusions.get("codex")
        if not isinstance(codex_exclusions, list) or not all(
            isinstance(pattern, str) and pattern.strip() for pattern in codex_exclusions
        ):
            issues.append(
                "oauth-excluded-models.codex must be a list of non-empty strings"
            )
        else:
            patterns = [pattern.strip().lower() for pattern in codex_exclusions]
            missing_exclusions = sorted(EXPECTED_CODEX_OAUTH_EXCLUSIONS - set(patterns))
            if missing_exclusions:
                issues.append(
                    "oauth-excluded-models.codex must exclude "
                    f"{missing_exclusions!r} to keep those routes pinned to ai.input.im"
                )
            blocked_oauth_routes = sorted(
                alias
                for alias in EXPECTED_OAUTH_ROUTE_ALIASES
                if any(fnmatchcase(alias.lower(), pattern) for pattern in patterns)
            )
            blocked_lower = {alias.lower() for alias in blocked_oauth_routes}
            if quarantined_aliases and not quarantine_issues:
                # An active quarantine is the only state in which removing a
                # live OAuth route from the catalog is intended. It must match
                # the marker exactly: an alias the marker does not name is an
                # unreviewed lane change, and an alias the marker names but the
                # config still serves means the quarantine did not take effect.
                missing_blocks = sorted(quarantined_aliases - blocked_lower)
                if missing_blocks:
                    issues.append(
                        "OAuth quarantine marker is active but these aliases are "
                        f"still served: {missing_blocks!r}"
                    )
                unauthorized = sorted(blocked_lower - quarantined_aliases)
                if unauthorized:
                    issues.append(
                        "oauth-excluded-models.codex blocks OAuth routes outside "
                        f"the quarantine marker: {unauthorized!r}"
                    )
            elif blocked_oauth_routes:
                issues.append(
                    "oauth-excluded-models.codex must leave configured OAuth "
                    f"routes available; blocked aliases={blocked_oauth_routes!r}"
                )

    api_keys_block = config.get("api-keys")
    if not isinstance(api_keys_block, dict):
        issues.append("api-keys must be a mapping in the v8 file layout")
        api_keys_block = {}
    codex_api_keys = api_keys_block.get("codex", [])
    if codex_api_keys is not None and not isinstance(codex_api_keys, list):
        issues.append("api-keys.codex must be a list when present")
    elif isinstance(codex_api_keys, list):
        managed_seen: set[str] = set()
        for index, provider in enumerate(codex_api_keys):
            label = f"api-keys.codex[{index}]"
            if not isinstance(provider, dict):
                issues.append(f"{label} must be a mapping")
                continue
            route_base = str(provider.get("base-url", "")).rstrip("/")
            route = EXPECTED_CODEX_ROUTE_GROUPS.get(route_base)
            if route is not None:
                # Managed codex-family route: everything is pinned, mirroring
                # the openai-compatibility per-slot checks.
                managed_seen.add(route_base)
                entries = provider.get("keys")
                if (
                    not isinstance(entries, list)
                    or len(entries) != 1
                    or not isinstance(entries[0], dict)
                    or set(entries[0]) != {"api-key"}
                    or not isinstance(entries[0].get("api-key"), str)
                    or not entries[0]["api-key"]
                ):
                    issues.append(
                        f"{label}.keys must contain exactly one non-empty api-key"
                    )
                expected_headers = route.get("headers") or {}
                if provider.get("headers", {}) != expected_headers:
                    issues.append(f"{label}.headers transport override must be absent")
                actual_models = _provider_models(provider)
                expected_models = {
                    model["name"]: model["alias"]
                    for model in route["models"]
                    if isinstance(model, dict)
                    and isinstance(model.get("name"), str)
                    and isinstance(model.get("alias"), str)
                }
                if actual_models != expected_models:
                    issues.append(
                        f"{label}.models={actual_models!r}; expected {expected_models!r}"
                    )
            exclusions = provider.get("excluded-models", [])
            if not isinstance(exclusions, list) or not all(
                isinstance(model, str) and model.strip() for model in exclusions
            ):
                issues.append(f"{label}.excluded-models must be a list of model names")
                continue
            actual_exclusions = {model.strip().lower() for model in exclusions}
            missing_routes = sorted(
                {model.lower() for model in EXCLUSIVE_CODEX_API_KEY_ROUTES}
                - actual_exclusions
            )
            if missing_routes:
                issues.append(
                    f"{label}.excluded-models must exclude {missing_routes!r} "
                    "to prevent bare-route overlap"
                )
        for route_base in sorted(set(EXPECTED_CODEX_ROUTE_GROUPS) - managed_seen):
            issues.append(
                f"api-keys.codex is missing the managed route group {route_base!r}"
            )

    claude_api_keys = api_keys_block.get("claude", [])
    if claude_api_keys is not None and not isinstance(claude_api_keys, list):
        issues.append("api-keys.claude must be a list when present")
    elif isinstance(claude_api_keys, list):
        managed_claude_seen: set[str] = set()
        for index, provider in enumerate(claude_api_keys):
            label = f"api-keys.claude[{index}]"
            if not isinstance(provider, dict):
                issues.append(f"{label} must be a mapping")
                continue
            route_base = str(provider.get("base-url", "")).rstrip("/")
            route = EXPECTED_CLAUDE_ROUTE_GROUPS.get(route_base)
            if route is None:
                continue
            # Managed claude-family route: everything is pinned, mirroring the
            # openai-compatibility per-slot checks.
            managed_claude_seen.add(route_base)
            entries = provider.get("keys")
            if (
                not isinstance(entries, list)
                or len(entries) != 1
                or not isinstance(entries[0], dict)
                or set(entries[0]) != {"api-key"}
                or not isinstance(entries[0].get("api-key"), str)
                or not entries[0]["api-key"]
            ):
                issues.append(
                    f"{label}.keys must contain exactly one non-empty api-key"
                )
                group_api_key = ""
            else:
                group_api_key = entries[0]["api-key"]
            # The manifest pins the header names and their non-secret values;
            # the placeholder resolves to this group's own credential, so the
            # comparison never has to hold a second copy of the key.
            expected_headers = {
                header_name: (
                    group_api_key
                    if header_value == SLOT_API_KEY_HEADER_TOKEN and group_api_key
                    else header_value
                )
                for header_name, header_value in (route.get("headers") or {}).items()
            }
            if provider.get("headers", {}) != expected_headers:
                issues.append(
                    f"{label}.headers must pin the declared claude route headers"
                )
            actual_models = _provider_models(provider)
            expected_models = {
                model["name"]: model["alias"]
                for model in route["models"]
                if isinstance(model, dict)
                and isinstance(model.get("name"), str)
                and isinstance(model.get("alias"), str)
            }
            if actual_models != expected_models:
                issues.append(
                    f"{label}.models={actual_models!r}; expected {expected_models!r}"
                )
        for route_base in sorted(
            set(EXPECTED_CLAUDE_ROUTE_GROUPS) - managed_claude_seen
        ):
            issues.append(
                f"api-keys.claude is missing the managed route group {route_base!r}"
            )

    compatibility = api_keys_block.get("openai-compatibility")
    if not isinstance(compatibility, list):
        issues.append("api-keys.openai-compatibility must be a list")
    else:
        by_host: dict[str, list[dict[str, Any]]] = {}
        aliases: dict[str, str] = {}
        for item in compatibility:
            host = _provider_host(item)
            if host:
                by_host.setdefault(host, []).append(item)
                for model in item.get("models", []) if isinstance(item, dict) else []:
                    if isinstance(model, dict) and isinstance(model.get("alias"), str):
                        alias = model["alias"].lower()
                        previous_host = aliases.get(alias)
                        if previous_host is not None and previous_host != host:
                            issues.append(
                                f"client alias {model['alias']!r} is shared by "
                                f"{previous_host} and {host}"
                            )
                        aliases[alias] = host
            if host in LEGACY_CHANNEL_HOSTS or (
                isinstance(item, dict) and item.get("name") == "relay-8003"
            ):
                issues.append(
                    f"retired provider {host or 'relay-8003'} must be removed"
                )
        unexpected_hosts = sorted(set(by_host) - set(EXPECTED_PROVIDER_MODELS))
        if unexpected_hosts:
            issues.append(
                f"unexpected openai-compatibility providers: {unexpected_hosts!r}"
            )
        for host in EXPECTED_PROVIDER_MODELS:
            entries = by_host.get(host, [])
            if len(entries) != 1:
                issues.append(
                    f"openai-compatibility must contain exactly one {host} provider"
                )
                continue
            provider = entries[0]
            expected_route = EXPECTED_PROVIDER_ROUTES[host]
            if provider.get("name") != expected_route.get("name"):
                issues.append(
                    f"openai-compatibility.{host}.name must be "
                    f"{expected_route.get('name')!r}"
                )
            if provider.get("disabled") is True:
                issues.append(
                    f"openai-compatibility.{host}.disabled must be absent or false"
                )
            label = f"openai-compatibility.{host}"
            issues.extend(
                _provider_transport_issues(
                    provider, label, expected_route.get("headers") or {}
                )
            )
            actual_models = _provider_models(provider)
            expected_map = EXPECTED_PROVIDER_MODEL_MAP[host]
            if actual_models != expected_map:
                issues.append(
                    f"{label}.models={actual_models!r}; expected {expected_map!r}"
                )
            elif _provider_model_records(provider) != EXPECTED_PROVIDER_MODEL_RECORDS[host]:
                issues.append(
                    f"{label}.models max-context-length/input-modalities drift"
                )
            expected_url = EXPECTED_PROVIDER_URLS.get(host)
            if expected_url is not None and _provider_url(provider) != expected_url:
                issues.append(
                    f"{label}.base-url must be exact {expected_url!r} "
                    "(no userinfo/query/fragment; HTTP is restricted to the authorized slot 3 relay)"
                )

    _walk_nested_overrides(config, (), issues)
    return issues


def validate_file(path: str | Path, marker_path: Path | None = None) -> list[str]:
    try:
        config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - exercised by remote diagnostics
        return [f"unable to read config: {type(exc).__name__}"]
    return validate_config(config, marker_path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: cpa_policy.py CONFIG", file=sys.stderr)
        raise SystemExit(2)
    violations = validate_file(sys.argv[1])
    if violations:
        for violation in violations:
            print(f"POLICY_FAILED {violation}")
        raise SystemExit(1)
    print("POLICY_OK")

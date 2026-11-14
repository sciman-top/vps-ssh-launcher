#!/usr/bin/env python3
"""Read-only per-model availability and per-channel health view for CPA.

One recurring operator question needs evidence instead of log archaeology:
**why is a model unavailable right now?** The answer is spread across four
independent surfaces that no existing panel joins:

* the admission broker's ``/healthz`` (loopback 8318): per-lane breaker state,
  per-model cooldowns, server Retry-After, manual holds;
* the CPA core management API ``/v0/management/auth-files`` (loopback 8317):
  per-credential status, quota exhaustion, per-model cooldown snapshots
  (``CooldownSnapshotForAuth`` ships in production core v8.0.61). This
  surface needs the *plaintext* management key: the config stores only a
  bcrypt hash, so provision it once at ``/root/.cpa-management-key``
  (chmod 600, read on the host only, never printed or transmitted). Without
  it the channel view degrades and the model verdicts continue from the
  other three surfaces marked ``UNKNOWN``;
* the durable lane risk files (``/var/lib/cpa-admission/<lane>-risk.json``):
  the persisted ``not_before`` anchors that survive admission restarts;
* the shared route manifest (``cpa_provider_routes.json``): the model-to-lane
  contract that decides which breaker state applies to which alias.

Metrics boundaries (deliberate, not gaps to "fix" later):

* **Cache hit ratio and first-token latency per model** come from the
  admission telemetry snapshot embedded in ``/healthz`` (1h rolling window,
  completed generations only, provider-reported ratio). They join on model
  name; series the manifest never named stay listed under
  ``telemetry_only_models`` so nothing is silently dropped.
* **Token totals, TPS and cost are NOT reported.** The only token-bearing
  management surface (``/observability/usage/queue``) is a destructive POP --
  a status readout must never consume queued events -- and the per-key usage
  endpoint carries success/failed counts only. Subscription OAuth lanes are
  not metered per token and plugin credits are pool-level, so any per-model
  cost number would be invented. Hours-scale throughput/latency aggregates
  (including the bounded ``output_tokens_per_s`` estimate) remain the job of
  ``cpa_natural_traffic_audit.py``, which reads the same journal without
  consuming anything.

This tool joins them into one verdict per model and one row per channel. It is
a **state view, not an active probe**: it never sends a generation request, so
``OK`` means "no block observed", not "proven reachable" -- end-to-end proof
stays with ``cpa-health.py`` readiness/generation modes.

Design rules:

* **Read-only everywhere.** No file is written locally or on the host; remote
  execution streams this file itself into a ``mktemp`` that is removed on exit
  (the ``cpa_error_dump_forensics.py`` transport precedent), so nothing is
  projected or persisted on the VPS and no ``-Apply`` machinery is touched.
  The payload travels zlib-compressed because Windows caps the whole
  ``CreateProcess`` command line at 32767 chars and the raw base64 no longer
  fits; a local budget guard fails loudly before that limit is hit again.
* **Fail-closed parsing.** Malformed surfaces degrade to ``UNKNOWN`` verdicts
  and recorded surface errors; they never crash the view or fabricate an OK.
* **Redaction-first.** The management secret, access keys and credential
  metadata never enter the document; free-text fields are masked and bounded.
* **No new wall-clock judgment calls.** Cooldowns are reported with the
  remaining seconds the surfaces themselves advertise.

Verdict vocabulary (precedence order): ``HOLD`` (manual hold), ``STATE_WRITE_
FAILED``, ``COOLING_LANE`` (lane breaker open), ``COOLING_MODEL`` (model-
scoped cooldown), ``PERSISTED_COOLDOWN`` (durable risk file ``not_before``
in the future; outranks transient credential reads), ``QUOTA`` (credential-
wide quota cooldown window), ``CRED_ERROR`` (every matching credential is
error/unavailable or under a non-quota credential cooldown), ``CRED_DEGRADED``
(some matching credentials failed), ``OK`` (no block observed),
``UNOBSERVED`` (direct API-key upstream with no stateful surface),
``UNKNOWN`` (required surface missing or invalid).

Usage (from the repo root; targets the default ``target.json`` profile)::

    python scripts/cpa_status.py                 # text view
    python scripts/cpa_status.py --json          # machine view
    python scripts/cpa_status.py --profile bwg   # explicit profile
    python scripts/cpa_status.py --serve         # local status page on
                                                 # http://127.0.0.1:8642/
                                                 # (browser, auto-refresh;
                                                 # /json for the raw document)

Exit codes: ``0`` a document was produced (surface errors are embedded),
``2`` usage, transport or unparseable-output failure.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Sequence
from urllib.parse import urlsplit

CORE_PORT = 8317
ADMISSION_PORT = 8318
CONFIG_PATH = "/opt/cliproxyapi/config.yaml"
ADMISSION_CONFIG_ENV = "VPS_SSH_LAUNCHER_ADMISSION_CONFIG"
ADMISSION_CONFIG_PATH = "/opt/cliproxyapi/cpa-admission.json"
RISK_STATE_DIR = "/var/lib/cpa-admission"
# Operator-provisioned plaintext management key. The config stores only a
# bcrypt hash (one-way), so this file is the only machine-readable source;
# it never leaves the host and must be chmod 600.
MANAGEMENT_KEY_FILE = "/root/.cpa-management-key"
# The four durable state files admission is launched with (bootstrap argv);
# the lane mapping is by filename prefix (antigravity-risk.json ->
# antigravity-oauth lane).
RISK_FILE_PREFIXES = ("antigravity", "claude", "chatgpt", "workbuddy")

LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Verdicts, ordered by precedence (first match wins).
VERDICT_HOLD = "HOLD"
VERDICT_STATE_WRITE_FAILED = "STATE_WRITE_FAILED"
VERDICT_COOLING_LANE = "COOLING_LANE"
VERDICT_COOLING_MODEL = "COOLING_MODEL"
VERDICT_PERSISTED_COOLDOWN = "PERSISTED_COOLDOWN"
VERDICT_QUOTA = "QUOTA"
VERDICT_CRED_ERROR = "CRED_ERROR"
VERDICT_CRED_DEGRADED = "CRED_DEGRADED"
VERDICT_OK = "OK"
VERDICT_UNOBSERVED = "UNOBSERVED"
VERDICT_UNKNOWN = "UNKNOWN"

_KEY_LIKE = re.compile(r"(sk-[A-Za-z0-9_-]{6,}|Bearer\s+\S+)", re.I)
_TEXT_LIMIT = 160

# Verdict pick order across layered candidates: admission state outranks core
# credential state; persisted risk anchors only win when live surfaces died.
_VERDICT_PRECEDENCE = (
    VERDICT_HOLD,
    VERDICT_STATE_WRITE_FAILED,
    VERDICT_COOLING_LANE,
    VERDICT_COOLING_MODEL,
    VERDICT_PERSISTED_COOLDOWN,
    VERDICT_QUOTA,
    VERDICT_CRED_ERROR,
    VERDICT_CRED_DEGRADED,
    VERDICT_UNKNOWN,
)


# ---------------------------------------------------------------------------
# redaction helpers


def mask_text(value: Any, limit: int = _TEXT_LIMIT) -> str:
    """Mask key-like material in free text and bound its length."""
    if not isinstance(value, str):
        return ""
    masked = _KEY_LIKE.sub("***", value)
    return masked[:limit]


def mask_email(value: Any) -> str:
    """Keep a hint of local part and domain only."""
    if not isinstance(value, str) or "@" not in value:
        return ""
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain[:1]}***"


def iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# surface collection (remote side only)


def _loopback_get_json(url: str, headers: dict[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with LOOPBACK_OPENER.open(request, timeout=timeout) as response:
        return json.load(response)


def _load_yaml_config(path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        import yaml  # the remote host ships PyYAML for cpa-health.py already
    except ImportError:  # pragma: no cover - remote-only path
        return None, "pyyaml unavailable on host"
    try:
        with open(path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
    except OSError as exc:
        return None, f"config read failed: {exc.__class__.__name__}"
    except ValueError as exc:
        return None, f"config parse failed: {exc}"
    if not isinstance(config, dict):
        return None, "config is not a mapping"
    return config, None


def _management_secret(config: dict[str, Any]) -> tuple[str | None, str | None]:
    """The plaintext management key, if the config carries one.

    ``management.secret-key`` normally stores a **bcrypt hash** (the core
    verifies with ``bcrypt.CompareHashAndPassword``), which cannot be used as
    a key. A plaintext value is only usable when it is clearly not a hash.
    """
    management = config.get("management") if isinstance(config, dict) else None
    if not isinstance(management, dict):
        return None, None
    secret = management.get("secret-key")
    if not (isinstance(secret, str) and secret.strip()):
        return None, None
    if secret.strip().startswith("$2"):
        return None, "management.secret-key is a bcrypt hash, not the key"
    return secret.strip(), None


def _management_key_file() -> tuple[str | None, str | None]:
    """Read the operator-provisioned key file (must be chmod 600)."""
    path = pathlib.Path(MANAGEMENT_KEY_FILE)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"key file unreadable: {exc.__class__.__name__}"
    if stat.st_mode & 0o777 != 0o600:
        return None, f"{MANAGEMENT_KEY_FILE} must be chmod 600"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as exc:
        return None, f"key file unreadable: {exc.__class__.__name__}"
    if not value:
        return None, f"{MANAGEMENT_KEY_FILE} is empty"
    return value, None


def _manifest_path() -> pathlib.Path:
    configured = os.environ.get(ADMISSION_CONFIG_ENV, ADMISSION_CONFIG_PATH)
    return pathlib.Path(configured).with_name("cpa_provider_routes.json")


def _read_json_file(path: pathlib.Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"missing: {path}"
    except (OSError, ValueError) as exc:
        return None, f"unreadable: {path}: {exc.__class__.__name__}"


def collect_surfaces() -> dict[str, Any]:
    """Gather every stateful surface; failures degrade to recorded errors."""
    errors: list[str] = []

    config, config_error = _load_yaml_config(CONFIG_PATH)
    if config_error:
        errors.append(f"config: {config_error}")

    healthz: Any = None
    try:
        healthz = _loopback_get_json(
            f"http://127.0.0.1:{ADMISSION_PORT}/healthz", {}, 5.0
        )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        errors.append(f"healthz: {exc.__class__.__name__}")

    auth_files: Any = None
    auth_note: str | None = None
    secret, secret_note = _management_key_file()
    if secret is None and config:
        secret, secret_note = _management_secret(config)
    if secret is None:
        auth_note = (
            "credential state unobservable: management key unavailable "
            f"({secret_note or 'no key source'}); write the plaintext key to "
            f"{MANAGEMENT_KEY_FILE} (chmod 600) to enable the channels view"
        )
        errors.append(f"auth-files: {auth_note}")
    else:
        try:
            auth_files = _loopback_get_json(
                f"http://127.0.0.1:{CORE_PORT}/v0/management/auth-files",
                {"X-Management-Key": secret},
                5.0,
            )
        except urllib.error.HTTPError as exc:
            auth_note = f"core management API rejected the key (HTTP {exc.code})"
            errors.append(f"auth-files: {auth_note}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            auth_note = f"auth-files unreachable: {exc.__class__.__name__}"
            errors.append(f"auth-files: {auth_note}")

    manifest, manifest_error = _read_json_file(_manifest_path())
    if manifest_error:
        errors.append(f"manifest: {manifest_error}")

    risk: dict[str, Any] = {}
    for prefix in RISK_FILE_PREFIXES:
        payload, risk_error = _read_json_file(
            pathlib.Path(RISK_STATE_DIR) / f"{prefix}-risk.json"
        )
        if risk_error:
            # A missing risk file is the normal "never cooled" state.
            if "missing" not in (risk_error or ""):
                errors.append(f"risk[{prefix}]: {risk_error}")
            continue
        risk[prefix] = payload

    return {
        "healthz": healthz,
        "auth_files": auth_files,
        "auth_note": auth_note,
        "manifest": manifest,
        "risk": risk,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# synthesis


def _entry_stem(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    return name[: -len(".json")] if name.endswith(".json") else name


def _quota_observation(entry: dict[str, Any]) -> dict[str, Any]:
    """Display-only quota watermarks.

    ``entry["quota"]`` carries the provider's *observed* signals snapshot
    (``observed_at`` + ``signals``), not an active-block verdict; the block
    verdicts come from the cooldown views and status fields instead.
    """
    quota = entry.get("quota")
    if not isinstance(quota, dict):
        return {}
    signals = quota.get("signals")
    return {
        "observed_at": quota.get("observed_at"),
        "signals": len(signals) if isinstance(signals, dict) else 0,
    }


def _cooldown_views(entry: dict[str, Any]) -> list[dict[str, Any]]:
    views = entry.get("cooldowns")
    if not isinstance(views, list):
        return []
    return [view for view in views if isinstance(view, dict)]


def _oauth_settings_index(manifest: dict[str, Any]) -> dict[str, str]:
    """model name -> provider family, from the manifest's oauth_settings."""
    index: dict[str, str] = {}
    settings = manifest.get("oauth_settings") if isinstance(manifest, dict) else None
    if isinstance(settings, dict):
        for provider, models in settings.items():
            if not isinstance(models, list):
                continue
            for model in models:
                name = model.get("name") if isinstance(model, dict) else model
                if isinstance(name, str) and name:
                    index[name] = str(provider)
    return index


def _match_route_credentials(
    kind: str,
    route_name: str,
    provider: str,
    files: list[dict[str, Any]],
    settings_index: dict[str, str] | None = None,
    route_models: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Lenient credential match; zero matches degrade the row, never crash.

    OAuth credentials are named after the account (e.g.
    ``codex-<hash>-<email>-plus.json``), not the route, so name matching is
    only the first strategy: the manifest's oauth_settings provides the real
    model->provider-family linkage and is used as the fallback.
    """
    if kind == "oauth":
        target = route_name
        matched = [
            entry
            for entry in files
            if _entry_stem(entry.get("name")) == target
            or target in str(entry.get("name", ""))
        ]
        if matched:
            return matched
        family: str | None = None
        if settings_index and route_models:
            families = {
                settings_index[model]
                for model in route_models
                if model in settings_index
            }
            if len(families) == 1:
                family = families.pop()
        if family:
            return [
                entry
                for entry in files
                if str(entry.get("provider", "")).lower() == family.lower()
            ]
        return []
    return [
        entry
        for entry in files
        if str(entry.get("provider", "")).lower() == provider.lower()
    ]


def _lane_prefix_for(lane: str) -> str | None:
    for prefix in RISK_FILE_PREFIXES:
        if lane == prefix or lane.startswith(f"{prefix}-"):
            return prefix
    return None


def _admission_verdict(
    lane_state: dict[str, Any], model: str
) -> tuple[str | None, str]:
    if lane_state.get("hold_reason"):
        return VERDICT_HOLD, f"hold_reason={lane_state['hold_reason']}"
    if lane_state.get("state_write_failed") is True:
        return VERDICT_STATE_WRITE_FAILED, "lane state persistence failing"
    server_retry = lane_state.get("server_retry_after_remaining")
    if lane_state.get("cooldown_active") is True:
        remaining = lane_state.get("cooldown_remaining")
        scope = lane_state.get("cooldown_scope", "lane")
        detail = f"scope={scope} remaining={remaining}s"
        if isinstance(server_retry, int) and server_retry > 0:
            detail += f" server_retry_after={server_retry}s"
        if scope == "model":
            per_model = lane_state.get("model_cooldowns")
            if isinstance(per_model, dict) and isinstance(per_model.get(model), int):
                return VERDICT_COOLING_MODEL, (
                    f"remaining={per_model[model]}s {detail}"
                )
            # A model-scoped window parks only the named model (the admission
            # design: siblings keep riding the lane), so an unnamed model has
            # no active block.
            return None, ""
        return VERDICT_COOLING_LANE, detail
    if isinstance(server_retry, int) and server_retry > 0:
        return VERDICT_COOLING_LANE, f"server_retry_after={server_retry}s"
    per_model = lane_state.get("model_cooldowns")
    if isinstance(per_model, dict) and isinstance(per_model.get(model), int):
        return VERDICT_COOLING_MODEL, f"remaining={per_model[model]}s"
    return None, ""


def _core_verdict(
    credentials: list[dict[str, Any]], model: str
) -> tuple[str | None, str]:
    if not credentials:
        return None, ""
    # CooldownSnapshotForAuth emits only *unexpired* timers, so any view here
    # is an active block. Model-scoped views name the blocked model directly.
    for view in _cooldown_views_for_model(credentials, model):
        remaining = view.get("remaining_seconds")
        reason = mask_text(view.get("reason"), 60)
        if isinstance(remaining, int) and remaining > 0:
            retry_at = view.get("retry_at")
            return VERDICT_COOLING_MODEL, (
                f"credential cooldown remaining={remaining}s reason={reason} "
                f"retry_at={retry_at}"
            )
    # Credential-wide views block every model the credential serves. Quota
    # reasons read as QUOTA; every other reason is a credential outage.
    credential_views = [
        view
        for entry in credentials
        for view in _cooldown_views(entry)
        if str(view.get("scope", "")) == "credential"
        and isinstance(view.get("remaining_seconds"), int)
        and int(view.get("remaining_seconds", 0)) > 0  # type: ignore[arg-type]
    ]
    if credential_views:
        quota_views = [
            view
            for view in credential_views
            if str(view.get("reason", "")) in ("credential_quota", "quota")
        ]
        if quota_views:
            longest = max(
                quota_views, key=lambda view: int(view.get("remaining_seconds", 0))
            )
            return VERDICT_QUOTA, (
                f"{len(quota_views)}/{len(credentials)} credential(s) exhausted, "
                f"remaining={longest.get('remaining_seconds')}s "
                f"retry_at={longest.get('retry_at')}"
            )
        longest = max(
            credential_views, key=lambda view: int(view.get("remaining_seconds", 0))
        )
        return VERDICT_CRED_ERROR, (
            f"credential cooldown remaining={longest.get('remaining_seconds')}s "
            f"reason={mask_text(longest.get('reason'), 60)} "
            f"retry_at={longest.get('retry_at')}"
        )
    errored = 0
    for entry in credentials:
        status = str(entry.get("status", "")).lower()
        if status == "error" or entry.get("unavailable") is True:
            errored += 1
    if errored and errored == len(credentials):
        messages = [
            mask_text(entry.get("status_message"), 60) or "no message"
            for entry in credentials
        ]
        return VERDICT_CRED_ERROR, f"{errored}/{len(credentials)} error: " + "; ".join(
            sorted(set(messages))
        )
    if errored:
        return (
            VERDICT_CRED_DEGRADED,
            f"{errored}/{len(credentials)} credential(s) error",
        )
    return None, ""


def _cooldown_views_for_model(
    credentials: list[dict[str, Any]], model: str
) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for entry in credentials:
        for view in _cooldown_views(entry):
            if str(view.get("model_key", "")) == model:
                views.append(view)
    return views


def _risk_verdict(
    risk: dict[str, Any], lane: str | None, now: float
) -> tuple[str | None, str]:
    if lane is None:
        return None, ""
    prefix = _lane_prefix_for(lane)
    if prefix is None:
        return None, ""
    payload = risk.get(prefix)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None, ""
    not_before = payload.get("not_before")
    if not isinstance(not_before, (int, float)) or float(not_before) <= now:
        return None, ""
    hold = payload.get("hold_reason")
    detail = f"until={iso_utc(float(not_before))}"
    if hold:
        detail += f" hold_reason={hold}"
    return VERDICT_PERSISTED_COOLDOWN, detail


def synthesize(surfaces: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Join the four surfaces into per-model and per-channel documents."""
    now = time.time() if now is None else now
    errors = [str(item) for item in surfaces.get("errors", [])]

    healthz = surfaces.get("healthz")
    lanes: dict[str, dict[str, Any]] = {}
    if isinstance(healthz, dict) and isinstance(healthz.get("lanes"), dict):
        for name, lane in healthz["lanes"].items():
            if isinstance(lane, dict) and isinstance(lane.get("state"), dict):
                lanes[str(name)] = lane

    telemetry_models: dict[str, dict[str, Any]] = {}
    telemetry_window: int | None = None
    if isinstance(healthz, dict) and isinstance(healthz.get("telemetry"), dict):
        telemetry = healthz["telemetry"]
        if isinstance(telemetry.get("models"), dict):
            telemetry_models = {
                str(name): entry
                for name, entry in telemetry["models"].items()
                if isinstance(entry, dict)
            }
        if isinstance(telemetry.get("window_seconds"), int):
            telemetry_window = telemetry["window_seconds"]

    auth_payload = surfaces.get("auth_files")
    files: list[dict[str, Any]] = []
    if isinstance(auth_payload, dict) and isinstance(auth_payload.get("files"), list):
        files = [entry for entry in auth_payload["files"] if isinstance(entry, dict)]

    manifest = surfaces.get("manifest")
    routes: list[dict[str, Any]] = []
    if isinstance(manifest, dict):
        for kind, key in (
            ("oauth", "oauth_routes"),
            ("plugin", "plugin_routes"),
            # Managed key routes are API-key upstreams like the providers: no
            # auth-file credential matching exists for them, so they share the
            # direct verdict path instead of the oauth/plugin one.
            ("direct", "codex_api_key_routes"),
            ("direct", "claude_api_key_routes"),
            ("direct", "providers"),
        ):
            section = manifest.get(key)
            if not isinstance(section, list):
                continue
            for route in section:
                if isinstance(route, dict):
                    routes.append({"kind": kind, "route": route})

    risk = surfaces.get("risk")
    risk_index = risk if isinstance(risk, dict) else {}

    models: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    row_keys: set[tuple[str, str | None]] = set()
    auth_note = str(surfaces.get("auth_note") or "")
    settings_index = (
        _oauth_settings_index(manifest) if isinstance(manifest, dict) else {}
    )
    for record in routes:
        kind = str(record["kind"])
        route = record["route"]
        lane = route.get("admission_lane")
        lane = str(lane) if isinstance(lane, str) and lane else None
        route_name = str(route.get("name", route.get("provider", "")))
        provider = str(route.get("provider", ""))
        model_list = route.get("models")
        if not isinstance(model_list, list):
            continue
        route_aliases: list[str] = []
        for raw_model in model_list:
            alias = (
                raw_model
                if isinstance(raw_model, str)
                else (
                    raw_model.get("alias") or raw_model.get("name")
                    if isinstance(raw_model, dict)
                    and isinstance(raw_model.get("alias") or raw_model.get("name"), str)
                    else None
                )
            )
            if alias:
                route_aliases.append(alias)
        for alias in route_aliases:
            seen_models.add(alias)
            # Dual-realm plugin routes legitimately list the same alias on one
            # lane; the row would be identical, so keep the first only.
            row_key = (alias.lower(), lane)
            if row_key in row_keys:
                continue
            row_keys.add(row_key)
            lane_state = lanes.get(lane, {}).get("state", {}) if lane else {}
            verdict: str
            detail: str
            if kind == "direct":
                verdict, detail = (
                    VERDICT_UNOBSERVED,
                    ("direct API-key upstream; use cpa-health.py for end-to-end proof"),
                )
            else:
                credentials = _match_route_credentials(
                    kind, route_name, provider, files, settings_index, route_aliases
                )
                candidates: list[tuple[str, str]] = []
                admission_verdict = (
                    _admission_verdict(lane_state, alias) if lane_state else (None, "")
                )
                if admission_verdict[0] is not None:
                    candidates.append((admission_verdict[0], admission_verdict[1]))
                core_verdict = (
                    _core_verdict(credentials, alias) if files else (None, "")
                )
                if core_verdict[0] is not None:
                    candidates.append((core_verdict[0], core_verdict[1]))
                risk_verdict = _risk_verdict(risk_index, lane, now)
                if risk_verdict[0] is not None:
                    candidates.append((risk_verdict[0], risk_verdict[1]))
                if not files and kind in ("oauth", "plugin"):
                    # Both route kinds serve from auth-file credentials; with
                    # the surface down, "no block observed" would be a guess.
                    candidates.append(
                        (
                            VERDICT_UNKNOWN,
                            auth_note.split(";")[0]
                            if auth_note
                            else "auth-files surface unavailable",
                        )
                    )
                verdict = VERDICT_OK
                detail = "no block observed"
                if candidates:
                    for ranked in _VERDICT_PRECEDENCE:
                        hit = next((c for c in candidates if c[0] == ranked), None)
                        if hit:
                            verdict, detail = hit
                            break
                if (
                    verdict in (VERDICT_OK, VERDICT_UNKNOWN)
                    and kind in ("oauth", "plugin")
                    and not credentials
                    and files
                ):
                    verdict = VERDICT_UNKNOWN
                    detail = (
                        "no credential matched (by route name or provider family); "
                        "check the manifest route vs auth file naming"
                    )
            models.append(
                {
                    "model": alias,
                    "kind": kind,
                    "lane": lane,
                    "verdict": verdict,
                    "detail": detail,
                    "metrics": telemetry_models.get(alias),
                }
            )
    telemetry_only = sorted(set(telemetry_models) - seen_models)

    # Core cooldowns on models the manifest never named: surface them so the
    # view cannot silently hide a blocked model.
    for entry in files:
        for view in _cooldown_views(entry):
            model_key = str(view.get("model_key", ""))
            remaining = view.get("remaining_seconds")
            if not model_key or model_key in seen_models:
                continue
            if not (isinstance(remaining, int) and remaining > 0):
                continue
            seen_models.add(model_key)
            models.append(
                {
                    "model": model_key,
                    "kind": "core-extra",
                    "lane": None,
                    "verdict": VERDICT_COOLING_MODEL,
                    "detail": (
                        f"remaining={remaining}s reason={mask_text(view.get('reason'), 60)}"
                    ),
                    "metrics": telemetry_models.get(model_key),
                }
            )

    channels: list[dict[str, Any]] = []
    for entry in files:
        views = _cooldown_views(entry)
        max_remaining = max(
            (
                view["remaining_seconds"]
                for view in views
                if isinstance(view.get("remaining_seconds"), int)
            ),
            default=0,
        )
        recent: dict[str, Any] | None = None
        buckets = entry.get("recent_requests")
        if isinstance(buckets, list):
            timed = [b for b in buckets if isinstance(b, dict) and b.get("time")]
            if timed:
                latest = max(timed, key=lambda b: str(b["time"]))
                recent = {
                    "time": str(latest.get("time")),
                    "success": latest.get("success"),
                    "failed": latest.get("failed"),
                }
        channels.append(
            {
                "provider": str(entry.get("provider", "")),
                "name": str(entry.get("name", "")),
                "status": str(entry.get("status", "")),
                "disabled": entry.get("disabled") is True,
                "unavailable": entry.get("unavailable") is True,
                "quota_observation": _quota_observation(entry),
                "cooldowns": len(views),
                "cooldown_max_remaining": max_remaining,
                "next_retry_after": entry.get("next_retry_after"),
                "requests_recent": recent,
                "success": entry.get("success"),
                "failed": entry.get("failed"),
                "status_message": mask_text(entry.get("status_message")),
                "email": mask_email(entry.get("email")),
            }
        )

    lane_summary: dict[str, Any] = {}
    for name, lane in lanes.items():
        state = lane["state"]
        lane_summary[name] = {
            "models": lane.get("models"),
            "hold_reason": state.get("hold_reason"),
            "state_write_failed": state.get("state_write_failed"),
            "cooldown_active": state.get("cooldown_active"),
            "cooldown_remaining": state.get("cooldown_remaining"),
            "cooldown_scope": state.get("cooldown_scope"),
            "model_cooldowns": state.get("model_cooldowns"),
            "server_retry_after_remaining": state.get("server_retry_after_remaining"),
            "inflight": state.get("inflight"),
            "pending": state.get("pending"),
        }

    return {
        "schema": 1,
        "generated_at": iso_utc(now),
        "metrics_window_seconds": telemetry_window,
        "surfaces": {
            "healthz": isinstance(healthz, dict),
            "auth_files": bool(files),
            "manifest": isinstance(manifest, dict),
            "risk_files": sorted(risk_index),
            "telemetry_models": len(telemetry_models),
            "errors": errors,
        },
        "lanes": lane_summary,
        "models": models,
        "telemetry_only_models": telemetry_only,
        "channels": channels,
    }


# ---------------------------------------------------------------------------
# rendering


def _metrics_text(metrics: Any) -> str:
    """One-line telemetry summary; tokens/cost are deliberately absent."""
    if not isinstance(metrics, dict):
        return ""
    bits: list[str] = []
    ratio = metrics.get("cache_hit_ratio")
    if isinstance(ratio, (int, float)):
        bits.append(f"cache={ratio * 100:.1f}%")
    for key, label in (
        ("cached_tokens", "cached_tok"),
        ("uncached_input_tokens", "in_tok"),
    ):
        value = metrics.get(key)
        if isinstance(value, int):
            bits.append(f"{label}={value}")
    first_text = metrics.get("first_text_ms")
    if isinstance(first_text, dict) and first_text.get("p50") is not None:
        bits.append(f"ttft p50={first_text.get('p50')}ms p90={first_text.get('p90')}ms")
    samples = metrics.get("samples")
    if isinstance(samples, int):
        bits.append(f"n={samples}")
    return f" [{' '.join(bits)}]" if bits else ""


def render(document: dict[str, Any]) -> list[str]:
    lines: list[str] = ["== CPA model availability =="]
    lines.append(
        f"  generated_at={document.get('generated_at')} "
        "(state view; OK = no block observed, not an active probe)"
    )
    window = document.get("metrics_window_seconds")
    if isinstance(window, int):
        lines.append(
            f"  metrics window: {window}s rolling (cache/ttft from admission "
            "telemetry; token totals and cost are not reported by any "
            "non-destructive surface)"
        )
    models = document.get("models", [])
    width_model = max((len(str(row.get("model"))) for row in models), default=5) + 2
    width_lane = (
        max((len(str(row.get("lane") or "-")) for row in models), default=4) + 2
    )
    width_verdict = max((len(str(row.get("verdict"))) for row in models), default=7) + 2
    for row in models:
        metrics_text = _metrics_text(row.get("metrics"))
        if not metrics_text:
            metrics_text = (
                " -" if row.get("kind") == "direct" else " (no traffic in 1h window)"
            )
        lines.append(
            f"  {str(row.get('model')).ljust(width_model)}"
            f"{str(row.get('lane') or '-').ljust(width_lane)}"
            f"{str(row.get('verdict')).ljust(width_verdict)}"
            f"{row.get('detail')}{metrics_text}"
        )
    if not models:
        lines.append("  (no model rows: no surfaces available)")
    telemetry_only = document.get("telemetry_only_models") or []
    if telemetry_only:
        lines.append(
            "  telemetry series without a manifest row: " + ", ".join(telemetry_only)
        )

    lines.append("== channels ==")
    channels = document.get("channels", [])
    for entry in channels:
        flags = []
        if entry.get("disabled"):
            flags.append("disabled")
        if entry.get("unavailable"):
            flags.append("unavailable")
        flag_text = f" flags={','.join(flags)}" if flags else ""
        next_retry = entry.get("next_retry_after")
        retry_text = f" next_retry={next_retry}" if next_retry else ""
        cooldown_text = (
            f" cooldowns={entry.get('cooldowns')}"
            f"(max={entry.get('cooldown_max_remaining')}s)"
            if entry.get("cooldowns")
            else ""
        )
        recent = entry.get("requests_recent")
        recent_text = (
            f" recent[{recent.get('time')} ok={recent.get('success')} "
            f"fail={recent.get('failed')}]"
            if isinstance(recent, dict)
            else ""
        )
        lines.append(
            f"  {entry.get('provider')}/{entry.get('name')} "
            f"status={entry.get('status')}{flag_text}"
            f"{retry_text}{cooldown_text}{recent_text} "
            f"ok/fail={entry.get('success')}/{entry.get('failed')}"
            f" email={entry.get('email') or '-'}"
        )
        if entry.get("status_message"):
            lines.append(f"    note: {entry.get('status_message')}")
    if not channels:
        lines.append("  (no credential rows: auth-files surface unavailable)")

    surfaces = document.get("surfaces", {})
    errors = surfaces.get("errors", [])
    if errors:
        lines.append("== surface errors ==")
        for error in errors:
            lines.append(f"  {error}")
    return lines


# ---------------------------------------------------------------------------
# local status page (serve mode)

# The page binds loopback only: the document is operator-sensitive and the
# fetch path shells out over SSH, so it must never be exposed off-host.
_STATUS_PORT = 8642
_SNAPSHOT_TTL_SECONDS = 15.0
_PAGE_REFRESH_SECONDS = 30

_VERDICT_CSS = {
    VERDICT_OK: "ok",
    VERDICT_HOLD: "bad",
    VERDICT_STATE_WRITE_FAILED: "bad",
    VERDICT_COOLING_LANE: "bad",
    VERDICT_COOLING_MODEL: "bad",
    VERDICT_PERSISTED_COOLDOWN: "bad",
    VERDICT_QUOTA: "bad",
    VERDICT_CRED_ERROR: "bad",
    VERDICT_CRED_DEGRADED: "warn",
    VERDICT_UNOBSERVED: "info",
    VERDICT_UNKNOWN: "info",
}


def collect_document(profile: str | None) -> dict[str, Any]:
    """One end-to-end collection over SSH; raises ValueError on failure."""
    returncode, stdout, stderr = run_remote(profile)
    if returncode != 0:
        detail = (stdout or stderr or "")[-200:]
        raise ValueError(f"remote execution failed (exit {returncode}): {detail}")
    return _parse_document(stdout)


def _collection_failure_document(message: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "generated_at": iso_utc(time.time()),
        "metrics_window_seconds": None,
        "surfaces": {
            "healthz": False,
            "auth_files": False,
            "manifest": False,
            "risk_files": [],
            "telemetry_models": 0,
            "errors": [f"collection failed: {message}"],
        },
        "lanes": {},
        "models": [],
        "telemetry_only_models": [],
        "channels": [],
    }


class _SnapshotCache:
    """Fresh-per-TTL documents; keeps the last good view on fetch failures."""

    def __init__(
        self,
        profile: str | None,
        ttl_seconds: float,
        loader: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        # The loader is the document source: the local mode shells out over
        # SSH, the on-host daemon mode collects in-process. Swapping it is
        # what lets --remote-serve reuse every other line of this file.
        if loader is not None:
            self._loader = loader
        else:
            self._loader = lambda: collect_document(profile)  # noqa: C3001
        self._lock = threading.Lock()
        self._document: dict[str, Any] | None = None
        self._fetched_monotonic = 0.0

    def get(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if self._document is not None and now - self._fetched_monotonic < self._ttl:
                return self._document
            try:
                document = self._loader()
            except ValueError as exc:
                if self._document is not None:
                    degraded = dict(self._document)
                    surfaces = dict(degraded.get("surfaces", {}))
                    errors = [str(e) for e in surfaces.get("errors", [])]
                    errors.append(f"collection failed: {exc}")
                    surfaces["errors"] = errors
                    degraded["surfaces"] = surfaces
                    return degraded
                return _collection_failure_document(str(exc))
            self._document = document
            self._fetched_monotonic = now
            return document


_PAGE_CSS = """
body{font-family:system-ui,Consolas,monospace;background:#14181f;color:#d7dde6;
margin:1.5rem;font-size:14px}
h1{font-size:18px}h2{font-size:15px;margin-top:1.4rem;color:#9fb3c8}
table{border-collapse:collapse;width:100%;margin-top:.4rem}
td,th{border:1px solid #2a3340;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#1c232d;color:#9fb3c8}
code{color:#8fd0ff}
.ok{color:#7fd18c;font-weight:600}.bad{color:#ff8f8f;font-weight:600}
.warn{color:#ffc36e;font-weight:600}.info{color:#9fb3c8;font-weight:600}
.meta{color:#9fb3c8;font-size:12px}.err{color:#ff8f8f;font-size:13px}
.note{color:#9fb3c8;font-size:12px;margin-top:.4rem}
"""


def render_html(
    document: dict[str, Any], refresh_seconds: int = _PAGE_REFRESH_SECONDS
) -> str:
    """Server-rendered status page; every dynamic string is escaped."""
    surfaces = document.get("surfaces", {})
    errors = [str(e) for e in surfaces.get("errors", [])]
    window = document.get("metrics_window_seconds")

    rows: list[str] = []
    for row in document.get("models", []):
        verdict = str(row.get("verdict", "UNKNOWN"))
        css = _VERDICT_CSS.get(verdict, "info")
        metrics_text = _metrics_text(row.get("metrics"))
        if not metrics_text:
            # Direct upstreams have no admission telemetry at all; routed
            # models with no series simply carried no traffic in the window.
            metrics_text = "-" if row.get("kind") == "direct" else "近1h无流量"
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(row.get('model')))}</code></td>"
            f"<td>{html.escape(str(row.get('lane') or '-'))}</td>"
            f'<td class="{css}">{html.escape(verdict)}</td>'
            f"<td>{html.escape(str(row.get('detail')))}</td>"
            f"<td>{html.escape(metrics_text)}</td>"
            "</tr>"
        )
    channel_rows: list[str] = []
    for entry in document.get("channels", []):
        flags = []
        if entry.get("disabled"):
            flags.append("disabled")
        if entry.get("unavailable"):
            flags.append("unavailable")
        cooldown_text = (
            f"{entry.get('cooldowns')} (max {entry.get('cooldown_max_remaining')}s)"
            if entry.get("cooldowns")
            else "-"
        )
        next_retry = entry.get("next_retry_after") or "-"
        recent = entry.get("requests_recent")
        if isinstance(recent, dict):
            recent_text = f"{recent.get('time')} ok={recent.get('success')} fail={recent.get('failed')}"
        else:
            recent_text = "-"
        channel_rows.append(
            "<tr>"
            f"<td>{html.escape(str(entry.get('provider')))}</td>"
            f"<td><code>{html.escape(str(entry.get('name')))}</code></td>"
            f"<td>{html.escape(str(entry.get('status')))}</td>"
            f"<td>{html.escape(','.join(flags) or '-')}</td>"
            f"<td>{html.escape(cooldown_text)}</td>"
            f"<td>{html.escape(str(next_retry))}</td>"
            f"<td>{html.escape(recent_text)}</td>"
            f"<td>{html.escape(str(entry.get('success')))}/"
            f"{html.escape(str(entry.get('failed')))}</td>"
            f"<td>{html.escape(str(entry.get('email') or '-'))}</td>"
            f"<td>{html.escape(str(entry.get('status_message') or '-'))}</td>"
            "</tr>"
        )

    error_html = ""
    if errors:
        items = "".join(f"<li>{html.escape(e)}</li>" for e in errors)
        error_html = f'<h2>采集面错误</h2><ul class="err">{items}</ul>'
    telemetry_only = document.get("telemetry_only_models") or []
    telemetry_note = ""
    if telemetry_only:
        telemetry_note = (
            '<p class="note">无清单行的 telemetry 序列: '
            f"{html.escape(', '.join(str(m) for m in telemetry_only))}</p>"
        )

    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
        "<title>CPA 模型可用性 / 渠道健康</title>"
        f"<style>{_PAGE_CSS}</style></head><body>"
        "<h1>CPA 模型可用性 / 渠道健康</h1>"
        f'<p class="meta">generated_at={html.escape(str(document.get("generated_at")))} '
        f"· 数据经 SSH 实时采集 · 每 {int(refresh_seconds)}s 自动刷新 · "
        "OK = 无阻断观察（状态视图，非主动探测）</p>"
        f'<p class="meta">指标窗口: {html.escape(str(window if window is not None else "n/a"))}s '
        "滚动（缓存命中率/首字延迟来自 admission telemetry；指标空白 = 该模型近 1h 无流量，非累计值；"
        "token 总量与费用无无害只读面，不显示）</p>"
        "<h2>模型</h2>"
        "<table><tr><th>模型</th><th>车道</th><th>判定</th><th>说明</th><th>指标</th></tr>"
        + ("".join(rows) or '<tr><td colspan="5">(无模型行：无可用采集面)</td></tr>')
        + "</table>"
        + telemetry_note
        + "<h2>渠道（凭据）</h2>"
        "<table><tr><th>渠道</th><th>凭据</th><th>状态</th><th>标记</th>"
        "<th>冷却</th><th>next_retry</th><th>近期</th><th>ok/fail</th><th>邮箱</th><th>备注</th></tr>"
        + (
            "".join(channel_rows)
            or '<tr><td colspan="10">(无凭据行：auth-files 面不可用)</td></tr>'
        )
        + "</table>"
        + error_html
        + '<p class="note">JSON 视图: <code>/json</code> · 判定词: OK/COOLING_LANE/'
        "COOLING_MODEL/PERSISTED_COOLDOWN/QUOTA/CRED_ERROR/CRED_DEGRADED/HOLD/"
        "STATE_WRITE_FAILED/UNOBSERVED/UNKNOWN</p>"
        "</body></html>"
    )


def _make_handler(cache: _SnapshotCache) -> type[BaseHTTPRequestHandler]:
    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            # The nginx shell may pass the /cpa-status/ prefix through (or
            # strip it via a proxy_pass trailing slash); accept both spellings
            # so the daemon never 404s a correctly-gated request.
            if path in ("/", "/cpa-status/", "/cpa-status"):
                body = render_html(cache.get()).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif path in ("/json", "/cpa-status/json"):
                body = json.dumps(cache.get(), ensure_ascii=False, indent=1).encode(
                    "utf-8"
                )
                content_type = "application/json; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            del format, args  # per-request noise stays out of the console

    return StatusHandler


def _host_document_loader() -> dict[str, Any]:
    """On-host document source: in-process collection, no SSH involved."""
    return synthesize(collect_surfaces())


def serve(
    profile: str | None,
    port: int,
    host: str = "127.0.0.1",
    loader: Callable[[], dict[str, Any]] | None = None,
) -> int:
    """Serve the status page; loopback only, Ctrl+C to stop."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(
            "the status page binds loopback only; the document is "
            "operator-sensitive and the fetch path shells out over SSH"
        )
    cache = _SnapshotCache(profile, _SNAPSHOT_TTL_SECONDS, loader=loader)
    server = ThreadingHTTPServer((host, port), _make_handler(cache))
    print(
        f"CPA 状态页: http://{host}:{port}/  (Ctrl+C 停止; "
        f"每 {_PAGE_REFRESH_SECONDS}s 自动刷新, JSON 在 /json)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# transport (local side)

# The argv budget, the compression and the ssh_tool.py argument order live in
# scripts/launcher_transport.py so the audit tools cannot drift apart.
#
# The import is deliberate: this same file is byte-projected to
# /opt/cpa-status/cpa_status.py and executed there by systemd, where no
# repository package exists. A module-level import would break the host
# deployment, so the transport is resolved lazily -- only the local operator
# path ever reaches it. Tests read the budget from scripts.launcher_transport
# directly; no module-attribute shim is needed for that.
REMOTE_TEMP_PREFIX = "cpa-status"
_TRANSPORT: Any = None


def _transport() -> Any:
    """Resolve the local-only transport module on first use.

    Only the operator path (run it from a checkout) reaches this. The projected
    host runtime serves --remote/--remote-serve and never builds an SSH
    command, so a missing repository package there is a programming error, not
    an environment one: fail closed with a message that says which path broke.
    """
    global _TRANSPORT
    if _TRANSPORT is not None:
        return _TRANSPORT
    try:
        from scripts import launcher_transport
    except ModuleNotFoundError:
        if __package__:
            raise
        try:
            from bootstrap import ensure_repo_imports  # type: ignore[import-not-found]

            ensure_repo_imports()
            from scripts import launcher_transport  # type: ignore[import-not-found,no-redef]
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "cpa_status: the local ssh_tool transport is unavailable in this "
                "runtime; the projected host copy is read-only and must be given "
                "--remote or --remote-serve"
            ) from exc

    _TRANSPORT = launcher_transport
    return launcher_transport


def _remote_payload() -> str:
    """zlib+base64 of this file; single line, single-quote safe."""
    payload: str = _transport().encode_source_payload(
        pathlib.Path(__file__).read_bytes()
    )
    return payload


def _remote_command() -> str:
    command: str = _transport().remote_temp_python_command(
        payload=_remote_payload(),
        temp_prefix=REMOTE_TEMP_PREFIX,
        action='python3 "$tmp" --remote --json',
    )
    return command


def run_remote(profile: str | None) -> tuple[int, str, str]:
    """Run the self-transport collection; returns (exit, stdout, stderr).

    The streams stay separated: ssh warnings (banner, "Permanently added")
    and remote stderr land in the third element, so a successful collection
    followed by any stderr tail cannot poison the JSON document that
    _parse_document slices out of stdout.
    """
    try:
        command = _remote_command()
    except ValueError as exc:
        return 2, "", f"cpa_status: {exc}"
    argv = _transport().launcher_argv(
        repo_root=pathlib.Path(__file__).parent.parent,
        command=command,
        profile=profile,
    )
    completed = subprocess.run(
        [sys.executable, *argv], capture_output=True, text=True, check=False
    )
    return completed.returncode, completed.stdout, completed.stderr


def _parse_document(output: str) -> dict[str, Any]:
    start = output.find("{")
    if start < 0:
        raise ValueError("remote produced no JSON document")
    document = json.loads(output[start:])
    if not isinstance(document, dict):
        raise ValueError("remote document is not a JSON object")
    return document


def _make_streams_lossy_safe() -> None:
    """Local-console adapter only; the remote runtime never imports it."""
    try:
        from scripts.audit_cli import make_streams_lossy_safe
    except ModuleNotFoundError:
        if __package__:
            raise
        from bootstrap import ensure_repo_imports  # type: ignore[import-not-found]

        ensure_repo_imports()
        from scripts.audit_cli import make_streams_lossy_safe

    make_streams_lossy_safe()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only CPA per-model availability and channel health view."
    )
    parser.add_argument("--profile", default=None, help="target.json profile")
    parser.add_argument("--json", action="store_true", help="emit the JSON document")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="serve the status page on 127.0.0.1 (browser view, auto-refresh)",
    )
    parser.add_argument(
        "--remote-serve",
        action="store_true",
        help="on-host daemon mode: collect in-process (no SSH) and serve; "
        "deployed to /opt/cpa-status/ behind the nginx basic-auth shell",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_STATUS_PORT,
        help=f"status page port (default: {_STATUS_PORT})",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: executed on the host via ssh_tool
    )
    args = parser.parse_args(argv)

    if args.remote:
        document = synthesize(collect_surfaces())
        print(json.dumps(document, ensure_ascii=False, indent=1))
        return 0

    if args.remote_serve:
        # On-host daemon: no local adapter imports (they do not exist on the
        # host), loopback-only guard stays; nginx's basic-auth shell provides
        # the operator-facing gate.
        return serve(None, args.port, loader=_host_document_loader)

    _make_streams_lossy_safe()

    if args.serve:
        return serve(args.profile, args.port)

    returncode, stdout, stderr = run_remote(args.profile)
    if returncode != 0:
        print(
            f"cpa_status: remote execution failed (exit {returncode})", file=sys.stderr
        )
        print(stdout, file=sys.stderr)
        print(stderr, file=sys.stderr)
        return 2
    try:
        document = _parse_document(stdout)
    except ValueError as exc:
        print(f"cpa_status: {exc}", file=sys.stderr)
        print(stdout, file=sys.stderr)
        print(stderr, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=1))
    else:
        for line in render(document):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only per-model availability and per-channel health view for CPA.

One recurring operator question needs evidence instead of log archaeology:
**why is a model unavailable right now?** The answer is spread across four
independent surfaces that no existing panel joins:

* the admission broker's ``/healthz`` (loopback 8318): per-lane breaker state,
  per-model cooldowns, server Retry-After, manual holds;
* the CPA core management API ``/v0/management/auth-files`` (loopback 8317):
  per-credential status, quota exhaustion, per-model cooldown snapshots
  (``CooldownSnapshotForAuth`` ships in production core v8.0.61);
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

Exit codes: ``0`` a document was produced (surface errors are embedded),
``2`` usage, transport or unparseable-output failure.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Sequence

CORE_PORT = 8317
ADMISSION_PORT = 8318
CONFIG_PATH = "/opt/cliproxyapi/config.yaml"
ADMISSION_CONFIG_ENV = "VPS_SSH_LAUNCHER_ADMISSION_CONFIG"
ADMISSION_CONFIG_PATH = "/opt/cliproxyapi/cpa-admission.json"
RISK_STATE_DIR = "/var/lib/cpa-admission"
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


def _management_secret(config: dict[str, Any]) -> str | None:
    management = config.get("management")
    if not isinstance(management, dict):
        return None
    secret = management.get("secret-key")
    if isinstance(secret, str) and secret.strip():
        return secret
    return None


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
    secret = _management_secret(config) if config else None
    if secret is None:
        errors.append("auth-files: management.secret-key unavailable")
    else:
        try:
            auth_files = _loopback_get_json(
                f"http://127.0.0.1:{CORE_PORT}/v0/management/auth-files",
                {"X-Management-Key": secret},
                5.0,
            )
        except urllib.error.HTTPError as exc:
            errors.append(f"auth-files: HTTP {exc.code}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f"auth-files: {exc.__class__.__name__}")

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
        "config_present": config is not None,
        "healthz": healthz,
        "auth_files": auth_files,
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


def _match_route_credentials(
    kind: str, route_name: str, provider: str, files: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Lenient credential match; zero matches degrade the row, never crash."""
    if kind == "oauth":
        target = route_name
        return [
            entry
            for entry in files
            if _entry_stem(entry.get("name")) == target
            or target in str(entry.get("name", ""))
        ]
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
        for raw_model in model_list:
            alias = (
                raw_model
                if isinstance(raw_model, str)
                else (
                    raw_model.get("alias")
                    if isinstance(raw_model, dict)
                    and isinstance(raw_model.get("alias"), str)
                    else None
                )
            )
            if not alias:
                continue
            seen_models.add(alias)
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
                    kind, route_name, provider, files
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
                if not files and kind == "oauth":
                    candidates.append(
                        (VERDICT_UNKNOWN, "auth-files surface unavailable")
                    )
                verdict = VERDICT_OK
                detail = "no block observed"
                if candidates:
                    for ranked in _VERDICT_PRECEDENCE:
                        hit = next((c for c in candidates if c[0] == ranked), None)
                        if hit:
                            verdict, detail = hit
                            break
                if verdict == VERDICT_OK and kind == "oauth" and not credentials:
                    verdict = VERDICT_UNKNOWN
                    detail = "no credential matched the route name"
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
        lines.append(
            f"  {str(row.get('model')).ljust(width_model)}"
            f"{str(row.get('lane') or '-').ljust(width_lane)}"
            f"{str(row.get('verdict')).ljust(width_verdict)}"
            f"{row.get('detail')}{_metrics_text(row.get('metrics'))}"
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
# transport (local side)


def _remote_payload() -> str:
    source = pathlib.Path(__file__).read_bytes()
    return base64.b64encode(source).decode("ascii")


def _remote_command() -> str:
    return (
        "umask 077; tmp=$(mktemp /tmp/cpa-status.XXXXXXXXXX.py) || exit 1; "
        "trap 'rm -f -- \"$tmp\"' EXIT; "
        f"printf %s '{_remote_payload()}' | base64 -d > \"$tmp\" && "
        'python3 "$tmp" --remote --json'
    )


def run_remote(profile: str | None) -> tuple[int, str]:
    argv = [sys.executable, str(pathlib.Path(__file__).parent.parent / "ssh_tool.py")]
    if profile:
        argv.extend(["--profile", profile])
    argv.extend(["run", "--command", _remote_command()])
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout + completed.stderr


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
        "--remote",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: executed on the host via ssh_tool
    )
    args = parser.parse_args(argv)

    if args.remote:
        document = synthesize(collect_surfaces())
        print(json.dumps(document, ensure_ascii=False, indent=1))
        return 0

    _make_streams_lossy_safe()
    returncode, output = run_remote(args.profile)
    if returncode != 0:
        print(
            f"cpa_status: remote execution failed (exit {returncode})", file=sys.stderr
        )
        print(output, file=sys.stderr)
        return 2
    try:
        document = _parse_document(output)
    except ValueError as exc:
        print(f"cpa_status: {exc}", file=sys.stderr)
        print(output, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=1))
    else:
        for line in render(document):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

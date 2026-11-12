"""HG-03 evidence contracts shared by HOME and OFFICE.

The command accepts a private JSON request and emits a redacted, schema-bound
evidence bundle. It never stores request bodies, credentials, capability paths
or arbitrary exception text. HOME may issue exactly one controlled CPA
request; all other observations are natural-traffic snapshots.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

if __package__ in {None, ""}:
    # The runbook invokes this as a script. Keep the repository package
    # importable without importing scripts.hg03_contract as a second module.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.cpa_stream_acceptance import probe as probe_responses
from scripts.cpa_stream_acceptance import public_endpoint

SCHEMA = "hg03-evidence/v1"
OUTCOMES = frozenset({"PASS", "FAIL", "BLOCKED", "NOT_RUN", "UNVERIFIED"})
PRODUCTS = frozenset(
    {
        "home_baseline",
        "route_decision",
        "server_ready",
        "access_acceptance",
        "ecs_optimization",
    }
)
LAYERS = frozenset(
    {"repo_verified", "filesystem_projected", "host_loaded", "live_accepted"}
)
ROUTES = frozenset({"D", "E", "H", "C", "M"})
CHECK_KINDS = frozenset(
    {
        "web",
        "git",
        "cpa",
        "sse",
        "codex",
        "glm",
        "intranet",
        "home_regression",
        "bwg_regression",
        "ecs",
        "qq",
    }
)
REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
CREDENTIAL_MARKERS = re.compile(r"(?i)(bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+)")
URL_MARKER = re.compile(r"(?i)https?://[^\s\"'<>]+")
# A capability mention without a scheme still leaks the protected path.
HOST_PORT_PATH_MARKER = re.compile(
    r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}:\d{2,5}/(?!<protected>)[^\s\"'<>]+"
)


class ContractError(ValueError):
    """Input or evidence is incomplete; no acceptance is implied."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(
    value: Any,
    *,
    name: str,
    required: bool = True,
    limit: int = 512,
) -> str:
    if not isinstance(value, str):
        if required or value is not None:
            raise ContractError(f"{name} must be a string")
        return ""
    value = value.strip()
    if required and not value:
        raise ContractError(f"{name} is required")
    if len(value) > limit:
        raise ContractError(f"{name} exceeds {limit} characters")
    text: str = str(value)
    return text


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} must be an object")
    return value


def _optional_bool(value: Any, *, name: str) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise ContractError(f"{name} must be a boolean")
    return value


def _outcome(value: Any) -> str:
    outcome = _text(value, name="outcome", limit=32)
    if outcome not in OUTCOMES:
        raise ContractError("outcome is not an HG-03 evidence status")
    return outcome


def safe_endpoint_reference(base_url: str) -> str:
    """Keep host/port while stripping all capability and query material."""
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ContractError("endpoint must be an HTTPS URL")
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port else ""
    value: str = urlunsplit(("https", host + port, "/<protected>", "", ""))
    return value


def _endpoint_markers(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    markers = [
        match.group(0)[:160]
        for match in URL_MARKER.finditer(value)
        if urlsplit(match.group(0)).path not in {"", "/", "/<protected>"}
        or urlsplit(match.group(0)).query
        or urlsplit(match.group(0)).fragment
    ]
    markers.extend(
        match.group(0)[:160] for match in HOST_PORT_PATH_MARKER.finditer(value)
    )
    return markers


def _assert_redacted(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    if CREDENTIAL_MARKERS.search(serialized) or _endpoint_markers(serialized):
        raise ContractError("evidence contains a capability path or credential")


def _check_shape(check: Mapping[str, Any]) -> dict[str, Any]:
    kind = _text(check.get("kind"), name="check.kind", limit=32)
    if kind not in CHECK_KINDS:
        raise ContractError("check.kind is not admitted")
    result: dict[str, Any] = {
        "kind": kind,
        "outcome": _outcome(check.get("outcome")),
        "summary": _text(check.get("summary"), name=f"check.{kind}.summary"),
    }
    for field in ("reason_code", "request_id", "model", "route"):
        value = _text(
            check.get(field),
            name=f"check.{kind}.{field}",
            required=False,
            limit=128,
        )
        if value:
            if field == "request_id" and not REQUEST_ID.fullmatch(value):
                raise ContractError("request_id has an invalid shape")
            if field == "route" and value not in ROUTES:
                raise ContractError("check.route is not admitted")
            result[field] = value
    evidence = _mapping(check.get("evidence", {}), name=f"check.{kind}.evidence")
    if len(evidence) > 32:
        raise ContractError("check.evidence has too many fields")
    result["evidence"] = {
        _text(key, name="evidence.key", limit=64): _text(
            item, name=f"evidence.{key}", required=False, limit=512
        )
        for key, item in sorted(evidence.items())
    }
    _assert_redacted(result)
    if result["outcome"] == "PASS":
        if result["evidence"].get("actual_business") != "true":
            raise ContractError(f"{kind} PASS requires actual_business=true")
        if kind == "cpa" and not (
            result.get("request_id") and result["evidence"].get("terminal") == "PASS"
        ):
            raise ContractError("cpa PASS requires request_id and terminal=PASS")
        if kind in {"sse", "codex"} and not result.get("request_id"):
            raise ContractError(f"{kind} PASS requires a CPA request_id")
        if kind == "git" and not result["evidence"].get("remote_object"):
            raise ContractError("git PASS requires remote_object=true")
    return result


def validate_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize an evidence bundle without adding credentials."""
    if value.get("schema") != SCHEMA:
        raise ContractError(f"schema must be {SCHEMA}")
    product = _text(value.get("product"), name="product", limit=64)
    if product not in PRODUCTS:
        raise ContractError("product is not admitted")
    role = _text(value.get("role"), name="role", limit=16).upper()
    if role not in {"HOME", "OFFICE"}:
        raise ContractError("role must be HOME or OFFICE")
    layer = _text(value.get("layer"), name="layer", limit=32)
    if layer not in LAYERS:
        raise ContractError("layer is not admitted")
    commit = _text(value.get("commit"), name="commit", limit=128)
    checks = value.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= 32:
        raise ContractError("checks must contain 1..32 observations")
    normalized = [_check_shape(_mapping(item, name="check")) for item in checks]
    if len({item["kind"] for item in normalized}) != len(normalized):
        raise ContractError("check kinds must be unique")
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "product": product,
        "role": role,
        "commit": commit,
        "layer": layer,
        "checks": normalized,
    }
    status = value.get("status")
    result["status"] = _outcome(status) if status is not None else "UNKNOWN"
    if product == "access_acceptance" and value.get("request_probe") is not None:
        result["request_probe"] = _optional_bool(
            value.get("request_probe"), name="request_probe"
        )
    pending_probe = (
        product == "access_acceptance"
        and result.get("request_probe") is True
        and result["status"] == "UNVERIFIED"
    )
    if (
        layer == "live_accepted"
        and product != "access_acceptance"
        and result["status"] != "PASS"
    ):
        raise ContractError("live_accepted requires status=PASS")
    if result["status"] == "PASS" and any(
        item["outcome"] != "PASS" for item in normalized
    ):
        raise ContractError("PASS conflicts with a non-PASS observation")
    _assert_redacted(result)
    goal = _text(value.get("goal"), name="goal", required=False)
    if goal:
        result["goal"] = goal
    created_at = _text(value.get("created_at"), name="created_at", required=False)
    result["created_at"] = created_at or _iso_now()
    timestamp = _text(value.get("timestamp"), name="timestamp", required=False)
    result["timestamp"] = timestamp or result["created_at"]
    route = _text(value.get("route"), name="route", required=False, limit=16)
    if route:
        if route not in ROUTES:
            raise ContractError("route is not admitted")
        result["route"] = route
    if product == "route_decision" and not route:
        raise ContractError("route_decision requires route")

    route_fields = value.get("route_fields")
    if route_fields is not None:
        fields = _mapping(route_fields, name="route_fields")
        normalized_fields = {
            key: _text(
                item,
                name=f"route_fields.{key}",
                required=False,
                limit=512,
            )
            for key, item in sorted(fields.items())
        }
        result["route_fields"] = normalized_fields
        if product == "route_decision":
            missing = [
                name
                for name in ("authorization", "link", "business", "cost")
                if not normalized_fields.get(name)
            ]
            if missing:
                raise ContractError(
                    "route_decision fields missing: " + ", ".join(missing)
                )
    elif product == "route_decision":
        raise ContractError("route_decision requires route_fields")

    if product == "access_acceptance":
        hotspot = _optional_bool(
            value.get("hotspot_disconnected"), name="hotspot_disconnected"
        )
        result["hotspot_disconnected"] = hotspot
        if layer == "live_accepted" and not pending_probe and hotspot is not True:
            raise ContractError(
                "live access acceptance requires explicit hotspot_disconnected=true"
            )
        if (
            layer == "live_accepted"
            and not pending_probe
            and any(item["outcome"] != "PASS" for item in normalized)
        ):
            raise ContractError("live access acceptance requires all checks PASS")
    else:
        hotspot = _optional_bool(
            value.get("hotspot_disconnected"), name="hotspot_disconnected"
        )
        if hotspot is not None:
            result["hotspot_disconnected"] = hotspot
    consistent = value.get("request_consistency")
    if consistent is not None:
        consistent = _mapping(consistent, name="request_consistency")
        request_id = _text(
            consistent.get("request_id"),
            name="request_consistency.request_id",
            required=False,
            limit=128,
        )
        if request_id:
            if not REQUEST_ID.fullmatch(request_id):
                raise ContractError("request consistency ID has an invalid shape")
            result["request_consistency"] = {
                "request_id": request_id,
                "server_seen": _optional_bool(
                    consistent.get("server_seen"),
                    name="request_consistency.server_seen",
                ),
                "upstream_contacted": _optional_bool(
                    consistent.get("upstream_contacted"),
                    name="request_consistency.upstream_contacted",
                ),
            }
            if any(
                item.get("request_id") and item["request_id"] != request_id
                for item in normalized
            ):
                raise ContractError("request consistency conflicts with a check")
    if (
        product == "access_acceptance"
        and (
            result.get("request_probe") is True
            or (result.get("status") == "PASS" and result.get("request_probe") is None)
        )
        and not result.get("request_consistency")
    ):
        raise ContractError(
            "request_consistency is required for CPA/SSE access acceptance"
        )
    if (
        product == "access_acceptance"
        and layer == "live_accepted"
        and result["status"] == "PASS"
        and any(item["kind"] in {"cpa", "sse", "codex"} for item in normalized)
        and not (
            isinstance(result.get("request_consistency"), Mapping)
            and result["request_consistency"].get("server_seen") is True
        )
    ):
        raise ContractError(
            "live acceptance of cpa/sse/codex requires request_consistency server_seen=true"
        )
    if product == "server_ready" and layer == "host_loaded":
        if not _text(
            value.get("projection_sha256"),
            name="projection_sha256",
            required=False,
            limit=64,
        ):
            raise ContractError("host_loaded server_ready requires projection_sha256")

    consistency = result.get("request_consistency")
    if isinstance(consistency, Mapping) and consistency.get("server_seen") is not True:
        # A controlled probe is recorded before HOME can attach the fresh
        # server-side Admission receipt. Keep that evidence UNVERIFIED while
        # still refusing a claimed accepted result.
        pending_probe = (
            product == "access_acceptance"
            and result.get("request_probe") is True
            and result.get("status") == "UNVERIFIED"
        )
        if not pending_probe:
            raise ContractError("request_consistency requires server_seen true")
    return result


def ensure_private_output(path: Path) -> Path:
    """Fail closed on existing, broad or non-file output targets."""
    path = path.expanduser().resolve()
    if path.exists() or path.is_symlink():
        raise ContractError("evidence output already exists")
    repository = Path(__file__).resolve().parents[1]
    if path.is_relative_to(repository):
        raise ContractError("evidence output must be outside the repository")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_bundle(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically create a new evidence file; never overwrite.

    POSIX targets get mode 0600. On Windows the effective ACLs inherit from
    the parent directory, so keep evidence under a private user directory.
    """
    validated = validate_bundle(value)
    target = ensure_private_output(path)
    payload = json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True)
    temporary = target.with_name(target.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload + "\n")
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def cpa_outcome(result: Mapping[str, Any]) -> tuple[str, str]:
    """Map the one-shot probe to a contract outcome and stable reason."""
    if result.get("result") == "PASS":
        return "PASS", "completed"
    failures = result.get("failures")
    reason = failures[0] if isinstance(failures, list) and failures else "unknown"
    if reason in {"transport_error", "deadline_exceeded"}:
        return "UNVERIFIED", str(reason)
    return "FAIL", str(reason)


def build_access_bundle(
    *,
    codex_config: Path,
    timeout: float,
    model: str,
    request_probe: bool,
    role: str,
    commit: str,
    route: str,
    layer: str,
    hotspot_disconnected: bool | None,
    route_fields: Mapping[str, Any],
) -> dict[str, Any]:
    """Observe and optionally issue one request; endpoint read failures stay safe."""
    base_url: str | None = None
    checks: list[dict[str, Any]] = []
    consistency: dict[str, Any] | None = None
    endpoint_read_error = "config_unavailable"
    request_probe_made: bool | None = True if request_probe else None
    try:
        base_url, _key = public_endpoint(codex_config)
    except (OSError, UnicodeError, ValueError) as exc:
        request_probe_made = False if request_probe else None
        endpoint_read_error = (
            "config_unavailable"
            if isinstance(exc, (OSError, UnicodeError))
            else "endpoint_invalid"
        )
        if request_probe:
            checks.append(
                {
                    "kind": "cpa",
                    "outcome": "UNVERIFIED",
                    "summary": "local CPA endpoint could not be read",
                    "reason_code": endpoint_read_error,
                    "model": model,
                    "evidence": {"retries": "0"},
                }
            )
    else:
        if request_probe:
            response = probe_responses(
                None,
                model,
                None,
                timeout,
                codex_config=codex_config,
            )
            outcome, reason = cpa_outcome(response)
            request_id = response.get("request_id")
            checks.append(
                {
                    "kind": "cpa",
                    "outcome": outcome,
                    "summary": (
                        "one-shot CPA generation reached a successful protocol terminal"
                        if outcome == "PASS"
                        else "one-shot CPA generation did not meet the protocol terminal"
                    ),
                    "reason_code": reason,
                    "request_id": request_id if isinstance(request_id, str) else None,
                    "model": model,
                    "evidence": {
                        "actual_business": "true" if outcome == "PASS" else "false",
                        "http_status": str(response.get("http_status", "none")),
                        "terminal": (
                            "PASS"
                            if outcome == "PASS"
                            else str(response.get("terminal", "missing"))
                        ),
                        "first_text_ms": str(response.get("first_text_ms", "none")),
                        "total_ms": str(response.get("total_ms", "none")),
                        "endpoint": safe_endpoint_reference(base_url),
                        "retries": "0",
                    },
                }
            )
            consistency = {
                "request_id": request_id,
                "server_seen": None,
                "upstream_contacted": None,
            }
    if not checks:
        checks.append(
            {
                "kind": "cpa",
                "outcome": "UNVERIFIED",
                "summary": "local endpoint reference read; no business request issued",
                "reason_code": (
                    "probe_not_requested"
                    if base_url is not None
                    else endpoint_read_error
                ),
                "evidence": {
                    "endpoint": (
                        safe_endpoint_reference(base_url)
                        if base_url is not None
                        else "unavailable"
                    ),
                    "retries": "0",
                },
            }
        )
    return {
        "schema": SCHEMA,
        "product": "access_acceptance",
        "role": role,
        "commit": commit,
        "layer": layer,
        "route": route,
        "route_fields": dict(route_fields),
        "hotspot_disconnected": hotspot_disconnected,
        "request_probe": request_probe_made,
        "request_consistency": consistency,
        "checks": checks,
        # The local protocol terminal is useful evidence, but acceptance waits
        # for the second step: a fresh server-side receipt with server_seen=true.
        "status": "UNVERIFIED",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validate", type=Path, help="validate JSON and optionally --output normalized"
    )
    parser.add_argument("--output", type=Path, help="new private evidence path")
    parser.add_argument(
        "--cpa-from-codex-config",
        type=Path,
        help="observe the existing HTTPS provider; issue one request only with --cpa-probe",
    )
    parser.add_argument("--cpa-probe", action="store_true")
    parser.add_argument(
        "--model", choices=("gpt-6-luna", "gpt-6.1-sol"), default="gpt-6-luna"
    )
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--role", choices=("HOME", "OFFICE"), default="HOME")
    parser.add_argument("--commit", default="unknown")
    parser.add_argument("--route", default="D")
    parser.add_argument(
        "--layer",
        choices=(
            "repo_verified",
            "filesystem_projected",
            "host_loaded",
            "live_accepted",
        ),
        default="live_accepted",
    )
    parser.add_argument("--hotspot-disconnected", action="store_true")
    args = parser.parse_args(argv)
    if args.validate is not None:
        try:
            value = json.loads(args.validate.read_text(encoding="utf-8"))
            validated = validate_bundle(_mapping(value, name="evidence"))
        except (OSError, UnicodeError, json.JSONDecodeError, ContractError) as exc:
            print(f"HG03_CONTRACT=FAIL reason={type(exc).__name__}")
            return 1
        if args.output is not None:
            try:
                write_bundle(args.output, validated)
            except (OSError, ContractError) as exc:
                print(f"HG03_EVIDENCE_WRITE=FAIL reason={type(exc).__name__}")
                return 1
        print("HG03_CONTRACT=PASS")
        return 0
    if args.cpa_from_codex_config is None:
        parser.error("provide --validate or --cpa-from-codex-config")
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be greater than zero and at most 300 seconds")
    if args.output is None:
        parser.error("--output is required to persist evidence")
    try:
        value = build_access_bundle(
            codex_config=args.cpa_from_codex_config,
            timeout=args.timeout,
            model=args.model,
            request_probe=args.cpa_probe,
            role=args.role,
            commit=args.commit,
            route=args.route,
            layer=args.layer,
            hotspot_disconnected=True if args.hotspot_disconnected else None,
            route_fields={"authorization": "existing approved route"},
        )
        write_bundle(args.output, value)
    except (OSError, UnicodeError, ContractError) as exc:
        print(f"HG03_EVIDENCE_WRITE=FAIL reason={type(exc).__name__}")
        return 1
    check = value["checks"][0] if value["checks"] else None
    print(
        "HG03_ACCESS="
        + json.dumps(
            {
                "outcome": check["outcome"] if check else "UNVERIFIED",
                "request_id": check.get("request_id") if check else None,
                "output": str(args.output),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    # A recorded readiness observation is a successful tool operation even when
    # no business request was made. Its evidence remains UNVERIFIED.
    return 0 if check and (not args.cpa_probe or check["outcome"] == "PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())

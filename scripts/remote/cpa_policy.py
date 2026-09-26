"""Validate the BWG CPA semantic safety policy without exposing secrets."""

from __future__ import annotations

import json
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


EXPECTED_TOP_LEVEL: dict[str, Any] = {
    # CPA runs inside Docker. The host-side Compose binding is the security
    # boundary and is asserted separately by the guardrail script; the
    # container must bind all interfaces so Nginx can reach it through the
    # published loopback port.
    "host": "0.0.0.0",
    "port": 8317,
    "force-model-prefix": True,
    "request-retry": 0,
    "max-retry-credentials": 1,
    "disable-cooling": False,
    "save-cooldown-status": False,
    "transient-error-cooldown-seconds": 60,
    # Error request dumps can contain prompts, headers, and client keys. Keep
    # CPA's own retained-file cap small; the updater also enforces a 24h age
    # bound independently.
    "error-logs-max-files": 5,
    # Bound aggregate log storage as well as the number of retained dumps.
    "logs-max-total-size-mb": 32,
    # The in-memory usage queue is the only source of cache-hit and lane-mix
    # telemetry the doctor's `==cache-usage==` segment can read. Disabling it
    # silently downgrades that observation to UNAVAILABLE instead of failing,
    # so the enablement itself is pinned here.
    "usage-statistics-enabled": True,
}

EXPECTED_ROUTING = {
    "strategy": "fill-first",
    "session-affinity": True,
    "session-affinity-ttl": "1h",
    # Keep each child task eligible for a separate credential. Binding every
    # subagent to the parent improves cache locality but concentrates bursts on
    # a single account, which is the less safe default for this public gateway.
    "session-affinity-subagents": False,
}

EXPECTED_QUOTA = {
    "switch-project": False,
    "switch-preview-model": False,
    "antigravity-credits": False,
}

EXPECTED_CODEX = {
    # Keep overload classification enabled, but bound how long bootstrap
    # frames can delay downstream response headers on a slow provider.
    "stream-bootstrap-buffering": True,
    "stream-bootstrap-timeout": "20s",
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
    slots: set[int] = set()
    hosts: set[str] = set()
    names: set[str] = set()
    aliases: set[str] = set()
    gpt_routes: set[str] = set()
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
            if alias.lower() in aliases:
                issues.append(f"route alias {alias!r} is assigned more than once")
            aliases.add(alias.lower())
            if alias.lower().startswith(("gpt-", "codex-")):
                gpt_routes.add(alias)
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
    retired = manifest.get("retired_hosts")
    if not isinstance(retired, list) or not all(
        isinstance(host, str) and host for host in retired
    ):
        issues.append("route manifest retired_hosts must be a list of non-empty hosts")
        retired = []
    if hosts.intersection(retired):
        issues.append("route manifest cannot include a retired provider host")
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
EXPECTED_CHANNEL_HOST = "ai.input.im"
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
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
)
EXCLUSIVE_CODEX_API_KEY_ROUTES = frozenset(
    _manifest_strings("codex_api_key_exclusions")
)
EXPECTED_PROVIDER_MODEL_MAP = {
    provider["host"]: {
        model["name"]: model["alias"]
        for model in provider["models"]
        if isinstance(model.get("name"), str) and isinstance(model.get("alias"), str)
    }
    for provider in _VALID_PROVIDER_ROUTES
}
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


def _provider_transport_issues(provider: Any, label: str) -> list[str]:
    if not isinstance(provider, dict):
        return [f"{label} must be a mapping"]
    issues: list[str] = []
    if "api-key" in provider:
        issues.append(f"{label}.api-key legacy field must be absent")
    for raw_key in provider:
        key = _canonical_key(raw_key)
        if key in FORBIDDEN_PROVIDER_KEYS:
            issues.append(f"{label}.{key} transport override must be absent")

    entries = provider.get("api-key-entries")
    if not isinstance(entries, list) or len(entries) != 1:
        count = len(entries) if isinstance(entries, list) else "invalid"
        issues.append(
            f"{label}.api-key-entries must contain exactly one entry; count={count}"
        )
    else:
        entry = entries[0]
        if not isinstance(entry, dict) or set(entry) != {"api-key"}:
            issues.append(
                f"{label}.api-key-entries must contain only one api-key field"
            )
        elif not isinstance(entry.get("api-key"), str) or not entry["api-key"]:
            issues.append(f"{label}.api-key-entries[0].api-key must be non-empty")
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


def validate_config(config: Any) -> list[str]:
    """Return semantic policy violations; an empty list means valid."""

    issues: list[str] = []
    if not isinstance(config, dict):
        return ["config must be a mapping"]
    if ROUTE_MANIFEST_ERROR is not None:
        issues.append(f"unable to read provider route manifest: {ROUTE_MANIFEST_ERROR}")
    issues.extend(_MANIFEST_ISSUES)

    for key, expected in EXPECTED_TOP_LEVEL.items():
        actual = config.get(key)
        if not _same_value(actual, expected):
            issues.append(f"{key}={actual!r}; expected {expected!r}")

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

    codex = config.get("codex")
    if not isinstance(codex, dict):
        issues.append("codex must be a mapping")
    else:
        for key, expected in EXPECTED_CODEX.items():
            actual = codex.get(key)
            if not _same_value(actual, expected):
                issues.append(f"codex.{key}={actual!r}; expected {expected!r}")

    oauth_exclusions = config.get("oauth-excluded-models")
    if not isinstance(oauth_exclusions, dict):
        issues.append("oauth-excluded-models must be a mapping")
    else:
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
            if blocked_oauth_routes:
                issues.append(
                    "oauth-excluded-models.codex must leave configured OAuth "
                    f"routes available; blocked aliases={blocked_oauth_routes!r}"
                )

    codex_api_keys = config.get("codex-api-key", [])
    if codex_api_keys is not None and not isinstance(codex_api_keys, list):
        issues.append("codex-api-key must be a list when present")
    elif isinstance(codex_api_keys, list):
        for index, provider in enumerate(codex_api_keys):
            label = f"codex-api-key[{index}]"
            if not isinstance(provider, dict):
                issues.append(f"{label} must be a mapping")
                continue
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

    compatibility = config.get("openai-compatibility")
    if not isinstance(compatibility, list):
        issues.append("openai-compatibility must be a list")
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
        for host, expected_models in EXPECTED_PROVIDER_MODELS.items():
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
            issues.extend(_provider_transport_issues(provider, label))
            actual_models = _provider_models(provider)
            expected_map = EXPECTED_PROVIDER_MODEL_MAP[host]
            if actual_models != expected_map:
                issues.append(
                    f"{label}.models={actual_models!r}; expected {expected_map!r}"
                )
            expected_url = EXPECTED_PROVIDER_URLS.get(host)
            if expected_url is not None and _provider_url(provider) != expected_url:
                issues.append(
                    f"{label}.base-url must be exact {expected_url!r} "
                    "(no userinfo/query/fragment; HTTP is restricted to the authorized slot 3 relay)"
                )

    _walk_nested_overrides(config, (), issues)
    return issues


def validate_file(path: str | Path) -> list[str]:
    try:
        config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - exercised by remote diagnostics
        return [f"unable to read config: {type(exc).__name__}"]
    return validate_config(config)


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

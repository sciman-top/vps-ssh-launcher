"""Validate the BWG CPA semantic safety policy without exposing secrets."""

from __future__ import annotations

import sys
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

EXPECTED_CHANNEL_HOST = "ai.input.im"
LEGACY_CHANNEL_HOST = "35.213.82.91"
EXPECTED_PROVIDER_MODELS = {
    EXPECTED_CHANNEL_HOST: {"gpt-5.6-sol", "gpt-5.6-terra"},
    "open.bigmodel.cn": {"glm-5.3-flash"},
    "api.deepseek.com": {"deepseek-flash"},
}
EXPECTED_PROVIDER_URLS = {
    EXPECTED_CHANNEL_HOST: "https://ai.input.im/v1",
    "open.bigmodel.cn": "https://open.bigmodel.cn/api/coding/paas/v4",
    "api.deepseek.com": "https://api.deepseek.com",
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


def _provider_models(provider: Any) -> set[str]:
    if not isinstance(provider, dict) or not isinstance(provider.get("models"), list):
        return set()
    models: set[str] = set()
    for item in provider["models"]:
        if not isinstance(item, dict):
            continue
        model = item.get("alias") or item.get("name")
        if isinstance(model, str) and model:
            models.add(model)
    return models


def _provider_url(provider: Any) -> str | None:
    if not isinstance(provider, dict):
        return None
    try:
        parsed = urlparse(str(provider.get("base-url", "")))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            return None
        path = parsed.path.rstrip("/")
    except ValueError:
        return None
    return f"https://{parsed.hostname.lower()}{path}"


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
        issues.append(f"{label}.api-key-entries must contain exactly one entry; count={count}")
    else:
        entry = entries[0]
        if not isinstance(entry, dict) or set(entry) != {"api-key"}:
            issues.append(f"{label}.api-key-entries must contain only one api-key field")
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

    compatibility = config.get("openai-compatibility")
    if not isinstance(compatibility, list):
        issues.append("openai-compatibility must be a list")
    else:
        by_host: dict[str, list[dict[str, Any]]] = {}
        for item in compatibility:
            host = _provider_host(item)
            if host:
                by_host.setdefault(host, []).append(item)
            if host == LEGACY_CHANNEL_HOST or (
                isinstance(item, dict) and item.get("name") == "relay-8003"
            ):
                issues.append(
                    "legacy 35.213.82.91:8003/relay-8003 provider must be removed"
                )
        for host, expected_models in EXPECTED_PROVIDER_MODELS.items():
            entries = by_host.get(host, [])
            if len(entries) != 1:
                issues.append(
                    f"openai-compatibility must contain exactly one {host} provider"
                )
                continue
            provider = entries[0]
            if provider.get("disabled") is True:
                issues.append(f"openai-compatibility.{host}.disabled must be absent or false")
            label = f"openai-compatibility.{host}"
            issues.extend(_provider_transport_issues(provider, label))
            actual_models = _provider_models(provider)
            if actual_models != expected_models:
                issues.append(
                    f"{label}.models={sorted(actual_models)!r}; "
                    f"expected {sorted(expected_models)!r}"
                )
            expected_url = EXPECTED_PROVIDER_URLS.get(host)
            if expected_url is not None and _provider_url(provider) != expected_url:
                issues.append(
                    f"{label}.base-url must be exact {expected_url!r} "
                    "(https, default port, no userinfo/query/fragment)"
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

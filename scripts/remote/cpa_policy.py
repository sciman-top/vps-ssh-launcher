"""Validate the BWG CPA semantic safety policy without exposing secrets."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


EXPECTED_TOP_LEVEL: dict[str, Any] = {
    "host": "127.0.0.1",
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
}

EXPECTED_QUOTA = {
    "switch-project": False,
    "switch-preview-model": False,
    "antigravity-credits": False,
}


def _canonical_key(key: Any) -> str:
    return str(key).strip().replace("_", "-")


def _same_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return type(actual) is bool and actual is expected
    if isinstance(expected, int):
        return type(actual) is int and actual == expected
    return actual == expected


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
    if (
        not isinstance(codex, dict)
        or codex.get("stream-bootstrap-buffering") is not True
    ):
        issues.append("codex.stream-bootstrap-buffering must be true")

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

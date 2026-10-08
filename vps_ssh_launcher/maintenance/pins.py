"""Pure pin validation shared by resource policies and command builders."""

from __future__ import annotations

import re
from typing import Any

_VERSION_RE = re.compile(r"^(?:v)?([0-9]+\.[0-9]+\.[0-9]+)$")
_HEX_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CPA_MARKERS = ("cliproxyapi", "cli-proxy-api")


def normalize_version(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Xray version pin must be a string like 26.3.27.")
    match = _VERSION_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError("Xray version pin must be a semantic numeric version.")
    return match.group(1)


def normalize_sha256(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("SHA-256 pin must contain 64 hex characters.")
    normalized = value.strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.removeprefix("sha256:")
    if _HEX_SHA256_RE.fullmatch(normalized) is None:
        raise ValueError("SHA-256 pin must contain 64 hex characters.")
    return normalized


def normalize_digest(value: Any) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value.strip()) is None:
        raise ValueError(
            "Docker digest pin must be sha256: followed by 64 hex characters."
        )
    return value.strip().lower()


def normalize_compose_file(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Docker compose_file must be an absolute POSIX path.")
    path = value.strip()
    lowered = path.lower().replace("\\", "/")
    if (
        not path.startswith("/")
        or "\x00" in path
        or any(part == ".." for part in path.split("/"))
        or any(marker in lowered for marker in _CPA_MARKERS)
    ):
        raise ValueError(
            "Docker compose_file must be absolute and must not target CPA."
        )
    return path


def normalize_services(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("Docker services must be a non-empty array.")
    services: list[str] = []
    for service in value:
        if not isinstance(service, str) or _SERVICE_RE.fullmatch(service) is None:
            raise ValueError("Docker service names must be simple allowlisted tokens.")
        if any(marker in service.lower() for marker in _CPA_MARKERS):
            raise ValueError("The generic Docker adapter refuses CPA services.")
        services.append(service)
    if len(set(services)) != len(services):
        raise ValueError("Docker services must not contain duplicates.")
    return tuple(sorted(services))


def normalize_digests(value: Any, *, services: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Docker digests must be a service-to-digest table.")
    if set(value) != set(services):
        raise ValueError("Docker digests must cover exactly the allowlisted services.")
    return {service: normalize_digest(value[service]) for service in services}

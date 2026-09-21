"""Stable, redaction-friendly fingerprints for maintenance state."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint(value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def fingerprint_without_keys(value: Any, *, excluded: set[str]) -> str:
    def strip(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: strip(child) for key, child in item.items() if key not in excluded
            }
        if isinstance(item, list):
            return [strip(child) for child in item]
        return item

    return fingerprint(strip(value))

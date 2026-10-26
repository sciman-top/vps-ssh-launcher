"""Stable, redaction-friendly fingerprints for maintenance state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence, Set as AbstractSet
from typing import Any

from .models import InventoryRecord

VOLATILE_FACT_KEYS = frozenset({"root_disk_used_percent", "memory_mb"})


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


def fingerprint_without_keys(value: Any, *, excluded: AbstractSet[str]) -> str:
    def strip(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: strip(child) for key, child in item.items() if key not in excluded
            }
        if isinstance(item, list):
            return [strip(child) for child in item]
        return item

    return fingerprint(strip(value))


def inventory_fingerprint(records: Sequence[InventoryRecord]) -> str:
    """Fingerprint inventory identity facts without importing SSH collection."""
    seen_profiles: set[str] = set()
    for record in records:
        profile = record.profile.strip()
        if not profile:
            raise ValueError("Inventory records require a non-empty profile name.")
        if profile in seen_profiles:
            raise ValueError(f"Inventory contains duplicate profile: {profile}")
        seen_profiles.add(profile)
    serialized = [record.to_dict() for record in records]
    return fingerprint_without_keys(serialized, excluded=VOLATILE_FACT_KEYS)

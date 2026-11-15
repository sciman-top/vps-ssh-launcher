"""Serialization and validation seams for persisted maintenance plans."""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from .models import PLAN_STATUSES, MaintenancePlan

_PLAN_ID_RE = re.compile(r"^plan-[0-9a-f]{16}$")


def validate_plan_id(value: Any) -> str:
    """Validate the database key before it can reach a filesystem or query."""
    if not isinstance(value, str) or _PLAN_ID_RE.fullmatch(value) is None:
        raise ValueError("Stored maintenance plan id has an invalid format.")
    return value


def serialize_plan(plan: MaintenancePlan) -> str:
    """Return the canonical JSON representation used by SQLite."""
    return json.dumps(plan.to_dict(), ensure_ascii=True, sort_keys=True)


def plan_from_dict(value: dict[str, Any]) -> MaintenancePlan:
    """Rehydrate and validate one decoded plan document."""
    status = value.get("status")
    if not isinstance(status, str) or status not in PLAN_STATUSES:
        raise ValueError("Stored maintenance plan status is invalid.")
    plan = MaintenancePlan.from_dict(value)
    validate_plan_id(plan.plan_id)
    return plan


def plan_from_json(serialized: str) -> MaintenancePlan:
    """Decode one stored plan and preserve the storage-layer diagnostics."""
    value = json.loads(serialized)
    if not isinstance(value, dict):
        raise ValueError("Stored plan root is invalid.")
    return plan_from_dict(value)


def plan_from_row(row: Sequence[Any]) -> MaintenancePlan:
    """Decode ``(plan_id, plan_json)`` and verify the key matches the payload."""
    if len(row) < 2:
        raise ValueError("Stored maintenance plan row is incomplete.")
    row_plan_id = validate_plan_id(str(row[0]))
    plan = plan_from_json(str(row[1]))
    if plan.plan_id != row_plan_id:
        raise ValueError("Stored plan id does not match its database key.")
    return plan

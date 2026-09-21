"""Redacted local receipts for maintenance decisions."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import MaintenancePlan


def build_receipt(
    plan: MaintenancePlan,
    *,
    outcome: str,
    reason: str,
    receipt_id: str | None = None,
) -> dict[str, Any]:
    status_counts = Counter(action.status for action in plan.actions)
    return {
        "receipt_id": receipt_id or plan.plan_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": "maintenance-control-plane",
        "outcome": outcome,
        "reason": reason,
        "plan_id": plan.plan_id,
        "policy_fingerprint": plan.policy_fingerprint,
        "inventory_fingerprint": plan.inventory_fingerprint,
        "plan_status": plan.status,
        "profiles": sorted({action.profile for action in plan.actions}),
        "action_count": len(plan.actions),
        "status_counts": dict(sorted(status_counts.items())),
        "sensitive_values_omitted": True,
    }


def write_receipt(
    receipt_dir: Path,
    plan: MaintenancePlan,
    *,
    outcome: str,
    reason: str,
) -> Path:
    receipt_dir = receipt_dir.expanduser()
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = build_receipt(plan, outcome=outcome, reason=reason)
    name = (
        f"{receipt['created_at'].replace('+00:00', 'Z').replace(':', '')}"
        f"-{plan.plan_id}.json"
    )
    destination = receipt_dir / name
    handle, temporary = tempfile.mkstemp(
        prefix=f".{plan.plan_id}-",
        suffix=".tmp",
        dir=receipt_dir,
        text=True,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination

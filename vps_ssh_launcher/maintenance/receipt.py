"""Redacted local receipts for maintenance decisions."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import MaintenancePlan
from .security import redact_evidence
from ..safe_files import atomic_write_json


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
        "reason": redact_evidence(reason),
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
        f"-{plan.plan_id}-{uuid.uuid4().hex[:8]}.json"
    )
    destination = receipt_dir / name
    atomic_write_json(destination, receipt, replace_existing=False)
    return destination

"""Redacted local receipts for maintenance decisions."""

from __future__ import annotations

import os
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
        # A plan accumulates several receipts (dry_run, refused, verified ...).
        # Keying them by plan_id made every receipt of one plan share an id, so
        # any consumer that deduplicated by receipt_id merged distinct events.
        "receipt_id": receipt_id or f"receipt-{uuid.uuid4().hex}",
        "plan_id": plan.plan_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": "maintenance-control-plane",
        "outcome": outcome,
        "reason": redact_evidence(reason),
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
    # A bare mkdir yields 0777&~umask; write_receipt is the first thing a fresh
    # host runs, so create it 0700 and verify instead of shipping receipts from
    # a directory the next atomic write would refuse.
    receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(receipt_dir, 0o700)
        except OSError as exc:
            raise ValueError(
                f"Unable to restrict the maintenance receipt directory: {receipt_dir}"
            ) from exc
    receipt = build_receipt(plan, outcome=outcome, reason=reason)
    name = (
        f"{receipt['created_at'].replace('+00:00', 'Z').replace(':', '')}"
        f"-{plan.plan_id}-{uuid.uuid4().hex[:8]}.json"
    )
    destination = receipt_dir / name
    atomic_write_json(destination, receipt, replace_existing=False)
    return destination

"""Plan persistence facade for the maintenance control plane."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import MaintenancePlan
from .state_codec import (
    plan_from_json as _plan_from_json,
    plan_from_row as _plan_from_row,
    serialize_plan as _serialize_plan,
    validate_plan_id as _validate_plan_id,
)
from .state_automation import (
    load_automation_target as load_automation_target,
    record_automation_attempt as record_automation_attempt,
    record_automation_outcome as record_automation_outcome,
    record_remote_start_and_mark_applied as record_remote_start_and_mark_applied,
    refund_automation_attempt as refund_automation_attempt,
    restore_unattended_busy_plan as restore_unattended_busy_plan,
)
from .state_store import connect as _connect
from .transitions import is_backward_transition as _is_backward_transition


def save_plan(
    path: Path,
    plan: MaintenancePlan,
    *,
    receipt_path: Path | None = None,
) -> None:
    plan_id = _validate_plan_id(plan.plan_id)
    serialized = _serialize_plan(plan)
    with _connect(path) as connection:
        target = connection.execute(
            "SELECT plan_json FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if target is not None:
            try:
                previous = _plan_from_json(str(target[0]))
            except ValueError:
                connection.rollback()
                raise
            # Only a BACKWARD transition is refused; forward progress must be
            # persisted. The old rule keyed off plan.status, and workflow marks
            # the plan "applied" before the adapter runs (deliberately, so a
            # crash cannot make the action retryable) -- so the terminal
            # verified/unverified/rolled_back write was silently dropped and the
            # stored plan stayed "applied" with no receipt pointer forever.
            if _is_backward_transition(previous, plan):
                connection.commit()
                return
        connection.execute(
            """
            INSERT INTO plans(plan_id, created_at, status, plan_json, receipt_path)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
              created_at=excluded.created_at,
              status=excluded.status,
              plan_json=excluded.plan_json,
              receipt_path=excluded.receipt_path
            """,
            (
                plan_id,
                plan.created_at,
                plan.status,
                serialized,
                str(receipt_path) if receipt_path else None,
            ),
        )
        connection.commit()


def record_plan_receipt(path: Path, *, plan_id: str, receipt_path: Path) -> None:
    """Attach a receipt to a stored plan without rewriting its execution state.

    The refusal path used to call an unconditional ``save_plan`` upsert,
    which overwrites ``plan_json``. That made an unlocked
    ``apply --yes`` (a dry run that takes no local lock) able to rewind a
    concurrent locked run that had just marked its action ``applied`` --
    resurrecting an action whose remote write was already in flight.
    """
    plan_id = _validate_plan_id(plan_id)
    with _connect(path) as connection:
        cursor = connection.execute(
            "UPDATE plans SET receipt_path = ? WHERE plan_id = ?",
            (str(receipt_path), plan_id),
        )
        connection.commit()
    if cursor.rowcount != 1:
        raise ValueError("Stored maintenance plan disappeared during receipt update.")


def save_plan_if_absent(
    path: Path, plan: MaintenancePlan
) -> tuple[MaintenancePlan, bool]:
    """Store a deterministic plan; report whether this call created the row.

    The caller needs that bit to compensate only a plan it just published, so
    it is returned here instead of being re-derived with a history scan.
    """

    plan = MaintenancePlan.from_dict(
        replace(
            plan,
            may_refund_unattended_attempt=True,
        ).to_dict()
    )
    plan_id = _validate_plan_id(plan.plan_id)
    serialized = _serialize_plan(plan)
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO plans(plan_id, created_at, status, plan_json)
            VALUES (?, ?, ?, ?)
            """,
            (plan_id, plan.created_at, plan.status, serialized),
        )
        if cursor.rowcount == 1:
            # A newly stored plan has not yet consumed an automation attempt.
            connection.commit()
            return plan, True
        row = connection.execute(
            "SELECT plan_id, plan_json FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Stored maintenance plan could not be read after save.")
        existing = _plan_from_row(row)
        if (
            existing.policy_fingerprint != plan.policy_fingerprint
            or existing.inventory_fingerprint != plan.inventory_fingerprint
            or len(existing.actions) != len(plan.actions)
            or any(
                (
                    current.profile,
                    current.resource,
                    current.desired,
                    current.observed,
                    current.target,
                )
                != (
                    requested.profile,
                    requested.resource,
                    requested.desired,
                    requested.observed,
                    requested.target,
                )
                for current, requested in zip(
                    existing.actions, plan.actions, strict=True
                )
            )
        ):
            raise ValueError(
                "Stored plan id is already bound to a different maintenance intent."
            )
        # A fresh inventory renews an unexecuted plan's review window. Execution
        # outcomes and their timestamps must survive deterministic replanning.
        if existing.status in {"ready", "planned", "blocked"} and all(
            current.status == requested.status
            for current, requested in zip(existing.actions, plan.actions, strict=True)
        ):
            existing = replace(existing, created_at=plan.created_at)
            connection.execute(
                "UPDATE plans SET created_at = ?, plan_json = ? WHERE plan_id = ?",
                (
                    existing.created_at,
                    _serialize_plan(existing),
                    plan_id,
                ),
            )
        connection.commit()
    return existing, False


def load_plan(path: Path, plan_id: str | None = None) -> MaintenancePlan:
    with _connect(path) as connection:
        if plan_id is None:
            row = connection.execute(
                "SELECT plan_id, plan_json FROM plans ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT plan_id, plan_json FROM plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
    if row is None:
        raise ValueError("No stored maintenance plan was found.")
    return _plan_from_row(row)


def delete_plan_if_pristine(
    path: Path,
    *,
    plan_id: str,
    receipt_path: Path | None,
) -> bool:
    """Compensate a failed plan publication only if no remote execution touched it."""
    plan_id = _validate_plan_id(plan_id)
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT plan_json, receipt_path FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            connection.commit()
            return True
        stored = _plan_from_json(str(row[0]))
        actual_receipt = str(row[1]) if row[1] is not None else None
        expected_receipt = str(receipt_path) if receipt_path is not None else None
        if (receipt_path is not None and actual_receipt != expected_receipt) or any(
            action.status != "planned" for action in stored.actions
        ):
            connection.commit()
            return False
        connection.execute("DELETE FROM plans WHERE plan_id = ?", (plan_id,))
        connection.commit()
    return True


def list_plans(path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("History limit must be positive.")
    with _connect(path) as connection:
        rows = connection.execute(
            """
            SELECT plan_id, created_at, status, receipt_path
            FROM plans
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "plan_id": str(row[0]),
            "created_at": str(row[1]),
            "status": str(row[2]),
            "receipt_path": str(row[3]) if row[3] is not None else None,
        }
        for row in rows
    ]

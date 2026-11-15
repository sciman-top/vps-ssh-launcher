"""Persistence operations for unattended maintenance attempts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import MaintenancePlan
from .state_codec import plan_from_json, validate_plan_id
from .state_store import (
    book_automation_attempt,
    connect,
    update_stored_plan,
)
from .transitions import updated_plan


def record_remote_start_and_mark_applied(
    path: Path,
    *,
    plan_id: str,
    profile: str,
    resource: str,
    pin_digest: str,
    attempted_at: str | None = None,
) -> None:
    """Atomically book a remote attempt and make its action retry-ineligible."""
    plan_id = validate_plan_id(plan_id)
    timestamp = attempted_at or datetime.now(timezone.utc).isoformat()
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT plan_json FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Maintenance plan no longer exists.")
        stored = plan_from_json(str(row[0]))
        matches = [
            action
            for action in stored.actions
            if action.profile == profile
            and action.resource == resource
            and action.status in {"planned", "applied"}
        ]
        if len(matches) != 1:
            raise ValueError("Remote-start action cannot be mapped safely.")
        index = stored.actions.index(matches[0])
        started = updated_plan(
            replace(
                stored,
                may_refund_unattended_attempt=True,
            ),
            action_index=index,
            status="applied",
            reason=(
                "Remote adapter started; outcome remains unresolved until its "
                "success or rollback marker is verified."
            ),
        )
        update_stored_plan(connection, started)
        book_automation_attempt(
            connection,
            profile=profile,
            resource=resource,
            pin_fingerprint=pin_digest,
            timestamp=timestamp,
            plan_id=plan_id,
        )
        connection.commit()


def restore_unattended_busy_plan(
    path: Path,
    *,
    plan_id: str,
    action_profile: str,
    action_resource: str,
    preserve_refund: bool = False,
) -> MaintenancePlan:
    """Restore a busy adapter's provisional root after a local interruption."""
    plan_id = validate_plan_id(plan_id)
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT plan_json FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Busy maintenance plan no longer exists.")
        stored = plan_from_json(str(row[0]))
        matches = [
            action
            for action in stored.actions
            if action.profile == action_profile and action.resource == action_resource
        ]
        if len(matches) != 1 or matches[0].status not in {"planned", "applied"}:
            raise ValueError(
                "Busy adapter attempt cannot be safely mapped to its stored plan action."
            )
        root_may_refund = (
            stored.may_refund_unattended_attempt if preserve_refund else False
        )
        restored = updated_plan(
            replace(
                stored,
                may_refund_unattended_attempt=root_may_refund,
            ),
            action_index=stored.actions.index(matches[0]),
            status="planned",
            reason=(
                "Host busy before adapter mutation; safe to retry after the "
                "remote lock is released."
            ),
        )
        update_stored_plan(connection, restored)
        connection.commit()
    return restored


def load_automation_target(
    path: Path,
    *,
    profile: str,
    resource: str,
    pin_fingerprint: str,
) -> dict[str, Any] | None:
    with connect(path) as connection:
        row = connection.execute(
            """
            SELECT attempt_count, last_attempt_at, last_outcome, last_plan_id
            FROM automation_targets
            WHERE profile = ? AND resource = ? AND pin_fingerprint = ?
            """,
            (profile, resource, pin_fingerprint),
        ).fetchone()
    if row is None:
        return None
    return {
        "profile": profile,
        "resource": resource,
        "pin_fingerprint": pin_fingerprint,
        "attempt_count": int(row[0]),
        "last_attempt_at": str(row[1]),
        "last_outcome": str(row[2]),
        "last_plan_id": str(row[3]),
    }


def record_automation_attempt(
    path: Path,
    *,
    profile: str,
    resource: str,
    pin_fingerprint: str,
    plan_id: str,
    attempted_at: str | None = None,
) -> int:
    timestamp = attempted_at or datetime.now(timezone.utc).isoformat()
    with connect(path) as connection:
        book_automation_attempt(
            connection,
            profile=profile,
            resource=resource,
            pin_fingerprint=pin_fingerprint,
            timestamp=timestamp,
            plan_id=plan_id,
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT attempt_count FROM automation_targets
            WHERE profile = ? AND resource = ? AND pin_fingerprint = ?
            """,
            (profile, resource, pin_fingerprint),
        ).fetchone()
    if row is None:
        raise RuntimeError("Automation attempt state was not persisted.")
    return int(row[0])


def refund_automation_attempt(
    path: Path,
    *,
    profile: str,
    resource: str,
    pin_fingerprint: str,
    plan_id: str,
    attempted_at: str | None = None,
) -> None:
    """Undo one attempt booking for a host that was busy and wrote nothing."""
    del attempted_at
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT plan_json FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Busy maintenance plan no longer exists.")
        plan = plan_from_json(str(row[0]))
        if plan.may_refund_unattended_attempt is not True:
            raise ValueError("The current plan has no refundable unattended attempt.")
        update_stored_plan(
            connection,
            replace(
                plan,
                may_refund_unattended_attempt=False,
            ),
        )
        updated = connection.execute(
            """
            UPDATE automation_targets
            SET attempt_count = MAX(attempt_count - 1, 0),
                last_outcome = 'busy',
                last_plan_id = ?
            WHERE profile = ? AND resource = ? AND pin_fingerprint = ?
            """,
            (
                plan_id,
                profile,
                resource,
                pin_fingerprint,
            ),
        ).rowcount
        connection.commit()
    if updated != 1:
        raise RuntimeError("Automation attempt to refund was not persisted.")


def record_automation_outcome(
    path: Path,
    *,
    profile: str,
    resource: str,
    pin_fingerprint: str,
    outcome: str,
    plan_id: str,
    attempted_at: str | None = None,
) -> None:
    timestamp = attempted_at or datetime.now(timezone.utc).isoformat()
    with connect(path) as connection:
        updated = connection.execute(
            """
            UPDATE automation_targets
            SET last_attempt_at = ?, last_outcome = ?, last_plan_id = ?
            WHERE profile = ? AND resource = ? AND pin_fingerprint = ?
            """,
            (
                timestamp,
                outcome,
                plan_id,
                profile,
                resource,
                pin_fingerprint,
            ),
        ).rowcount
        connection.commit()
    if updated != 1:
        raise RuntimeError("Automation outcome has no recorded attempt.")

"""SQLite state for plans and redacted execution receipts."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import sys
from contextlib import contextmanager, suppress
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import (
    ACTION_STATUSES,
    PLAN_STATUSES,
    MaintenancePlan,
)
from .planner import updated_plan
from ..safe_files import open_private_file, prepare_private_parent

_PLAN_ID_RE = re.compile(r"^plan-[0-9a-f]{16}$")
_PRIVATE_FILE_MODE = 0o600


def _validate_plan_id(value: Any) -> str:
    if not isinstance(value, str) or _PLAN_ID_RE.fullmatch(value) is None:
        raise ValueError("Stored maintenance plan id has an invalid format.")
    return value


SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    receipt_path TEXT
);

CREATE TABLE IF NOT EXISTS automation_targets (
    profile TEXT NOT NULL,
    resource TEXT NOT NULL,
    pin_fingerprint TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    last_attempt_at TEXT NOT NULL,
    last_outcome TEXT NOT NULL,
    last_plan_id TEXT NOT NULL,
    PRIMARY KEY(profile, resource, pin_fingerprint)
);
"""


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    path = path.expanduser()
    prepare_private_parent(path)
    if os.name != "nt":
        descriptor = open_private_file(path, os.O_CREAT | os.O_RDWR)
        os.close(descriptor)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        connection.commit()
        # A SQLite side file may be created by the database process. Restrict
        # the main file (and any sidecars that already exist) at every open so
        # receipts' redaction promise also holds at the local storage boundary.
        if os.name != "nt":
            for candidate in (
                path,
                path.with_name(path.name + "-wal"),
                path.with_name(path.name + "-shm"),
            ):
                if candidate.exists() and candidate.is_file():
                    os.chmod(candidate, _PRIVATE_FILE_MODE)
                    if stat.S_IMODE(candidate.stat().st_mode) & 0o077:
                        raise ValueError(
                            f"Maintenance state is accessible to other users: {candidate}"
                        )
        yield connection
    finally:
        if sys.exc_info()[0] is not None:
            with suppress(Exception):
                connection.rollback()
        connection.close()


def save_plan(
    path: Path,
    plan: MaintenancePlan,
    *,
    receipt_path: Path | None = None,
    allow_invalid_root_update: bool = False,
) -> None:
    plan_id = _validate_plan_id(plan.plan_id)
    eligible = plan.may_refund_unattended_attempt
    stored = replace(
        plan,
        may_refund_unattended_attempt=eligible,
    )
    serialized = json.dumps(stored.to_dict(), ensure_ascii=True, sort_keys=True)
    with _connect(path) as connection:
        if allow_invalid_root_update:
            connection.execute(
                """
                INSERT INTO plans(
                    plan_id, created_at, status, plan_json, receipt_path
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    status=excluded.status,
                    plan_json=excluded.plan_json,
                    receipt_path=excluded.receipt_path
                """,
                (
                    plan_id,
                    stored.created_at,
                    stored.status,
                    serialized,
                    str(receipt_path) if receipt_path else None,
                ),
            )
            connection.commit()
            return
        target = connection.execute(
            "SELECT plan_json FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if target is not None:
            previous_value = json.loads(str(target[0]))
            if not isinstance(previous_value, dict):
                connection.rollback()
                raise ValueError("Stored plan root is invalid.")
            try:
                previous = _plan_from_dict(previous_value)
            except ValueError:
                connection.rollback()
                raise
            if previous.status not in {"planned", "ready", "blocked"}:
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
                stored.created_at,
                stored.status,
                serialized,
                str(receipt_path) if receipt_path else None,
            ),
        )
        connection.commit()


def save_plan_if_absent(path: Path, plan: MaintenancePlan) -> MaintenancePlan:
    """Create a deterministic plan without resetting an existing execution."""

    plan = MaintenancePlan.from_dict(
        replace(
            plan,
            may_refund_unattended_attempt=True,
        ).to_dict()
    )
    plan_id = _validate_plan_id(plan.plan_id)
    eligible = plan.may_refund_unattended_attempt
    stored = replace(plan, may_refund_unattended_attempt=eligible)
    serialized = json.dumps(stored.to_dict(), ensure_ascii=True, sort_keys=True)
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO plans(plan_id, created_at, status, plan_json)
            VALUES (?, ?, ?, ?)
            """,
            (plan_id, stored.created_at, stored.status, serialized),
        )
        if cursor.rowcount == 1:
            # A newly stored plan has not yet consumed an automation attempt.
            connection.commit()
            return stored
        row = connection.execute(
            "SELECT plan_id, plan_json FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Stored maintenance plan could not be read after save.")
        row_plan_id = _validate_plan_id(str(row[0]))
        value = json.loads(str(row[1]))
        if not isinstance(value, dict):
            raise ValueError("Stored plan root is invalid.")
        existing = _plan_from_dict(value)
        if existing.plan_id != row_plan_id:
            raise ValueError("Stored plan id does not match its database key.")
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
                    json.dumps(existing.to_dict(), ensure_ascii=True, sort_keys=True),
                    plan_id,
                ),
            )
        connection.commit()
    return existing


def _plan_from_dict(value: dict[str, Any]) -> MaintenancePlan:
    status = value.get("status")
    if not isinstance(status, str) or status not in PLAN_STATUSES:
        raise ValueError("Stored maintenance plan status is invalid.")
    actions = value.get("actions")
    if not isinstance(actions, list):
        raise ValueError("Stored plan actions are invalid.")
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("Stored plan action is invalid.")
        action_status = action.get("status")
        if not isinstance(action_status, str) or action_status not in ACTION_STATUSES:
            raise ValueError("Stored maintenance action status is invalid.")
    plan = MaintenancePlan.from_dict(value)
    _validate_plan_id(plan.plan_id)
    return plan


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
    row_plan_id = _validate_plan_id(str(row[0]))
    value = json.loads(str(row[1]))
    if not isinstance(value, dict):
        raise ValueError("Stored plan root is invalid.")
    plan = _plan_from_dict(value)
    if plan.plan_id != row_plan_id:
        raise ValueError("Stored plan id does not match its database key.")
    return plan


def _update_stored_plan(
    connection: sqlite3.Connection,
    plan: MaintenancePlan,
) -> None:
    plan_id = _validate_plan_id(plan.plan_id)
    cursor = connection.execute(
        """
        UPDATE plans
        SET created_at = ?, status = ?, plan_json = ?
        WHERE plan_id = ?
        """,
        (
            plan.created_at,
            plan.status,
            json.dumps(plan.to_dict(), ensure_ascii=True, sort_keys=True),
            plan_id,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError("Stored maintenance plan disappeared during update.")


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
    plan_id = _validate_plan_id(plan_id)
    timestamp = attempted_at or datetime.now(timezone.utc).isoformat()
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT plan_json FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Maintenance plan no longer exists.")
        value = json.loads(str(row[0]))
        if not isinstance(value, dict):
            raise ValueError("Stored plan root is invalid.")
        stored = _plan_from_dict(value)
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
        _update_stored_plan(connection, started)
        connection.execute(
            """
            INSERT INTO automation_targets(
                profile, resource, pin_fingerprint, attempt_count,
                last_attempt_at, last_outcome, last_plan_id
            ) VALUES (?, ?, ?, 1, ?, 'started', ?)
            ON CONFLICT(profile, resource, pin_fingerprint) DO UPDATE SET
                attempt_count = automation_targets.attempt_count + 1,
                last_attempt_at = excluded.last_attempt_at,
                last_outcome = excluded.last_outcome,
                last_plan_id = excluded.last_plan_id
            """,
            (profile, resource, pin_digest, timestamp, plan_id),
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
    """Restore a busy adapter's provisional root after any earlier local interruption."""
    plan_id = _validate_plan_id(plan_id)
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT plan_json FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Busy maintenance plan no longer exists.")
        value = json.loads(str(row[0]))
        if not isinstance(value, dict):
            raise ValueError("Stored plan root is invalid.")
        stored = _plan_from_dict(value)
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
        _update_stored_plan(connection, restored)
        connection.commit()
    return restored


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
        value = json.loads(str(row[0]))
        if not isinstance(value, dict):
            raise ValueError("Stored plan root is invalid.")
        stored = _plan_from_dict(value)
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


def load_automation_target(
    path: Path,
    *,
    profile: str,
    resource: str,
    pin_fingerprint: str,
) -> dict[str, Any] | None:
    with _connect(path) as connection:
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
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO automation_targets(
                profile, resource, pin_fingerprint, attempt_count,
                last_attempt_at, last_outcome, last_plan_id
            ) VALUES (?, ?, ?, 1, ?, 'started', ?)
            ON CONFLICT(profile, resource, pin_fingerprint) DO UPDATE SET
                attempt_count = automation_targets.attempt_count + 1,
                last_attempt_at = excluded.last_attempt_at,
                last_outcome = excluded.last_outcome,
                last_plan_id = excluded.last_plan_id
            """,
            (profile, resource, pin_fingerprint, timestamp, plan_id),
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
    """Undo one attempt booking for a host that was busy and wrote nothing.

    A ``MAINTENANCE_BUSY`` adapter exit means the flock was held elsewhere and
    no remote change was attempted, so the pinned target must not lose an
    unattended attempt slot to a transient collision. The plan and attempt
    refund are one transaction; a stale plan cannot refund an old attempt.
    """
    timestamp = attempted_at or datetime.now(timezone.utc).isoformat()
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT plan_json FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Busy maintenance plan no longer exists.")
        value = json.loads(str(row[0]))
        if not isinstance(value, dict):
            raise ValueError("Stored plan root is invalid.")
        plan = _plan_from_dict(value)
        if plan.may_refund_unattended_attempt is not True:
            raise ValueError("The current plan has no refundable unattended attempt.")
        _update_stored_plan(
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
                last_attempt_at = ?,
                last_outcome = 'busy',
                last_plan_id = ?
            WHERE profile = ? AND resource = ? AND pin_fingerprint = ?
            """,
            (
                timestamp,
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
    with _connect(path) as connection:
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

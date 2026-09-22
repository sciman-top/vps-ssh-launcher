"""SQLite state for plans and redacted execution receipts."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, cast

from .models import ActionStatus, MaintenanceAction, MaintenancePlan

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
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        connection.commit()
        yield connection
    finally:
        connection.close()


def save_plan(
    path: Path,
    plan: MaintenancePlan,
    *,
    receipt_path: Path | None = None,
) -> None:
    with _connect(path) as connection:
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
                plan.plan_id,
                plan.created_at,
                plan.status,
                json.dumps(plan.to_dict(), ensure_ascii=True, sort_keys=True),
                str(receipt_path) if receipt_path else None,
            ),
        )
        connection.commit()


def _plan_from_dict(value: dict[str, Any]) -> MaintenancePlan:
    actions_raw = value.get("actions")
    if not isinstance(actions_raw, list):
        raise ValueError("Stored plan actions are invalid.")
    actions = []
    for item in actions_raw:
        if not isinstance(item, dict):
            raise ValueError("Stored plan action is invalid.")
        actions.append(
            MaintenanceAction(
                profile=str(item["profile"]),
                resource=str(item["resource"]),
                desired=str(item["desired"]),
                observed=str(item["observed"]),
                status=cast(ActionStatus, str(item["status"])),
                reason=str(item["reason"]),
                target=(
                    str(item["target"]) if item.get("target") is not None else None
                ),
            )
        )
    return MaintenancePlan(
        plan_id=str(value["plan_id"]),
        created_at=str(value["created_at"]),
        policy_fingerprint=str(value["policy_fingerprint"]),
        inventory_fingerprint=str(value["inventory_fingerprint"]),
        actions=tuple(actions),
        status=str(value["status"]),
    )


def load_plan(path: Path, plan_id: str | None = None) -> MaintenancePlan:
    with _connect(path) as connection:
        if plan_id is None:
            row = connection.execute(
                "SELECT plan_json FROM plans ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT plan_json FROM plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
    if row is None:
        raise ValueError("No stored maintenance plan was found.")
    value = json.loads(str(row[0]))
    if not isinstance(value, dict):
        raise ValueError("Stored plan root is invalid.")
    return _plan_from_dict(value)


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

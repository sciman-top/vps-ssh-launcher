"""SQLite connection and write helpers shared by maintenance state modules."""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Iterator

from ..safe_files import open_private_file, prepare_private_parent
from .models import MaintenancePlan
from .state_codec import serialize_plan, validate_plan_id

_PRIVATE_FILE_MODE = 0o600

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
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """Open the private state database and normalize its sidecar permissions."""
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


def update_stored_plan(
    connection: sqlite3.Connection,
    plan: MaintenancePlan,
) -> None:
    """Update one existing plan row inside the caller's transaction."""
    plan_id = validate_plan_id(plan.plan_id)
    cursor = connection.execute(
        """
        UPDATE plans
        SET created_at = ?, status = ?, plan_json = ?
        WHERE plan_id = ?
        """,
        (
            plan.created_at,
            plan.status,
            serialize_plan(plan),
            plan_id,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError("Stored maintenance plan disappeared during update.")


def book_automation_attempt(
    connection: sqlite3.Connection,
    *,
    profile: str,
    resource: str,
    pin_fingerprint: str,
    timestamp: str,
    plan_id: str,
) -> None:
    """Upsert one automation attempt row; the caller owns the transaction."""
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

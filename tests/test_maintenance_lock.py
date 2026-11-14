"""Contention and stale-owner recovery for the unattended maintenance lock.

The lock is what stops two scheduled runs from writing the same host. Its
contended, stale and legacy paths were only reachable on a live collision, so
they are driven here with real files and real OS locks.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from vps_ssh_launcher.maintenance import automation


def _owner(*, pid: int, token: str | None, age_minutes: int = 0) -> dict[str, object]:
    created = (datetime.now().astimezone() - timedelta(minutes=age_minutes)).isoformat()
    owner: dict[str, object] = {"pid": pid, "created_at": created}
    if token is not None:
        owner["token"] = token
    return owner


def _write(path: Path, owner: dict[str, object]) -> None:
    path.write_text(json.dumps(owner), encoding="utf-8")


class LockContentionTests(unittest.TestCase):
    def test_a_second_holder_is_refused_while_the_first_is_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.lock"
            with automation.unattended_lock(path):
                with self.assertRaisesRegex(ValueError, "Another unattended"):
                    with automation.unattended_lock(path):
                        self.fail("a second holder must not enter")

    def test_the_lock_file_is_released_after_a_normal_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.lock"
            with automation.unattended_lock(path):
                self.assertTrue(path.exists())
            self.assertFalse(path.exists())
            with automation.unattended_lock(path):
                self.assertTrue(path.exists())

    def test_the_lock_file_is_released_after_an_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.lock"
            with self.assertRaises(RuntimeError):
                with automation.unattended_lock(path):
                    raise RuntimeError("body failed")
            with automation.unattended_lock(path):
                self.assertTrue(path.exists())

    def test_the_guard_inode_survives_so_a_replacement_cannot_be_locked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.lock"
            with automation.unattended_lock(path):
                guard = path.with_name(path.name + ".guard")
                self.assertTrue(guard.exists())
            self.assertTrue(guard.exists())


class StaleOwnerRecoveryTests(unittest.TestCase):
    def test_a_dead_owner_past_the_stale_window_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.lock"
            dead = 2**31 - 1
            _write(path, _owner(pid=dead, token="a" * 32, age_minutes=600))
            with automation.unattended_lock(path, stale_after_minutes=1):
                current = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotEqual(current["token"], "a" * 32)

    def test_a_live_owner_is_never_recovered_even_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.lock"
            _write(path, _owner(pid=os.getpid(), token="b" * 32, age_minutes=600))
            with self.assertRaisesRegex(ValueError, "Another unattended"):
                with automation.unattended_lock(path, stale_after_minutes=1):
                    self.fail("a live owner must not be displaced")

    def test_a_legacy_owner_without_a_token_is_preserved(self) -> None:
        # A token-less owner cannot be fenced: replacing it could let two runs
        # believe they hold the lock. It must stay for manual review.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.lock"
            _write(path, _owner(pid=2**31 - 1, token=None, age_minutes=600))
            with self.assertRaisesRegex(ValueError, "fencing token"):
                with automation.unattended_lock(path, stale_after_minutes=1):
                    self.fail("a token-less owner must not be displaced")
            self.assertTrue(path.exists())

    def test_an_unreadable_owner_is_refused_rather_than_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.lock"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unreadable"):
                with automation.unattended_lock(path, stale_after_minutes=1):
                    self.fail("an unreadable owner must not be displaced")
            self.assertTrue(path.exists())

    def test_the_owner_file_is_created_private(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode bits only")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.lock"
            with automation.unattended_lock(path):
                mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)


class OwnerFileValidationTests(unittest.TestCase):
    def test_a_non_positive_pid_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.json"
            _write(
                path, {"pid": 0, "created_at": datetime.now().astimezone().isoformat()}
            )
            with self.assertRaisesRegex(ValueError, "unreadable"):
                automation._read_lock_owner(path)

    def test_a_malformed_token_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.json"
            _write(path, _owner(pid=1, token="NOT-HEX"))
            with self.assertRaisesRegex(ValueError, "unreadable"):
                automation._read_lock_owner(path)

    def test_a_naive_created_at_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.json"
            _write(path, {"pid": 1, "created_at": "2026-11-14T12:00:00"})
            with self.assertRaisesRegex(ValueError, "unreadable"):
                automation._read_lock_owner(path)

    def test_an_unknown_key_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.json"
            _write(path, {**_owner(pid=1, token="c" * 32), "extra": 1})
            with self.assertRaisesRegex(ValueError, "unreadable"):
                automation._read_lock_owner(path)


class RestoreTests(unittest.TestCase):
    def test_a_moved_owner_is_restored_when_the_path_is_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "owner.moved"
            path = Path(directory) / "owner.json"
            _write(candidate, _owner(pid=1, token="d" * 32))
            automation.restore_moved_owner(path, candidate)
            self.assertTrue(path.exists())

    def test_a_successor_that_already_occupies_the_path_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "owner.moved"
            path = Path(directory) / "owner.json"
            _write(candidate, _owner(pid=1, token="e" * 32))
            _write(path, _owner(pid=2, token="f" * 32))
            automation.restore_moved_owner(path, candidate)
            # Restoring must not overwrite a successor that already holds it.
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["pid"],
                2,
            )

    def test_an_unrestorable_owner_raises_for_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "owner.moved"
            path = Path(directory) / "owner.json"
            _write(candidate, _owner(pid=1, token="a" * 32))
            with mock.patch.object(
                automation.os, "link", side_effect=OSError("denied")
            ):
                with self.assertRaisesRegex(ValueError, "manual review"):
                    automation.restore_moved_owner(path, candidate)


if __name__ == "__main__":
    unittest.main()

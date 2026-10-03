"""Tests for the bounded temp-directory cleanup guard in conftest.py.

The guard exists because a blocked delete made the whole suite hang. These tests
pin the properties that matter on any host:

* a blocked cleanup must return inside the budget instead of waiting;
* a cleanup must never raise into the test that triggered it;
* whatever the host does, the outcome is either "removed" or "recorded as a
  leak" - never a silent hang.

Note on this host: the safe-delete shim blocks removal even for a one-file
temporary directory, so the removal branch is the one that normally does not
fire here. The test is written so it stays meaningful on a healthy host too.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

import conftest

TEST_BUDGET_SECONDS = 1.0


class BoundedCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        # The guard records leaks for the session-end warning; a test that
        # deliberately provokes one must not leave it behind. The budget is
        # shortened so the suite does not pay the production timeout per test.
        self._saved_budget = conftest.TEMP_CLEANUP_BUDGET_SECONDS
        self._saved_cleanup = conftest._original_cleanup
        with conftest._leaked_lock:
            self._saved_leaked = list(conftest._leaked)
            conftest._leaked.clear()
        conftest.TEMP_CLEANUP_BUDGET_SECONDS = TEST_BUDGET_SECONDS

    def tearDown(self) -> None:
        conftest.TEMP_CLEANUP_BUDGET_SECONDS = self._saved_budget
        conftest._original_cleanup = self._saved_cleanup
        with conftest._leaked_lock:
            conftest._leaked[:] = self._saved_leaked

    def test_blocked_cleanup_returns_and_leaks_instead_of_hanging(self) -> None:
        release = threading.Event()

        def blocking_cleanup(self: Any, *args: Any, **kwargs: Any) -> None:
            release.wait(60)

        conftest._original_cleanup = blocking_cleanup
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                leaked_path = Path(tmp)
            elapsed = time.monotonic() - started
        finally:
            release.set()

        self.assertLess(elapsed, TEST_BUDGET_SECONDS + 4, "cleanup must not wait")
        self.assertTrue(leaked_path.exists(), "the directory is leaked, not waited on")
        self.assertEqual(conftest.leaked_directories(), [str(leaked_path)])

    def test_real_cleanup_returns_promptly_and_accounts_for_the_outcome(self) -> None:
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "payload.json").write_text("{}", encoding="utf-8")
        elapsed = time.monotonic() - started

        self.assertLess(
            elapsed,
            TEST_BUDGET_SECONDS + 4,
            "cleanup must return inside the budget even when deletion is blocked",
        )
        removed = not path.exists()
        recorded = str(path) in conftest.leaked_directories()
        # `removed or recorded` is the real contract: the cleanup must never end
        # in neither state. Both can hold at once when the delete finishes just
        # after the budget expired, which is exactly what this host does - its
        # safe-delete shim makes removal slow (seconds) or unbounded (the
        # 58-minute gate hang).
        self.assertTrue(
            removed or recorded,
            "cleanup must either remove the directory or record it as leaked",
        )

    def test_cleanup_failure_does_not_propagate(self) -> None:
        def exploding_cleanup(self: Any, *args: Any, **kwargs: Any) -> None:
            raise PermissionError("simulated environment refusal")

        conftest._original_cleanup = exploding_cleanup
        try:
            with tempfile.TemporaryDirectory():
                pass
        finally:
            conftest._original_cleanup = self._saved_cleanup
        self.assertEqual(conftest.leaked_directories(), [])


if __name__ == "__main__":
    unittest.main()

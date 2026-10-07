"""Bound temporary-directory cleanup when a host delete hook stalls.

Timed-out cleanup runs in a daemon thread and records a session warning.
The host deletion policy still applies; no alternate deletion path is used.
Healthy cleanup completes normally. The budget is configurable for hosts
with known slow or blocked deletion.
"""

from __future__ import annotations

import os
import tempfile
import threading
from typing import Any

import pytest

TEMP_CLEANUP_BUDGET_SECONDS = float(
    os.environ.get("VPS_SSH_LAUNCHER_TEMP_CLEANUP_BUDGET_SECONDS", "5.0")
)

_leaked: list[str] = []
_leaked_lock = threading.Lock()
_original_cleanup = tempfile.TemporaryDirectory.cleanup


def _bounded_cleanup(self: Any, *args: Any, **kwargs: Any) -> None:
    finished = threading.Event()

    def _run() -> None:
        try:
            _original_cleanup(self, *args, **kwargs)
        except Exception:
            # A cleanup failure is an environment artifact; it must never turn
            # into a test failure or an error in teardown.
            pass
        finally:
            finished.set()

    worker = threading.Thread(target=_run, name="bounded-temp-cleanup", daemon=True)
    worker.start()
    if finished.wait(TEMP_CLEANUP_BUDGET_SECONDS):
        return
    with _leaked_lock:
        _leaked.append(str(getattr(self, "name", "<unknown>")))


tempfile.TemporaryDirectory.cleanup = _bounded_cleanup  # type: ignore[method-assign]


def leaked_directories() -> list[str]:
    """Directories the guard gave up on; used by the session-end warning."""

    with _leaked_lock:
        return list(_leaked)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    leaked = leaked_directories()
    if not leaked:
        return
    message = (
        f"{len(leaked)} temporary directory/ies could not be removed within "
        f"{TEMP_CLEANUP_BUDGET_SECONDS:g}s and were leaked: the host's safe-delete "
        "shim is blocking deletion. Tests were not failed for it; delete the paths "
        "manually if they matter."
    )
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        reporter.write_line(f"WARNING: {message}")
    else:  # pragma: no cover - only without the terminal plugin
        print(f"WARNING: {message}")

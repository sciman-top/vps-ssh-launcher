"""Test-session guards that keep the suite terminable on this host.

Why this exists
---------------
This host injects a safe-delete shim into every Python process
(`.../cli/vendor/shim/sitecustomize.py`), and `tempfile.TemporaryDirectory`
cleanup routes through it. On 2026-10-03 that call blocked indefinitely inside
`_safe_remove`, so `pytest -q` — the repository's own gate — never finished:

    test_ssh_tool.py::SSHToolTests::test_apply_config_allows_agent_only_profile
      passed every assertion, then hung for 58 minutes inside
      tempfile.TemporaryDirectory.__exit__ -> shutil.rmtree -> _safe_remove

The suite has 73 temporary-directory sites (test_ssh_tool 29, test_maintenance
26, test_scripts 13, test_cpa_failure_triage 5), so any of them can hit it.

What this does
--------------
Cleanup is best effort and bounded. A directory that cannot be removed within
`TEMP_CLEANUP_BUDGET_SECONDS` is *leaked*, not waited on; the directory object's
finalizer is already detached by `cleanup()` before `rmtree` runs, so interpreter
shutdown cannot block on it either. A session-end warning names the leak, so the
environment fault stays visible instead of being quietly absorbed by a green run.

What this deliberately does NOT do: bypass the shim. Nothing here approves,
deletes through another path, or touches the guard's state. The delete is simply
not allowed to block the suite.

Two visible consequences on this host, both intentional:

* `pyproject.toml` sets `faulthandler_timeout = 120`, so a test that takes
  longer than two minutes dumps every thread's stack. That dump is how the
  original hang was found, and on this host it can also fire for a test that is
  merely slow because each of its cleanup sites spends the budget first.
* Each timed-out cleanup leaves a daemon thread parked inside the shim. They are
  daemons and bounded by the number of temporary-directory sites, so the process
  still exits; the session-end warning lists the directories that were leaked.
"""

from __future__ import annotations

import os
import tempfile
import threading
from typing import Any

import pytest

# A healthy delete takes milliseconds. 5 s is generous for a slow-but-working
# host, and caps the worst-case overhead of the whole suite at ~5 s per
# temporary-directory site instead of paying the full production timeout 73 times.
#
# Overridable so a host whose delete shim is *known* to be broken can trade
# leaked directories for wall-clock. Every cleanup site otherwise spends the
# whole budget before giving up: on 2026-10-04 the gate's test step sat at 79%
# for ~10 minutes with the stack parked in the shim's `_safe_rmdir`, and
# faulthandler kept dumping a "Timeout (0:02:00)" trace for tests that were
# merely slow, not stuck. `VPS_SSH_LAUNCHER_TEMP_CLEANUP_BUDGET_SECONDS=0.5`
# keeps the guard's bound while removing that cost.
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

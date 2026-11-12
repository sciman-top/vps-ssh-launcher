"""Bound temporary-directory cleanup when a host delete hook stalls.

Timed-out cleanup runs in a daemon thread and records a session warning.
The host deletion policy still applies; no alternate deletion path is used.
Healthy cleanup completes normally. The budget is configurable for hosts
with known slow or blocked deletion.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

import pytest

# ``runpy.run_path`` mimics a script launched from scripts/ while the normal
# test import may already have loaded ``scripts``. Seed only the top-level
# fallback name; production imports the real package module when available.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

TEMP_CLEANUP_BUDGET_SECONDS = float(
    os.environ.get("VPS_SSH_LAUNCHER_TEMP_CLEANUP_BUDGET_SECONDS", "5.0")
)

_leaked: list[str] = []
_leaked_lock = threading.Lock()
_original_cleanup = tempfile.TemporaryDirectory.cleanup
_original_mkdtemp = tempfile.mkdtemp
_original_write_text = Path.write_text
_original_mkdir = Path.mkdir


def _private_temp_root() -> Path | None:
    """Return an explicit test-only temp root when CI opted in."""
    if os.environ.get("VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP") != "1":
        return None
    return Path(tempfile.gettempdir()).resolve()


def _secure_fixture_tree(root: Path) -> None:
    """Make host-style tempfile fixtures satisfy their private-file contract."""
    for directory, _, files in os.walk(root):
        os.chmod(directory, stat.S_IRWXU)
        for name in files:
            path = Path(directory, name)
            if not path.is_symlink():
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _private_mkdtemp(*args: Any, **kwargs: Any) -> str:
    path = str(_original_mkdtemp(*args, **kwargs))
    _secure_fixture_tree(Path(path))
    return path


def _private_write_text(
    filename: Path,
    data: str,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> int:
    """Tighten fixture text after a normal Path.write_text call."""
    target = str(filename)
    written = int(
        _original_write_text(
            filename,
            data,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )
    )
    try:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    except (AttributeError, OSError):
        pass
    return written


def _private_mkdir(
    directory: Path,
    mode: int = 0o777,
    parents: bool = False,
    exist_ok: bool = False,
) -> None:
    _original_mkdir(directory, mode=mode, parents=parents, exist_ok=exist_ok)
    try:
        os.chmod(directory, stat.S_IRWXU)
    except (AttributeError, OSError):
        pass


def _posix_test_directory(root: Path) -> Path:
    """Select a native Linux path when tests cross into the Bash lane."""
    getuid = getattr(os, "getuid", None)
    if os.name != "posix" or getuid is None or getuid() != 0:
        return root
    # Under WSL both drives and /tmp may be 9p. Its find d_type is DT_UNKNOWN;
    # the source contains a d_type=symlink assertion below.
    root_name = "".join(
        char if char.isalnum() or char in "-_" else "-" for char in root.name
    )
    marker = Path("/tmp") / f"vps-ssh-launcher-test-{os.getpid()}" / root_name
    if marker.exists() and (not marker.is_dir() or any(marker.iterdir())):
        raise RuntimeError(f"Unexpected pre-existing native test directory: {marker}")
    _original_mkdir(marker.parent, mode=stat.S_IRWXU, parents=True, exist_ok=True)
    os.chmod(marker.parent, stat.S_IRWXU)
    _original_mkdir(marker, mode=stat.S_IRWXU, parents=False, exist_ok=True)
    os.chmod(marker, stat.S_IRWXU)
    return marker


def _apply_private_fixture_bridge() -> None:
    if _private_temp_root() is None:
        return
    tempfile.mkdtemp = _private_mkdtemp  # type: ignore[assignment]
    cast(Any, Path).write_text = _private_write_text
    cast(Any, Path).mkdir = _private_mkdir


def pytest_configure(config: pytest.Config) -> None:
    # Linux's tempfile default is 0700; this bridge keeps a host with a
    # broader umask faithful to the same private-fixture precondition.
    _apply_private_fixture_bridge()


def pytest_runtest_setup(item: pytest.Item) -> None:
    # Dir-config loading can import this same file under another module name.
    # Reapply this idempotent bridge at each test boundary.
    _apply_private_fixture_bridge()


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

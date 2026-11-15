"""Give each xdist worker its own temporary directory.

pytest-xdist starts every worker as a separate process, and a child inherits
the environment of its parent. The gate points each worker at a directory of
its own so fixtures stay private there instead of piling up in the shared host
temporary directory, where two workers could harden or delete each other work.

The gate loads this file through PYTHONPATH. pytest rewrites a PYTHONPATH it
finds into its own sys.path changes and drops the original, so a worker sees
this entry as an empty segment and keeps it -- an empty entry costs nothing,
while a third-party plugin would need hooks in an installed distribution.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def configure() -> None:
    """Adopt this worker private directory, if the gate supplied one."""
    root = os.environ.get("VPS_SSH_LAUNCHER_GATE_TEMP_ROOT")
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not root or not worker:
        return
    directory = Path(root) / worker
    directory.mkdir(parents=True, exist_ok=True)
    resolved = str(directory)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = resolved
    tempfile.tempdir = resolved


if __name__ == "__main__":
    configure()

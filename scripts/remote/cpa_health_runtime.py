"""Private runtime paths shared by CPA health and doctor tools."""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_PROBE_LOCK_PATH = "/run/cpa-admission/health-probe.lock"
_SAFE_LOCK_PATH = re.compile(r"^/[A-Za-z0-9_./-]{1,220}$")


def probe_lock_path() -> Path:
    """Use the service-writable runtime directory, not read-only /opt state."""
    configured = os.environ.get("CPA_HEALTH_LOCK", DEFAULT_PROBE_LOCK_PATH)
    if _SAFE_LOCK_PATH.fullmatch(configured) is None or ".." in configured:
        raise ValueError("CPA_HEALTH_LOCK must be an absolute safe runtime path")
    return Path(configured)

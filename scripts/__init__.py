"""Local audit and maintenance entrypoints; never a remote-runtime package."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.append(_SCRIPTS_DIR)

try:
    from . import bootstrap as _repo_bootstrap
except ImportError:
    import bootstrap as _repo_bootstrap  # type: ignore[import-not-found,no-redef]

ensure_repo_imports = _repo_bootstrap.ensure_repo_imports
__all__ = ["ensure_repo_imports"]

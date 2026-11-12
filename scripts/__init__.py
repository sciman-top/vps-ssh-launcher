"""Local audit and maintenance entrypoints; never a remote-runtime package."""

from __future__ import annotations

import sys
import types
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

# Direct-file entrypoints have only ``scripts/`` on sys.path. Make their
# fallback resolve to the same module-level implementation without enabling
# imports from the remote runtime or depending on a caller's current directory.
_bootstrap = types.ModuleType(f"{__name__}.bootstrap")
_bootstrap.ensure_repo_imports = ensure_repo_imports  # type: ignore[attr-defined]
sys.modules[_bootstrap.__name__] = _bootstrap

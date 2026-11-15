"""Resolve repository-local imports in local and staged-script modes."""

import sys
from pathlib import Path


def _local_paths() -> tuple[Path, Path]:
    module_path = globals().get("__file__")
    if isinstance(module_path, str) and Path(module_path).name == "bootstrap.py":
        return Path(module_path).resolve().parent, Path(module_path).resolve().parents[
            1
        ]
    return Path(__file__).resolve().parent, Path(__file__).resolve().parents[1]


def ensure_repo_imports() -> None:
    """Prepend the repository root when Python runs a script by file path."""

    scripts_dir, repo_root = _local_paths()
    paths = (repo_root, scripts_dir)
    for path in reversed(paths):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    # Some Windows Python installations preload a top-level ``scripts``
    # namespace from pywin32 (``win32/scripts``).  When a repository script is
    # executed by file path, that unrelated namespace wins the first import
    # before this bootstrap gets a chance to add the repository root.  Drop it
    # only when it does not resolve to this repository's package; a local
    # package that is already loaded remains untouched.
    existing = sys.modules.get("scripts")
    if existing is None:
        return
    existing_file = getattr(existing, "__file__", None)
    existing_paths = getattr(existing, "__path__", ())
    local_package = False
    if isinstance(existing_file, str):
        local_package = Path(existing_file).resolve().parent == scripts_dir
    if not local_package:
        for path in existing_paths:
            try:
                if Path(path).resolve() == scripts_dir:
                    local_package = True
                    break
            except (OSError, TypeError, ValueError):
                continue
    if not local_package:
        sys.modules.pop("scripts", None)

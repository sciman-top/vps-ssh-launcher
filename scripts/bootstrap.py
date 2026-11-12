"""Resolve repository-local imports in local and staged-script modes."""

import sys
from pathlib import Path
from typing import Any


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


def import_repo(package: str) -> Any:
    """Import a staged repository helper without a package initializer."""

    return __import__(package, fromlist=["_"])

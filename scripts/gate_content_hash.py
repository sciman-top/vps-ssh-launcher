"""Content hashes that let a recorded gate result stand in for a re-run.

A recorded result is only reusable while its inputs are byte-identical, so the
two hashes here are content addresses rather than host facts: the tree hash
walks repo-relative paths, and the environment hash lists the distributions
installed in the interpreter that would run the gate.

Working state is deliberately excluded. The gate records a receipt while it is
itself running, so a directory it writes into -- outputs/, where that receipt
lands -- would otherwise invalidate the evidence it is holding.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

# Caches, working state, and the receipt directory itself. Everything else is
# an input: a new tracked file, script, or projected payload must change the
# hash even when no existing file did.
IGNORED_PARTS = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        "outputs",
        # A second, Linux-side virtualenv kept beside the Windows one; its
        # interpreter stubs are not readable on every host.
        ".venv-linux",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".claude",
        ".workbuddy-ai",
        ".zcode",
        "sciman-v2ray-agent",
        "build",
        "dist",
    }
)
IGNORED_PATTERNS = ("*.egg-info", ".tmp_*")
CHUNK = 1 << 20


def is_ignored(relative: str) -> bool:
    """Whether a repo-relative path is working state rather than an input."""
    return any(
        part in IGNORED_PARTS
        or any(fnmatch.fnmatch(part, pattern) for pattern in IGNORED_PATTERNS)
        for part in Path(relative).parts
    )


def iter_inputs(root: Path) -> list[str]:
    """Every file the gate could read, as sorted repo-relative POSIX paths."""
    found: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = list(directory.iterdir())
        except OSError as exc:
            # Ignored working state never reaches this walk. If a real input
            # directory becomes unreadable, fail closed instead of producing a
            # receipt that silently omits files the gate could not inspect.
            raise RuntimeError(
                f"cannot inspect gate input directory {directory}: {exc}"
            ) from exc
        for path in children:
            relative = path.relative_to(root).as_posix()
            if is_ignored(relative):
                continue
            # A dangling symlink inside the tree is still an input path:
            # whether it resolves is the tree problem, not the gate
            # explanation.
            if path.is_symlink():
                found.append(relative)
                continue
            try:
                if path.is_dir():
                    pending.append(path)
                elif path.is_file():
                    found.append(relative)
            except OSError as exc:
                raise RuntimeError(
                    f"cannot inspect gate input path {relative}: {exc}"
                ) from exc
    return sorted(found)


def tree_hash(root: Path, paths: list[str]) -> str:
    """Content address over the named paths, in repo-relative order."""
    digest = hashlib.sha256()
    for relative in sorted(paths):
        digest.update(relative.encode("utf-8") + b"\0")
        try:
            with (root / relative).open("rb") as handle:
                while True:
                    chunk = handle.read(CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"gate input disappeared while hashing {relative}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"cannot read gate input {relative}: {exc}") from exc
        digest.update(b"\0")
    return digest.hexdigest()


def distribution_label(distribution: importlib.metadata.Distribution) -> str:
    """One sorted key for an installed distribution, name and version."""
    name = str(distribution.metadata["Name"] or "<unnamed>")
    return f"{name}=={distribution.version}".lower()


def environment_hash() -> str:
    """Content address over this interpreter and its installed distributions."""
    names = sorted(
        distribution_label(distribution)
        for distribution in importlib.metadata.distributions()
    )
    identity = {
        "executable": str(Path(sys.executable).resolve()),
        "implementation": platform.python_implementation().lower(),
        "machine": platform.machine().lower(),
        "platform": sys.platform,
        "version": list(sys.version_info[:3]),
        "distributions": names,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate content hashes.")
    parser.add_argument("root", type=Path, nargs="?")
    parser.add_argument("paths", nargs="*", help="Repo-relative paths to hash.")
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the input list instead of a hash, one path per line.",
    )
    args = parser.parse_args(argv)
    # The walk starts at the repository root; an explicit --root only matters
    # when the gate calls this from somewhere else, and it is not sent over
    # the command line because a tree this size overflows a Windows argument
    # list. The root therefore defaults to the checkout that owns this file.
    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    if args.list:
        for relative in iter_inputs(root):
            print(relative)
        return 0
    unknown = args.paths or iter_inputs(root)
    paths = [path for path in unknown if not is_ignored(path)]
    print(
        json.dumps({"tree": tree_hash(root, paths), "environment": environment_hash()})
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Small shared safety primitives for the local Cockpit maintenance tools."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess  # nosec B404
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

COCKPIT_PROCESS_NAMES = frozenset({"cockpit-tools.exe", "cockpit-cliproxy.exe"})


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def running_cockpit_processes() -> list[str]:
    """Return known Cockpit executable names without exposing process output."""
    if os.name != "nt":
        return []
    try:
        result = subprocess.run(  # nosec B603 B607
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("cannot inspect running Cockpit processes") from exc
    if result.returncode != 0:
        raise RuntimeError("cannot inspect running Cockpit processes")
    names = {
        line.split(",", 1)[0].strip().strip('"').lower()
        for line in result.stdout.splitlines()
    }
    return sorted(names & COCKPIT_PROCESS_NAMES)


def atomic_write_json(path: Path, payload: Any) -> bytes:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        before = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        before = None
    if before is not None and (
        not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
    ):
        raise ValueError(f"refusing unsafe Cockpit file target: {path.name}")
    if before is not None:
        mode = stat.S_IMODE(before.st_mode)
    else:
        mode = 0o600
    temporary_path = path.parent / (
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    descriptor = os.open(
        temporary_path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
        mode,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if before is not None:
            after = path.stat(follow_symlinks=False)
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("Cockpit file changed during atomic write")
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return data


def create_cockpit_backup(paths: Sequence[Path], backup_root: Path, label: str) -> Path:
    """Copy payloads into one private backup directory keyed by file name.

    Backups use the bare file name (no parent prefix), so two source paths
    sharing a basename would silently overwrite each other; refuse instead of
    losing one of them.
    """
    names = [path.name for path in paths]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"backup file names collide: {', '.join(duplicates)}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = backup_root / f"{label}-{stamp}"
    backup_dir.mkdir(parents=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    for path in paths:
        backup = backup_dir / path.name
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    return backup_dir


def restore_cockpit_backup(
    sources: Sequence[Path],
    backup_root: Path,
) -> None:
    """Restore every source from one backup; fail closed on ambiguity."""
    names = [path.name for path in sources]
    if len(set(names)) != len(names):
        raise ValueError("backup restore file names collide")
    for path in sources:
        backup = backup_root / path.name
        if backup.is_symlink() or not backup.is_file():
            raise ValueError(f"backup is missing: {path.name}")
        if path.is_symlink():
            raise ValueError(f"restore target is a symlink: {path.name}")
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.restore"
        )
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(backup.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                shutil.copymode(path, temporary)
            os.replace(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

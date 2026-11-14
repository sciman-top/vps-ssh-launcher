"""Private atomic-file primitives for local maintenance and config state."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any


def open_private_file(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open without a transient group/other-readable creation window."""
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    return os.open(path, flags, mode)


def prepare_private_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
            raise ValueError(
                f"Private local state directory is accessible to other users: {path.parent}"
            )


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    replace_existing: bool = True,
) -> None:
    """Publish a complete private file, refusing a symlink target.

    The temporary inode starts with 0600. A new destination is created with
    O_EXCL, so its name cannot be redirected through a symlink. An existing
    regular destination has its exact permission bits preserved.
    """
    destination = Path(os.path.abspath(path.expanduser()))
    prepare_private_parent(destination)
    try:
        destination.stat(follow_symlinks=False)
    except FileNotFoundError:
        existing_mode: int | None = None
    except OSError as exc:
        raise ValueError(
            f"Unable to inspect atomic-write destination: {destination}"
        ) from exc
    else:
        path_stat = destination.stat(follow_symlinks=False)
        if stat.S_ISLNK(path_stat.st_mode):
            raise ValueError(f"Refusing atomic write through a symlink: {destination}")
        if not stat.S_ISREG(path_stat.st_mode):
            raise ValueError(
                f"Atomic-write destination is not a regular file: {destination}"
            )
        if not replace_existing:
            raise FileExistsError(destination)
        existing_mode = stat.S_IMODE(path_stat.st_mode)
        if os.name != "nt" and existing_mode & 0o077:
            raise ValueError(
                f"Private local state is accessible to other users: {destination}"
            )

    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary)
    try:
        try:
            # fchmod is POSIX-only; on Windows the descriptor is created with
            # mode 0o600 already and the AttributeError below is swallowed.
            os.fchmod(descriptor, 0o600)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            if os.name != "nt":
                raise
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if existing_mode is not None and os.name != "nt":
            os.chmod(temporary_path, existing_mode)
        if not replace_existing:
            # The precheck gives a clear error; this hard-link publish closes
            # the race where another writer creates the name in between.
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                raise FileExistsError(destination) from None
        else:
            os.replace(temporary_path, destination)
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary_path.unlink()


def atomic_write_json(
    path: Path,
    value: Any,
    *,
    replace_existing: bool = True,
) -> None:
    data = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    atomic_write_bytes(path, data, replace_existing=replace_existing)

"""Regression coverage for private local atomic writes."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from vps_ssh_launcher import safe_files
from vps_ssh_launcher.safe_files import atomic_write_bytes, atomic_write_json


def test_atomic_json_preserves_existing_permissions(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    atomic_write_json(path, {"new": True})
    assert path.read_text(encoding="utf-8") == '{\n  "new": true\n}\n'
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_atomic_json_refuses_symlink_and_existing_destination_when_exclusive(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "destination.json"
    destination.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        atomic_write_json(destination, {"unsafe": True}, replace_existing=False)
    if os.name != "nt":
        target = tmp_path / "target.json"
        target.write_text("{}\n", encoding="utf-8")
        link = tmp_path / "link.json"
        link.symlink_to(target)
        with pytest.raises(ValueError, match="symlink"):
            atomic_write_json(link, {"unsafe": True})
        assert target.read_text(encoding="utf-8") == "{}\n"


def test_atomic_json_new_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "new.json"
    atomic_write_json(path, {"ok": True}, replace_existing=False)
    assert path.is_file()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_atomic_write_refuses_a_non_regular_destination(tmp_path: Path) -> None:
    # A directory at the destination name must be refused before mkstemp, or the
    # publish step would fail late with an opaque OS error.
    directory = tmp_path / "occupied"
    directory.mkdir()
    with pytest.raises(ValueError, match="not a regular file"):
        atomic_write_bytes(directory, b"payload")


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_atomic_write_refuses_a_destination_readable_by_other_users(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "state.json"
    destination.write_text("{}\n", encoding="utf-8")
    destination.chmod(0o644)
    with pytest.raises(ValueError, match="accessible to other users"):
        atomic_write_json(destination, {"unsafe": True})
    # The refused write must not have replaced the file.
    assert destination.read_text(encoding="utf-8") == "{}\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_prepare_private_parent_rejects_a_world_readable_directory(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    with pytest.raises(ValueError, match="accessible to other users"):
        safe_files.prepare_private_parent(shared / "state.json")
    shared.chmod(0o700)
    safe_files.prepare_private_parent(shared / "state.json")


def test_exclusive_publish_keeps_the_destination_when_it_appears_mid_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The precheck and the publish are separated by a real window; the hard-link
    # path closes it. Simulate a writer winning that race and assert the loser
    # reports FileExistsError instead of clobbering the winner.
    destination = tmp_path / "race.json"
    real_link = os.link

    def racing_link(source: Path, target: Path) -> None:
        Path(target).write_text('{"winner": true}\n', encoding="utf-8")
        real_link(source, target)

    # Patching safe_files.os patches the shared module: keep the real link.
    monkeypatch.setattr(safe_files.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        atomic_write_json(destination, {"loser": True}, replace_existing=False)
    assert destination.read_text(encoding="utf-8") == '{"winner": true}\n'


def test_failed_publish_removes_the_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A crash between mkstemp and the publish must not leave a private partial
    # file next to the state directory.
    destination = tmp_path / "state.json"

    def failing_replace(source: Path, target: Path) -> None:
        raise OSError("publish failed")

    monkeypatch.setattr(safe_files.os, "replace", failing_replace)
    with pytest.raises(OSError, match="publish failed"):
        atomic_write_json(destination, {"value": True})
    assert list(tmp_path.glob(".state.json.*.tmp")) == []
    assert not destination.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_opened_private_file_starts_at_the_requested_mode(tmp_path: Path) -> None:
    path = tmp_path / "secret"
    descriptor = safe_files.open_private_file(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        os.write(descriptor, b"x")
    finally:
        os.close(descriptor)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

"""Regression coverage for private local atomic writes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vps_ssh_launcher.safe_files import atomic_write_json


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

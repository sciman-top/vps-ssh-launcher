"""Platform safety tests for Cockpit's shared file operations."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest

MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "cockpit_file_ops.py")
)
running_cockpit_processes = cast(Any, MODULE["running_cockpit_processes"])


def test_non_windows_process_gate_requires_explicit_write_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MODULE["os"], "name", "posix")
    monkeypatch.delenv(
        "VPS_SSH_LAUNCHER_ALLOW_NON_WINDOWS_COCKPIT_WRITE", raising=False
    )
    with pytest.raises(RuntimeError, match="refusing an unverified catalog write"):
        running_cockpit_processes()
    monkeypatch.setenv("VPS_SSH_LAUNCHER_ALLOW_NON_WINDOWS_COCKPIT_WRITE", "1")
    assert running_cockpit_processes() == []


def test_windows_process_gate_still_uses_tasklist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MODULE["os"], "name", "nt")
    result = mock.Mock(returncode=0, stdout='"cockpit-tools.exe","1"\n')
    with mock.patch.object(MODULE["subprocess"], "run", return_value=result):
        assert running_cockpit_processes() == ["cockpit-tools.exe"]

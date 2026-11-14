"""The persisted circuit state must be read through a single, no-follow open.

`path.exists()` + `path.read_text()` followed a swapped symlink and raised
FileNotFoundError when the file vanished between the two calls, taking the whole
admission process down at startup. `_load_risk_state` opens once with
O_NOFOLLOW, fstats the descriptor it actually parses, and treats a missing file
as "no state".
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ADMISSION = REPO_ROOT / "scripts" / "remote" / "cpa-admission.py"


@pytest.fixture(scope="module")
def admission() -> Any:
    spec = importlib.util.spec_from_file_location("cpa_admission_under_test", ADMISSION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _state(path: Path, **overrides: Any) -> None:
    value = {"version": 1, "not_before": 0, "hold_reason": None}
    value.update(overrides)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_a_missing_state_file_is_a_startup_error(
    admission: Any, tmp_path: Path
) -> None:
    # The unit always passes a durable path, so "absent" must not read as
    # "nothing to restore": that would erase an operator hold on restart.
    with pytest.raises(FileNotFoundError):
        admission._load_risk_state(tmp_path / "absent.json")


def test_the_recorded_identity_is_the_inode_that_was_parsed(
    admission: Any, tmp_path: Path
) -> None:
    path = tmp_path / "state.json"
    _state(path)
    value, identity = admission._load_risk_state(path)
    assert value["version"] == 1
    stat_result = os.stat(path)
    assert identity == (stat_result.st_dev, stat_result.st_ino)


def test_a_symlinked_state_file_is_refused(admission: Any, tmp_path: Path) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        # The refusal is O_NOFOLLOW's doing. Windows has no equivalent flag for
        # os.open, so the same path is simply followed there; asserting a
        # refusal the platform cannot produce would only pin a fiction.
        pytest.skip("O_NOFOLLOW is unavailable on this platform")
    real = tmp_path / "real.json"
    _state(real)
    link = tmp_path / "link.json"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="unreadable"):
        admission._load_risk_state(link)


def test_a_directory_is_refused(admission: Any, tmp_path: Path) -> None:
    directory = tmp_path / "dir.json"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        admission._load_risk_state(directory)

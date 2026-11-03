"""Tests for the remote mutation embedded in cpa_workbuddy_login_region.ps1."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import yaml

SCRIPT = Path(__file__).parents[1] / "scripts" / "cpa_workbuddy_login_region.ps1"


def _python_block() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    marker = 'python3 - "$CONFIG" "$REGION" "$APPLY" "$BACKUP_ROOT" <<\'PY\'\n'
    return source.split(marker, 1)[1].split("\nPY\n", 1)[0]


def _run(
    tmp_path: Path,
    *,
    region: str,
    apply: bool,
    mutate: dict | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    config: dict[str, Any] = {
        "routing": {"retry": {"request-retry": 0}},
        "plugins": {
            "configs": {
                "workbuddy": {
                    "enabled": True,
                    "login_region": "cn",
                    "models_cn": ["deepseek-v4.1-flash"],
                    "models_intl": ["deepseek-v4.1-flash"],
                }
            }
        },
    }
    if mutate:
        config["plugins"]["configs"]["workbuddy"].update(mutate)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    backup_root = tmp_path / "backups"
    env = os.environ.copy()
    env["WORKBUDDY_LOGIN_REGION_BACKUP_ROOT"] = str(backup_root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _python_block(),
            str(config_path),
            region,
            "1" if apply else "0",
            str(backup_root),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result, config_path, backup_root


def test_dry_run_reports_target_without_mutation(tmp_path: Path) -> None:
    result, config_path, backup_root = _run(tmp_path, region="intl", apply=False)
    assert result.returncode == 0, result.stderr
    assert "WORKBUDDY_LOGIN_REGION_DRY_RUN current=cn target=intl" in result.stdout
    config_data = cast(
        dict[str, Any],
        yaml.safe_load(config_path.read_text(encoding="utf-8")),
    )
    assert config_data["plugins"]["configs"]["workbuddy"]["login_region"] == "cn"
    assert not backup_root.exists()


def test_apply_changes_only_login_region_and_creates_backup(tmp_path: Path) -> None:
    result, config_path, backup_root = _run(tmp_path, region="intl", apply=True)
    assert result.returncode == 0, result.stderr
    assert "WORKBUDDY_LOGIN_REGION=APPLIED previous=cn current=intl" in result.stdout
    updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert updated["plugins"]["configs"]["workbuddy"]["login_region"] == "intl"
    assert updated["routing"]["retry"]["request-retry"] == 0
    backups = list(backup_root.glob("*/config.yaml"))
    assert len(backups) == 1
    original = yaml.safe_load(backups[0].read_text(encoding="utf-8"))
    assert original["plugins"]["configs"]["workbuddy"]["login_region"] == "cn"


def test_invalid_current_region_is_refused(tmp_path: Path) -> None:
    result, _, _ = _run(
        tmp_path, region="intl", apply=True, mutate={"login_region": "secret"}
    )
    assert result.returncode != 0
    assert "REFUSE current WorkBuddy login_region" in result.stderr


def test_disabled_plugin_is_refused(tmp_path: Path) -> None:
    result, _, _ = _run(tmp_path, region="intl", apply=True, mutate={"enabled": False})
    assert result.returncode != 0
    assert "REFUSE WorkBuddy plugin is not enabled" in result.stderr


def test_apply_script_drains_traffic_before_mutation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert (
        'TRAFFIC_LOCK="${WORKBUDDY_LOGIN_REGION_TRAFFIC_LOCK:-/run/cpa-admission/requests.lock}"'
        in source
    )
    assert source.index('exec 8>"$TRAFFIC_LOCK"') < source.index('python3 - "$CONFIG"')

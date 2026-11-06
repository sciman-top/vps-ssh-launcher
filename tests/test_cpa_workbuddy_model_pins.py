"""Tests for the remote mutation embedded in cpa_workbuddy_model_pins.ps1."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

SCRIPT = Path(__file__).parents[1] / "scripts" / "cpa_workbuddy_model_pins.ps1"


def _python_block() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    marker = 'python3 - "$CONFIG" "$MODELS_CN" "$MODELS_INTL" "$APPLY" "$BACKUP_ROOT" <<\'PY\'\n'
    return source.split(marker, 1)[1].split("\nPY\n", 1)[0]


def _run(
    tmp_path: Path,
    *,
    models_cn: str,
    models_intl: str,
    apply: bool,
    mutate: dict[str, Any] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    config: dict[str, Any] = {
        "routing": {"retry": {"request-retry": 0}},
        "plugins": {
            "configs": {
                "workbuddy": {
                    "enabled": True,
                    "login_region": "intl",
                    "models_cn": "deepseek-v4.1-flash",
                    "models_intl": "deepseek-v4.1-flash,deepseek-v4.1-flash-sg",
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
    env["WORKBUDDY_MODEL_PINS_BACKUP_ROOT"] = str(backup_root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _python_block(),
            str(config_path),
            models_cn,
            models_intl,
            "1" if apply else "0",
            str(backup_root),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env=env,
    )
    return result, config_path, backup_root


def test_dry_run_reports_target_without_mutation(tmp_path: Path) -> None:
    result, config_path, backup_root = _run(
        tmp_path,
        models_cn="deepseek-v4.1-flash,hy4free",
        models_intl="deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free",
        apply=False,
    )
    assert result.returncode == 0, result.stderr
    assert "WORKBUDDY_MODEL_PINS_DRY_RUN" in result.stdout
    assert "target_cn=deepseek-v4.1-flash,hy4free" in result.stdout
    assert (
        "target_intl=deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free"
        in result.stdout
    )
    assert not backup_root.exists()

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    wb = loaded["plugins"]["configs"]["workbuddy"]
    assert wb["models_cn"] == "deepseek-v4.1-flash"
    assert wb["models_intl"] == "deepseek-v4.1-flash,deepseek-v4.1-flash-sg"


def test_already_applied_exits_cleanly(tmp_path: Path) -> None:
    result, _, backup_root = _run(
        tmp_path,
        models_cn="deepseek-v4.1-flash",
        models_intl="deepseek-v4.1-flash,deepseek-v4.1-flash-sg",
        apply=True,
    )
    assert result.returncode == 0, result.stderr
    assert "WORKBUDDY_MODEL_PINS=ALREADY_APPLIED" in result.stdout
    assert not backup_root.exists()


def test_apply_updates_config_and_leaves_backup(tmp_path: Path) -> None:
    result, config_path, backup_root = _run(
        tmp_path,
        models_cn="deepseek-v4.1-flash,hy4free",
        models_intl="deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free",
        apply=True,
    )
    assert result.returncode == 0, result.stderr
    assert "WORKBUDDY_MODEL_PINS=APPLIED" in result.stdout
    assert "cn=deepseek-v4.1-flash,hy4free" in result.stdout
    assert "intl=deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free" in result.stdout

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    wb = loaded["plugins"]["configs"]["workbuddy"]
    assert wb["models_cn"] == "deepseek-v4.1-flash,hy4free"
    assert wb["models_intl"] == "deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free"

    backups = list(backup_root.glob("cpa-workbuddy-model-pins-backup-*"))
    assert len(backups) == 1
    backup_file = backups[0] / "config.yaml"
    assert backup_file.exists()
    backup_wb = yaml.safe_load(backup_file.read_text(encoding="utf-8"))["plugins"][
        "configs"
    ]["workbuddy"]
    assert backup_wb["models_cn"] == "deepseek-v4.1-flash"
    assert backup_wb["models_intl"] == "deepseek-v4.1-flash,deepseek-v4.1-flash-sg"


def test_refuse_on_disabled_plugin(tmp_path: Path) -> None:
    result, _, _ = _run(
        tmp_path,
        models_cn="deepseek-v4.1-flash,hy4free",
        models_intl="deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free",
        apply=False,
        mutate={"enabled": False},
    )
    assert result.returncode != 0
    assert "REFUSE WorkBuddy plugin is not enabled" in (result.stderr + result.stdout)


def test_refuse_on_invalid_token(tmp_path: Path) -> None:
    result, _, _ = _run(
        tmp_path,
        models_cn="deepseek-v4.1-flash;rm -rf /",
        models_intl="deepseek-v4.1-flash",
        apply=False,
    )
    assert result.returncode != 0
    assert "REFUSE models_cn contains invalid model token" in (
        result.stderr + result.stdout
    )

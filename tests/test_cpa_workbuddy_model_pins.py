"""Tests for the remote mutation embedded in cpa_workbuddy_model_pins.ps1.

2026-11-17 luode0320 cutover: the tool no longer writes plugin model pins
(the mmqz keys are gone); it projects the route manifest's
workbuddy-ai-provider fork-alias bridge into config.yaml's
oauth-model-alias section and removes a leftover legacy workbuddy channel.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

SCRIPT = Path(__file__).parents[1] / "scripts" / "cpa_workbuddy_model_pins.ps1"
MANIFEST = Path(__file__).parents[1] / "scripts" / "remote" / "cpa_provider_routes.json"

BRIDGE_ENTRIES = [
    {
        "name": "deepseek-v4.1-flash-sg",
        "alias": "deepseek-v4.1-flash-intl",
        "fork": True,
    },
]


def _python_block() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    marker = 'python3 - "$CONFIG" "$APPLY" "$BACKUP_ROOT" "$MANIFEST_B64" <<\'PY\'\n'
    return source.split(marker, 1)[1].split("\nPY\n", 1)[0]


def _manifest_b64() -> str:
    return base64.b64encode(MANIFEST.read_bytes()).decode("ascii")


def _run(
    tmp_path: Path,
    config: dict[str, Any],
    *,
    apply: bool,
    manifest_b64: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    backup_root = tmp_path / "backups"
    env = os.environ.copy()
    env["WORKBUDDY_ALIAS_BRIDGE_BACKUP_ROOT"] = str(backup_root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _python_block(),
            str(config_path),
            "1" if apply else "0",
            str(backup_root),
            manifest_b64 if manifest_b64 is not None else _manifest_b64(),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env=env,
    )
    return result, config_path, backup_root


def _config(
    *,
    alias_channel: Any = None,
    plugins: Any = None,
) -> dict[str, Any]:
    if alias_channel is None:
        alias_channel = {"workbuddy": BRIDGE_ENTRIES[:1]}
    config: dict[str, Any] = {
        "routing": {"retry": {"request-retry": 0}},
        "plugins": plugins
        if plugins is not None
        else {
            "configs": {
                "workbuddy-provider": {"enabled": True},
                "workbuddy-ai-provider": {"enabled": True},
            }
        },
        "oauth-model-alias": alias_channel,
    }
    return config


def test_dry_run_reports_target_without_mutation(tmp_path: Path) -> None:
    result, config_path, backup_root = _run(tmp_path, _config(), apply=False)
    assert result.returncode == 0, result.stderr
    assert "WORKBUDDY_ALIAS_BRIDGE_DRY_RUN" in result.stdout
    assert "legacy_channel_present=true" in result.stdout
    assert not backup_root.exists()

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "workbuddy" in loaded["oauth-model-alias"]
    assert "workbuddy-ai-provider" not in loaded["oauth-model-alias"]


def test_already_applied_exits_cleanly(tmp_path: Path) -> None:
    result, _, backup_root = _run(
        tmp_path,
        _config(alias_channel={"workbuddy-ai-provider": BRIDGE_ENTRIES}),
        apply=True,
    )
    assert result.returncode == 0, result.stderr
    assert "WORKBUDDY_ALIAS_BRIDGE=ALREADY_APPLIED" in result.stdout
    assert not backup_root.exists()


def test_apply_swaps_channel_drops_legacy_and_leaves_backup(tmp_path: Path) -> None:
    result, config_path, backup_root = _run(tmp_path, _config(), apply=True)
    assert result.returncode == 0, result.stderr
    assert "WORKBUDDY_ALIAS_BRIDGE=APPLIED" in result.stdout
    assert "aliases=deepseek-v4.1-flash-intl-only" in result.stdout

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "workbuddy" not in loaded["oauth-model-alias"]
    assert loaded["oauth-model-alias"]["workbuddy-ai-provider"] == BRIDGE_ENTRIES
    # Nothing outside the alias section moved.
    assert loaded["routing"] == {"retry": {"request-retry": 0}}
    assert "workbuddy-provider" in loaded["plugins"]["configs"]

    backups = list(backup_root.glob("cpa-workbuddy-alias-bridge-backup-*"))
    assert len(backups) == 1
    backup_config = yaml.safe_load(
        (backups[0] / "config.yaml").read_text(encoding="utf-8")
    )
    assert "workbuddy" in backup_config["oauth-model-alias"]


def test_refuse_when_alias_section_missing(tmp_path: Path) -> None:
    result, _, _ = _run(tmp_path, _config(alias_channel={}), apply=False)
    assert result.returncode != 0
    assert "REFUSE oauth-model-alias section missing" in (result.stderr + result.stdout)

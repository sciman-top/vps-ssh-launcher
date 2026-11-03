"""Tests for scripts/cockpit_provider_prune.py."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any, cast

MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "cockpit_provider_prune.py")
)

main = cast(Any, MODULE["main"])
BLOCKED = cast(int, MODULE["BLOCKED"])


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture(root: Path) -> tuple[Path, Path]:
    cockpit = root / "cockpit"
    registry = {
        "providers": [
            {
                "id": "retired",
                "name": "old relay",
                "baseUrl": "https://codex.ciii.club/private/v1",
                "apiKeys": [{"id": "k1", "apiKey": "secret-value"}],
            },
            {
                "id": "keep",
                "name": "fq.sciman.top",
                "baseUrl": "https://fq.sciman.top:8443/private/v1",
                "apiKeys": [{"id": "k2", "apiKey": "keep-value"}],
            },
        ]
    }
    _write(cockpit / "codex_model_providers.json", registry)
    policy = root / "provider-policy.json"
    _write(policy, {"retired_hosts": ["codex.ciii.club"]})
    return cockpit, policy


def test_dry_run_reports_without_mutation(tmp_path: Path, capsys: Any) -> None:
    cockpit, policy = _fixture(tmp_path)
    registry_path = cockpit / "codex_model_providers.json"
    before = registry_path.read_bytes()
    rc = main(["--cockpit-dir", str(cockpit), "--provider-policy", str(policy)])
    output = capsys.readouterr().out
    assert rc == 0
    assert "COCKPIT_PROVIDER_PRUNE=DRY_RUN" in output
    assert "old relay" in output
    assert "secret-value" not in output
    assert "private" not in output
    assert registry_path.read_bytes() == before


def test_apply_removes_only_retired_host_and_creates_backup(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    cockpit, policy = _fixture(tmp_path)
    registry_path = cockpit / "codex_model_providers.json"
    monkeypatch.setitem(main.__globals__, "running_cockpit_processes", lambda: [])
    backup_root = tmp_path / "backups"
    rc = main(
        [
            "--cockpit-dir",
            str(cockpit),
            "--provider-policy",
            str(policy),
            "--backup-root",
            str(backup_root),
            "--apply",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0
    assert "COCKPIT_PROVIDER_PRUNE=APPLIED" in output
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert [provider["id"] for provider in registry["providers"]] == ["keep"]
    backups = list(backup_root.glob("*/codex_model_providers.json"))
    assert len(backups) == 1
    assert (
        json.loads(backups[0].read_text(encoding="utf-8"))["providers"][0]["id"]
        == "retired"
    )


def test_apply_refuses_while_cockpit_runs(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    cockpit, policy = _fixture(tmp_path)
    monkeypatch.setitem(
        main.__globals__,
        "running_cockpit_processes",
        lambda: ["cockpit-tools.exe"],
    )
    rc = main(
        [
            "--cockpit-dir",
            str(cockpit),
            "--provider-policy",
            str(policy),
            "--backup-root",
            str(tmp_path / "backups"),
            "--apply",
        ]
    )
    captured = capsys.readouterr()
    assert rc == BLOCKED
    assert "REFUSE Cockpit is running" in captured.err
    assert not (tmp_path / "backups").exists()


def test_apply_supports_top_level_list_registry(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cockpit = tmp_path / "cockpit"
    registry_path = cockpit / "codex_model_providers.json"
    _write(
        registry_path,
        [
            {
                "id": "retired",
                "name": "old relay",
                "baseUrl": "https://codex.ciii.club/private/v1",
            },
            {
                "id": "keep",
                "name": "fq.sciman.top",
                "baseUrl": "https://fq.sciman.top:8443/private/v1",
            },
        ],
    )
    policy = tmp_path / "provider-policy.json"
    _write(policy, {"retired_hosts": ["codex.ciii.club"]})
    monkeypatch.setitem(main.__globals__, "running_cockpit_processes", lambda: [])
    rc = main(
        [
            "--cockpit-dir",
            str(cockpit),
            "--provider-policy",
            str(policy),
            "--backup-root",
            str(tmp_path / "backups"),
            "--apply",
        ]
    )
    assert rc == 0
    assert [provider["id"] for provider in json.loads(registry_path.read_text())] == [
        "keep"
    ]


def test_official_deepseek_is_not_retired_without_an_explicit_local_policy(
    tmp_path: Path, capsys: Any
) -> None:
    cockpit = tmp_path / "cockpit"
    registry_path = cockpit / "codex_model_providers.json"
    _write(
        registry_path,
        [
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "baseUrl": "https://api.deepseek.com/v1",
            }
        ],
    )
    policy = tmp_path / "provider-policy.json"
    _write(policy, {"retired_hosts": []})
    rc = main(
        [
            "--cockpit-dir",
            str(cockpit),
            "--provider-policy",
            str(policy),
        ]
    )
    assert rc == 0
    assert "COCKPIT_PROVIDER_PRUNE=NOOP" in capsys.readouterr().out

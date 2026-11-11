"""Tests for scripts/cockpit_model_catalog_prune.py."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any, cast

MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "cockpit_model_catalog_prune.py")
)

main = cast(Any, MODULE["main"])
BLOCKED = cast(int, MODULE["BLOCKED"])


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _fixture(root: Path) -> tuple[Path, Path, Path]:
    cockpit = root / "cockpit"
    codex = root / "codex"
    registry = {
        "providers": [
            {
                "name": "fq.sciman.top",
                "modelCatalog": ["gpt-6-luna", "gpt-reserve"],
                "modelCapabilities": {"gpt-reserve": {"vision": False}},
                "apiKeys": [
                    {
                        "id": "key-1",
                        "modelCatalog": ["gpt-6-luna", "gpt-reserve"],
                        "modelCapabilities": {"gpt-reserve": {"vision": False}},
                    }
                ],
            }
        ]
    }
    _write(cockpit / "codex_model_providers.json", registry)
    _write(
        codex / ".cockpit-experimental-model-catalog-config.json",
        {
            "models": [
                {"model_id": "gpt-6-luna"},
                {"model_id": "gpt-reserve"},
            ]
        },
    )
    _write(
        codex / "cockpit-model-catalog.json",
        {"models": [{"slug": "gpt-6-luna"}, {"slug": "gpt-reserve"}]},
    )
    meta = codex / "cockpit-model-catalog.meta.json"
    _write(meta, {"generator": 11, "catalogHash": "old"})
    policy = root / "provider-policy.json"
    _write(policy, {"retired_hosts": [], "retired_models": ["gpt-reserve"]})
    return cockpit, codex, policy


def test_dry_run_reports_without_mutation(tmp_path: Path, capsys: Any) -> None:
    cockpit, codex, policy = _fixture(tmp_path)
    registry_path = cockpit / "codex_model_providers.json"
    before = registry_path.read_bytes()
    rc = main(
        [
            "--cockpit-dir",
            str(cockpit),
            "--codex-dir",
            str(codex),
            "--provider-policy",
            str(policy),
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0
    assert "RETIRED_MODEL_REFERENCES total=6" in output
    assert "COCKPIT_MODEL_CATALOG_PRUNE=DRY_RUN" in output
    assert registry_path.read_bytes() == before


def test_apply_removes_only_exact_retired_model_and_updates_hash(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cockpit, codex, policy = _fixture(tmp_path)
    monkeypatch.setitem(main.__globals__, "running_cockpit_processes", lambda: [])
    rc = main(
        [
            "--cockpit-dir",
            str(cockpit),
            "--codex-dir",
            str(codex),
            "--provider-policy",
            str(policy),
            "--backup-root",
            str(tmp_path / "backups"),
            "--apply",
        ]
    )
    assert rc == 0

    registry = json.loads(
        (cockpit / "codex_model_providers.json").read_text(encoding="utf-8")
    )
    provider = registry["providers"][0]
    assert provider["modelCatalog"] == ["gpt-6-luna"]
    assert provider["modelCapabilities"] == {}
    assert provider["apiKeys"][0]["modelCatalog"] == ["gpt-6-luna"]
    assert provider["apiKeys"][0]["modelCapabilities"] == {}

    experimental = json.loads(
        (codex / ".cockpit-experimental-model-catalog-config.json").read_text(
            encoding="utf-8"
        )
    )
    assert experimental["models"] == [{"model_id": "gpt-6-luna"}]

    desktop = json.loads(
        (codex / "cockpit-model-catalog.json").read_text(encoding="utf-8")
    )
    assert desktop["models"] == [{"slug": "gpt-6-luna"}]
    meta = json.loads(
        (codex / "cockpit-model-catalog.meta.json").read_text(encoding="utf-8")
    )
    assert meta["catalogHash"] != "old"
    assert len(meta["catalogHash"]) == 64
    assert list((tmp_path / "backups").glob("*/codex_model_providers.json"))


def test_apply_refuses_while_cockpit_runs(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    cockpit, codex, policy = _fixture(tmp_path)
    monkeypatch.setitem(
        main.__globals__,
        "running_cockpit_processes",
        lambda: ["cockpit-tools.exe"],
    )
    rc = main(
        [
            "--cockpit-dir",
            str(cockpit),
            "--codex-dir",
            str(codex),
            "--provider-policy",
            str(policy),
            "--backup-root",
            str(tmp_path / "backups"),
            "--apply",
        ]
    )
    assert rc == BLOCKED
    assert "REFUSE Cockpit is running" in capsys.readouterr().err
    assert not (tmp_path / "backups").exists()


def test_exact_model_is_removed_without_prefix_false_positive() -> None:
    prune_json = cast(Any, MODULE["prune_json"])
    document = {
        "modelCatalog": ["gpt-reserve", "gpt-reserve-extra"],
        "nested": {"models": [{"model_id": "gpt-reserve-extra"}]},
    }
    updated, removed = prune_json(document, {"gpt-reserve"})
    assert removed == 1
    assert updated["modelCatalog"] == ["gpt-reserve-extra"]
    assert updated["nested"]["models"] == [{"model_id": "gpt-reserve-extra"}]


def test_create_cockpit_backup_refuses_colliding_names(tmp_path: Path) -> None:
    # Backups are keyed by bare file name (no parent prefix), so two sources
    # with the same basename would silently overwrite each other inside one
    # backup directory. The helper must refuse instead of losing one of them.
    create_cockpit_backup = cast(Any, MODULE["create_cockpit_backup"])
    first = tmp_path / "a" / "same.json"
    second = tmp_path / "b" / "same.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    try:
        create_cockpit_backup([first, second], tmp_path / "backups", "cockpit-test")
    except ValueError as exc:
        assert "collide" in str(exc)
    else:
        raise AssertionError("colliding backup names must be refused")


def test_failed_multi_file_publish_restores_catalog_and_metadata(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    cockpit, codex, policy = _fixture(tmp_path)
    registry = cockpit / "codex_model_providers.json"
    catalog = codex / "cockpit-model-catalog.json"
    meta = codex / "cockpit-model-catalog.meta.json"
    originals = {
        path: path.read_bytes() for path in (registry, catalog, meta)
    }
    monkeypatch.setitem(main.__globals__, "running_cockpit_processes", lambda: [])
    real_write = main.__globals__["atomic_write_json"]
    writes = 0

    def fail_after_catalog(path: Path, payload: Any) -> None:
        nonlocal writes
        real_write(path, payload)
        writes += 1
        if path == catalog:
            raise OSError("simulated publish failure")

    monkeypatch.setitem(main.__globals__, "atomic_write_json", fail_after_catalog)
    rc = main(
        [
            "--cockpit-dir",
            str(cockpit),
            "--codex-dir",
            str(codex),
            "--provider-policy",
            str(policy),
            "--backup-root",
            str(tmp_path / "backups"),
            "--apply",
        ]
    )
    assert rc != 0
    assert "backups restored" in capsys.readouterr().err
    assert {path: path.read_bytes() for path in originals} == originals

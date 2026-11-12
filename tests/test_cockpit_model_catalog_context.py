"""Tests for scripts/cockpit_model_catalog_context.py."""

from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path
from typing import Any, cast

MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "cockpit_model_catalog_context.py")
)
collect_findings = cast(Any, MODULE["collect_findings"])
FINDINGS = cast(int, MODULE["FINDINGS"])
CANNOT_CHECK = cast(int, MODULE["CANNOT_CHECK"])
BLOCKED = cast(int, MODULE["BLOCKED"])
main = cast(Any, MODULE["main"])


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(root: Path) -> tuple[Path, Path]:
    codex = root / "codex"
    contracts = root / "overrides.json"
    write(
        contracts,
        {
            "models": [
                {
                    "slug": "glm-5.3",
                    "context_window": 1_000_000,
                    "max_context_window": 1_000_000,
                    "auto_compact_token_limit": 900_000,
                },
                {
                    "slug": "gpt-6-luna",
                    "context_window": 272_000,
                    "max_context_window": 272_000,
                    "auto_compact_token_limit": 244_800,
                },
            ]
        },
    )
    write(
        codex / ".cockpit-experimental-model-catalog-config.json",
        {
            "models": [
                {
                    "model_id": "glm-5.3",
                    "context_window": 1_000_000,
                    "auto_compact_token_limit": 900_000,
                },
                {
                    "model_id": "gpt-6-luna",
                    "context_window": 272_000,
                    "auto_compact_token_limit": 244_800,
                },
            ]
        },
    )
    catalog = codex / "cockpit-model-catalog.json"
    write(
        catalog,
        {
            "models": [
                {
                    "slug": "glm-5.3",
                    "context_window": 1_000_000,
                    "max_context_window": 1_000_000,
                    "auto_compact_token_limit": 900_000,
                },
                {
                    "slug": "gpt-6-luna",
                    "context_window": 272_000,
                    "max_context_window": 272_000,
                    "auto_compact_token_limit": 244_800,
                },
            ]
        },
    )
    write(
        codex / "cockpit-model-catalog.meta.json",
        {"catalogHash": hashlib.sha256(catalog.read_bytes()).hexdigest()},
    )
    return codex, contracts


def test_consistent_editor_and_generated_catalog_pass(tmp_path: Path) -> None:
    codex, contracts = fixture(tmp_path)
    editor, failures = collect_findings(codex, contracts)
    assert editor == ["glm-5.3", "gpt-6-luna"]
    assert failures == []


def test_stale_editor_is_a_failure_even_if_catalog_looks_correct(
    tmp_path: Path,
) -> None:
    codex, contracts = fixture(tmp_path)
    editor = codex / ".cockpit-experimental-model-catalog-config.json"
    document = json.loads(editor.read_text(encoding="utf-8"))
    document["models"][0]["context_window"] = 500_000
    write(editor, document)
    _, failures = collect_findings(codex, contracts)
    assert any(
        "glm-5.3: editor context_window=500000 expected=1000000" in f for f in failures
    )
    assert not any("catalog" in f for f in failures)


def test_hash_mismatch_and_duplicate_catalog_ids_fail_closed(tmp_path: Path) -> None:
    codex, contracts = fixture(tmp_path)
    meta = codex / "cockpit-model-catalog.meta.json"
    document = json.loads(meta.read_text(encoding="utf-8"))
    document["catalogHash"] = "stale"
    write(meta, document)
    _, failures = collect_findings(codex, contracts)
    assert any("metadata hash" in f for f in failures)

    write(meta, document | {"catalogHash": "later"})
    catalog = codex / "cockpit-model-catalog.json"
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["models"].append(payload["models"][0])
    write(catalog, payload)
    meta.write_text(
        json.dumps({"catalogHash": hashlib.sha256(catalog.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )
    try:
        collect_findings(codex, contracts)
    except ValueError as exc:
        assert "duplicate model id" in str(exc)
    else:
        raise AssertionError("duplicate catalog ids must be rejected")


def test_missing_generated_catalog_entry_is_not_a_noop(tmp_path: Path) -> None:
    codex, contracts = fixture(tmp_path)
    catalog = codex / "cockpit-model-catalog.json"
    document = json.loads(catalog.read_text(encoding="utf-8"))
    document["models"] = [
        model for model in document["models"] if model["slug"] != "gpt-6-luna"
    ]
    write(catalog, document)
    write(
        codex / "cockpit-model-catalog.meta.json",
        {"catalogHash": hashlib.sha256(catalog.read_bytes()).hexdigest()},
    )
    _, failures = collect_findings(codex, contracts)
    assert "gpt-6-luna: missing catalog entry" in failures


def test_missing_projection_is_reportable_not_silent(tmp_path: Path) -> None:
    codex, contracts = fixture(tmp_path)
    (codex / ".cockpit-experimental-model-catalog-config.json").unlink()
    assert main(["--codex-dir", str(codex), "--overrides", str(contracts)]) == FINDINGS


def test_unreadable_contract_returns_cannot_check(tmp_path: Path) -> None:
    codex, contracts = fixture(tmp_path)
    contracts.write_text("{broken", encoding="utf-8")
    assert (
        main(["--codex-dir", str(codex), "--overrides", str(contracts)]) == CANNOT_CHECK
    )


def test_apply_requires_closed_cockpit_and_preserves_other_models(
    tmp_path: Path, monkeypatch: Any
) -> None:
    codex, contracts = fixture(tmp_path)
    editor = codex / ".cockpit-experimental-model-catalog-config.json"
    catalog = codex / "cockpit-model-catalog.json"
    document = json.loads(editor.read_text(encoding="utf-8"))
    document["models"][0]["context_window"] = 500_000
    document["models"].append({"model_id": "custom", "context_window": 123})
    write(editor, document)
    monkeypatch.setitem(main.__globals__, "running_cockpit_processes", lambda: [])
    assert (
        main(
            [
                "--codex-dir",
                str(codex),
                "--overrides",
                str(contracts),
                "--apply",
                "--backup-root",
                str(tmp_path / "backups"),
            ]
        )
        == 0
    )
    updated = json.loads(editor.read_text(encoding="utf-8"))
    assert updated["models"][0]["context_window"] == 1_000_000
    assert updated["models"][-1] == {"model_id": "custom", "context_window": 123}
    assert (
        json.loads(
            (codex / "cockpit-model-catalog.meta.json").read_text(encoding="utf-8")
        )["catalogHash"]
        == hashlib.sha256(catalog.read_bytes()).hexdigest()
    )
    assert list((tmp_path / "backups").glob("cockpit-context-pins-*"))


def test_apply_refuses_while_cockpit_runs(tmp_path: Path, monkeypatch: Any) -> None:
    codex, contracts = fixture(tmp_path)
    editor = codex / ".cockpit-experimental-model-catalog-config.json"
    original = editor.read_bytes()
    monkeypatch.setitem(
        main.__globals__, "running_cockpit_processes", lambda: ["cockpit-tools.exe"]
    )
    assert (
        main(
            [
                "--codex-dir",
                str(codex),
                "--overrides",
                str(contracts),
                "--apply",
                "--backup-root",
                str(tmp_path / "backups"),
            ]
        )
        == BLOCKED
    )
    assert editor.read_bytes() == original

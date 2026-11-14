"""Contract tests for the regressions this audit closed.

Each case pins a failure mode that the existing suite could not see: the
defect only appears on an input or an environment the gate never produced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import (
    check_docs,
    cpa_catalog_check,
    cpa_error_dump_forensics,
    hg03_contract,
)


def test_docs_gate_covers_every_current_docs_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # docs/audits was outside DOCS while AGENTS promised that a docs-only
    # change runs check_docs.py, so a broken path in an audit note passed both
    # the local gate and CI. The tree-derived scope must cover it and must keep
    # excluding the historical receipts.
    docs = Path(__file__).resolve().parents[1] / "docs"
    scanned = {doc.resolve() for doc in check_docs.DOCS}
    audits = sorted((docs / "audits").glob("*.md"))
    assert audits, "docs/audits should hold at least one current note"
    for note in audits:
        assert note.resolve() in scanned, note
    evidence = list((docs / "change-evidence").glob("*.md"))
    if evidence:
        assert not any(doc.resolve() in scanned for doc in evidence)

    # A wrong path in an audit note is now a finding instead of silence.
    note = tmp_path / "audit.md"
    note.write_text(
        "Run `scripts/definitely_not_present.py` first.\n", encoding="utf-8"
    )
    monkeypatch.setattr(check_docs, "DOCS", [note])
    errors = check_docs.check()
    assert any("inline path does not exist" in error for error in errors), errors


def test_docs_workflow_watches_the_docs_tree_it_checks() -> None:
    # Two dead edges: HG03_Final_v4/** left the repository on 2026-11-14 (the
    # glob could never match), and docs/audits/** was checked by neither the
    # workflow paths nor check_docs.DOCS.
    import yaml

    text = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "docs.yml"
    ).read_text(encoding="utf-8")
    triggers = yaml.safe_load(text)
    if True in triggers and "on" not in triggers:
        triggers["on"] = triggers.pop(True)
    for event in ("push", "pull_request"):
        paths = triggers["on"][event]["paths"]
        assert "HG03_Final_v4/**" not in paths, event
        assert "docs/**" in paths, event


def test_catalog_check_fails_closed_on_an_empty_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # _load_json returns {} for a syntactically valid EMPTY object without
    # recording anything, so "if not manifest: return errors" reported PASS.
    empty = tmp_path / "cpa_provider_routes.json"
    empty.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cpa_catalog_check, "MANIFEST", empty)
    errors = cpa_catalog_check.check()
    assert any("empty" in error for error in errors), errors
    assert cpa_catalog_check.main([]) == 1


def test_docs_gate_reports_findings_for_a_document_outside_the_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The HG-03 kit lives outside ROOT; relative_to(ROOT) raised ValueError and
    # turned one broken link into a traceback for the whole docs gate.
    kit = tmp_path / "kit"
    kit.mkdir()
    doc = kit / "handoff.md"
    # An inline repository path is checked unconditionally, so this is the
    # finding shape that used to raise ValueError while formatting itself.
    doc.write_text("Run `scripts/definitely_not_present.py` first.\n", encoding="utf-8")
    monkeypatch.setattr(check_docs, "DOCS", [doc])
    errors = check_docs.check()
    assert any("inline path does not exist" in error for error in errors), errors
    assert any(error.startswith(kit.as_posix()) for error in errors), errors


def test_forensics_treats_an_empty_dump_set_as_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No retained dump is not a clean bill of health; it used to exit 0.
    monkeypatch.setattr(cpa_error_dump_forensics, "REMOTE_LOG_DIR", tmp_path)
    assert cpa_error_dump_forensics.main(["--remote"]) == 2
    assert cpa_error_dump_forensics.main([]) == 2


def test_hg03_normalization_round_trips_through_write_bundle(tmp_path: Path) -> None:
    # A status-less bundle became status="UNKNOWN", which its own validator
    # rejects; write_bundle() re-validates, so the evidence write always failed.
    bundle = {
        "schema": hg03_contract.SCHEMA,
        "product": "home_baseline",
        "role": "HOME",
        "commit": "abc123",
        "layer": "repo_verified",
        "checks": [{"kind": "web", "outcome": "UNVERIFIED", "summary": "not run"}],
    }
    normalized = hg03_contract.validate_bundle(dict(bundle))
    assert normalized["status"] in hg03_contract.OUTCOMES
    output = tmp_path / "evidence.json"
    hg03_contract.write_bundle(output, normalized)
    assert (
        json.loads(output.read_text(encoding="utf-8"))["status"] == normalized["status"]
    )


def test_hg03_validate_with_output_writes_the_evidence_file(tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text(
        json.dumps(
            {
                "schema": hg03_contract.SCHEMA,
                "product": "home_baseline",
                "role": "HOME",
                "commit": "abc123",
                "layer": "repo_verified",
                "checks": [
                    {"kind": "web", "outcome": "UNVERIFIED", "summary": "not run"}
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.json"
    assert hg03_contract.main(["--validate", str(source), "--output", str(output)]) == 0
    assert output.exists()


def test_transport_staging_pipeline_enables_pipefail() -> None:
    # Without pipefail a corrupted payload (base64 -d warns and writes an empty
    # file) still ran the next stage against a partial script.
    from scripts import launcher_transport

    command = launcher_transport.remote_temp_python_command(
        payload="QUJD", temp_prefix="fixture", action="true"
    )
    assert "set -o pipefail" in command


def test_a_verified_plan_survives_a_later_save(tmp_path: Path) -> None:
    # save_plan() used to drop every write once the stored plan left
    # planned/ready/blocked -- but workflow marks "applied" BEFORE the adapter
    # runs, so the terminal verified/unverified write never persisted.

    from vps_ssh_launcher.maintenance import state
    from vps_ssh_launcher.maintenance.models import (
        ActionStatus,
        MaintenanceAction,
        MaintenancePlan,
        PlanStatus,
    )

    def plan(status: PlanStatus, action_status: ActionStatus) -> MaintenancePlan:
        return MaintenancePlan(
            plan_id="plan-" + "0" * 16,
            created_at="2026-11-14T00:00:00+00:00",
            policy_fingerprint="sha256:p",
            inventory_fingerprint="sha256:i",
            actions=(
                MaintenanceAction(
                    profile="bwg",
                    resource="docker",
                    desired="upgrade",
                    observed="present",
                    status=action_status,
                    reason="fixture",
                    target="/srv/compose.yml",
                ),
            ),
            status=status,
        )

    database = tmp_path / "state.db"
    state.save_plan(database, plan("planned", "planned"))
    state.save_plan(database, plan("applied", "applied"))
    state.save_plan(database, plan("verified", "verified"))
    assert state.load_plan(database).status == "verified"

    # ... and the terminal state still cannot be rewound by a stale copy.
    state.save_plan(database, plan("planned", "planned"))
    stored = state.load_plan(database)
    assert stored.status == "verified"
    assert [action.status for action in stored.actions] == ["verified"]


def test_receipts_of_one_plan_carry_distinct_ids(tmp_path: Path) -> None:
    from vps_ssh_launcher.maintenance import receipt
    from vps_ssh_launcher.maintenance.models import MaintenancePlan

    plan = MaintenancePlan(
        plan_id="plan-" + "1" * 16,
        created_at="2026-11-14T00:00:00+00:00",
        policy_fingerprint="sha256:p",
        inventory_fingerprint="sha256:i",
        actions=(),
        status="ready",
    )
    first = receipt.write_receipt(tmp_path / "r", plan, outcome="dry_run", reason="a")
    second = receipt.write_receipt(tmp_path / "r", plan, outcome="refused", reason="b")
    ids = {
        json.loads(path.read_text(encoding="utf-8"))["receipt_id"]
        for path in (first, second)
    }
    assert len(ids) == 2


def test_redaction_covers_json_shaped_assignments() -> None:
    from vps_ssh_launcher.maintenance.security import redact_evidence

    for text in (
        '{"password": "hunter2"}',
        '"private_key": "-----BEGIN PRIVATE KEY-----"',
        '{"api_key": "sk-live"}',
    ):
        assert "hunter2" not in redact_evidence(text)
        assert "BEGIN PRIVATE KEY" not in redact_evidence(text)
        assert "sk-live" not in redact_evidence(text)


def test_request_log_audit_survives_a_null_latency(tmp_path: Path) -> None:
    import sqlite3
    import time
    from contextlib import redirect_stdout
    import io

    from scripts import cockpit_request_log_audit

    db = tmp_path / "logs.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "create table request_logs (id integer primary key, timestamp integer, "
            "http_status integer, error_category text, latency_ms integer, "
            "model_id text, gateway_mode text)"
        )
        now_ms = int(time.time() * 1000)
        connection.executemany(
            "insert into request_logs values (?, ?, ?, ?, ?, ?, ?)",
            [
                # The sidecar writes the row before the request finishes, so a
                # long in-flight request has latency_ms = NULL.
                (1, now_ms - 1_000, 200, None, None, "m1", "gw"),
                (2, now_ms - 1_000, 429, "quota_or_rate_limit", None, "m1", "gw"),
            ],
        )
    out = io.StringIO()
    argv = ["audit", "--db", str(db), "--since-minutes", "60"]
    with redirect_stdout(out):
        import sys

        original = sys.argv
        sys.argv = argv
        try:
            code = cockpit_request_log_audit.main()
        finally:
            sys.argv = original
    assert code == 0, out.getvalue()
    assert json.loads(out.getvalue())["total"] == 2


def test_dpapi_helper_accepts_its_declared_parameters() -> None:
    # [CmdletBinding(Mandatory)] is not a CmdletBinding property: the script
    # parsed but threw on every invocation, so the frp acceptance path that
    # unseals its token could never run.
    import shutil
    import subprocess

    script = Path(__file__).resolve().parents[1] / "scripts" / "frp_relay_dpapi.ps1"
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is not available")
    completed = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-Command",
            f"(Get-Command '{script}').Parameters.Keys -join ','",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    for name in ("Mode", "InputPath", "OutputPath"):
        assert name in completed.stdout

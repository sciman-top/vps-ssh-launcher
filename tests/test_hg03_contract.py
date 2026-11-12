"""HG-03 evidence contracts: fail closed on weak or secret-bearing evidence."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest import mock

import pytest

from scripts import hg03_contract


def _bundle(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": hg03_contract.SCHEMA,
        "product": "home_baseline",
        "role": "HOME",
        "commit": "abc123",
        "layer": "repo_verified",
        "checks": [
            {
                "kind": "bwg_regression",
                "outcome": "UNVERIFIED",
                "summary": "live probe not run",
            }
        ],
    }
    value.update(overrides)
    return value


def test_endpoint_reference_strips_capability_and_query() -> None:
    assert (
        hg03_contract.safe_endpoint_reference(
            "https://gateway.example:8443/secret/v1?token=SECRET"
        )
        == "https://gateway.example:8443/<protected>"
    )
    with pytest.raises(hg03_contract.ContractError):
        hg03_contract.safe_endpoint_reference("http://gateway.example/secret")


def test_access_live_acceptance_requires_hotspot_confirmation_and_passes() -> None:
    check = {
        "kind": "cpa",
        "outcome": "PASS",
        "summary": "successful generated response",
        "request_id": "abcdef12-3456",
        "evidence": {"actual_business": "true", "terminal": "PASS"},
    }
    with pytest.raises(hg03_contract.ContractError, match="hotspot"):
        hg03_contract.validate_bundle(
            _bundle(
                product="access_acceptance",
                layer="live_accepted",
                checks=[check],
            )
        )
    with pytest.raises(hg03_contract.ContractError, match="all checks"):
        hg03_contract.validate_bundle(
            _bundle(
                product="access_acceptance",
                layer="live_accepted",
                hotspot_disconnected=True,
                checks=[check, dict(check, kind="web", outcome="FAIL")],
            )
        )
    accepted = hg03_contract.validate_bundle(
        _bundle(
            product="access_acceptance",
            layer="live_accepted",
            hotspot_disconnected=True,
            request_probe=False,
            checks=[check],
        )
    )
    assert accepted["checks"][0]["outcome"] == "PASS"


def test_payload_checks_and_consistency_reject_weak_claims() -> None:
    with pytest.raises(hg03_contract.ContractError, match="actual_business"):
        hg03_contract.validate_bundle(
            _bundle(
                checks=[
                    {
                        "kind": "web",
                        "outcome": "PASS",
                        "summary": "loading",
                        "evidence": {"actual_business": "false"},
                    }
                ]
            )
        )
    with pytest.raises(hg03_contract.ContractError, match="request_id"):
        hg03_contract.validate_bundle(
            _bundle(
                checks=[
                    {
                        "kind": "cpa",
                        "outcome": "PASS",
                        "summary": "sent",
                        "evidence": {"actual_business": "true"},
                    }
                ]
            )
        )
    with pytest.raises(hg03_contract.ContractError, match="request_consistency"):
        hg03_contract.validate_bundle(
            _bundle(
                request_consistency={
                    "request_id": "abcdef12-3456",
                    "server_seen": None,
                    "upstream_contacted": None,
                }
            )
        )


def test_two_consistent_requests_cannot_be_collapsed_into_one_bundle() -> None:
    with pytest.raises(hg03_contract.ContractError, match="conflicts"):
        hg03_contract.validate_bundle(
            _bundle(
                request_consistency={
                    "request_id": "abcdef12-3456",
                    "server_seen": True,
                    "upstream_contacted": None,
                },
                checks=[
                    {
                        "kind": "cpa",
                        "outcome": "PASS",
                        "summary": "first request",
                        "request_id": "abcdef12-3456",
                        "evidence": {"actual_business": "true", "terminal": "PASS"},
                    },
                    {
                        "kind": "sse",
                        "outcome": "PASS",
                        "summary": "second request",
                        "request_id": "fedcba98-7654",
                        "evidence": {"actual_business": "true"},
                    },
                ],
            )
        )
    with pytest.raises(hg03_contract.ContractError, match="server_seen"):
        hg03_contract.validate_bundle(
            _bundle(
                request_consistency={
                    "request_id": "abcdef12-3456",
                    "server_seen": "yes",
                    "upstream_contacted": None,
                }
            )
        )


def test_route_decision_requires_its_evidence_fields() -> None:
    with pytest.raises(hg03_contract.ContractError, match="route_fields"):
        hg03_contract.validate_bundle(_bundle(product="route_decision", route="D"))
    value = hg03_contract.validate_bundle(
        _bundle(
            product="route_decision",
            route="D",
            route_fields={
                "authorization": "approved for test",
                "link": "control probe pending",
                "business": "NOT_RUN",
                "cost": "reuse",
            },
        )
    )
    assert value["route"] == "D"


def test_bundle_rejects_capability_and_credential_literals() -> None:
    for secret in (
        "https://fq.sciman.top:8443/0123456789abcdef/v1",
        "https://unlisted.example:8443/capability/v1?token=SECRET",
        "Bearer SECRET",
        "api_key=SECRET",
    ):
        with pytest.raises(hg03_contract.ContractError, match="capability"):
            hg03_contract.validate_bundle(
                _bundle(
                    checks=[
                        {
                            "kind": "web",
                            "outcome": "UNVERIFIED",
                            "summary": secret,
                        }
                    ]
                )
            )


def test_cpa_pass_requires_protocol_terminal_and_probe_consistency() -> None:
    check = {
        "kind": "cpa",
        "outcome": "PASS",
        "summary": "one-shot generation",
        "request_id": "abcdef12-3456",
        "evidence": {"actual_business": "true", "terminal": "PASS"},
    }
    hg03_contract.validate_bundle(
        _bundle(
            product="access_acceptance",
            layer="repo_verified",
            checks=[check],
        )
    )
    with pytest.raises(hg03_contract.ContractError, match="request_consistency"):
        hg03_contract.validate_bundle(
            _bundle(
                product="access_acceptance",
                layer="repo_verified",
                status="PASS",
                checks=[check],
            )
        )
    assert (
        hg03_contract.validate_bundle(
            _bundle(
                product="access_acceptance",
                layer="repo_verified",
                request_probe=False,
                checks=[
                    {
                        "kind": "cpa",
                        "outcome": "UNVERIFIED",
                        "summary": "business request not issued",
                    }
                ],
            )
        )["request_probe"]
        is False
    )
    with pytest.raises(hg03_contract.ContractError, match="request_consistency"):
        hg03_contract.validate_bundle(
            _bundle(
                product="access_acceptance",
                layer="repo_verified",
                request_probe=True,
                checks=[check],
            )
        )
    with pytest.raises(hg03_contract.ContractError, match="terminal"):
        hg03_contract.validate_bundle(
            _bundle(
                checks=[
                    {
                        **check,
                        "summary": "connection only",
                        "evidence": {"actual_business": "true"},
                    }
                ]
            )
        )


def test_git_pass_requires_downloaded_remote_object() -> None:
    check = {
        "kind": "git",
        "outcome": "PASS",
        "summary": "ls-remote response",
        "evidence": {"actual_business": "true"},
    }
    with pytest.raises(hg03_contract.ContractError, match="remote_object"):
        hg03_contract.validate_bundle(_bundle(checks=[check]))
    accepted = hg03_contract.validate_bundle(
        _bundle(
            checks=[
                {
                    **check,
                    "evidence": {"actual_business": "true", "remote_object": "true"},
                }
            ]
        )
    )
    assert accepted["checks"][0]["outcome"] == "PASS"


def test_evidence_output_is_private_and_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "private" / "evidence.json"
    hg03_contract.write_bundle(output, _bundle())
    assert output.read_text(encoding="utf-8").endswith("\n")
    if os.name == "posix":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(hg03_contract.ContractError, match="already exists"):
        hg03_contract.write_bundle(output, _bundle())
    with pytest.raises(hg03_contract.ContractError, match="outside"):
        hg03_contract.write_bundle(
            Path(hg03_contract.__file__).resolve().parent / "private-evidence.json",
            _bundle(),
        )


def test_contract_cli_validates_without_printing_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "input.json"
    path.write_text(json.dumps(_bundle(goal="SECRET goal")), encoding="utf-8")
    assert hg03_contract.main(["--validate", str(path)]) == 0
    assert capsys.readouterr().out.strip() == "HG03_CONTRACT=PASS"
    path.write_text("{not-json SECRET", encoding="utf-8")
    assert hg03_contract.main(["--validate", str(path)]) == 1
    capture = capsys.readouterr()
    assert "SECRET" not in capture.out + capture.err


def test_one_shot_preserves_request_id_and_redacts_endpoint(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "[model_providers.codex_local_access]\n"
        'base_url = "https://gateway.example:8443/capability/v1"\n'
        'experimental_bearer_token = "SECRET"\n',
        encoding="utf-8",
    )
    result = {
        "result": "PASS",
        "failures": [],
        "request_id": "0123456789abcdef0123456789abcdef",
        "http_status": 200,
        "completed_ms": 900,
        "first_text_ms": 500,
        "total_ms": 1000,
    }
    with mock.patch(
        "scripts.hg03_contract.probe_responses", return_value=result
    ) as generation:
        claim = hg03_contract.build_access_bundle(
            codex_config=config,
            timeout=60,
            model="gpt-6-luna",
            request_probe=True,
            role="HOME",
            commit="abc123",
            route="D",
            layer="live_accepted",
            hotspot_disconnected=None,
            route_fields={"authorization": "existing approved route"},
        )
    generation.assert_called_once()
    encoded = json.dumps(claim, ensure_ascii=False)
    assert "SECRET" not in encoded
    assert "capability" not in encoded
    assert claim["checks"][0]["request_id"] == "0123456789abcdef0123456789abcdef"


def test_local_readiness_is_not_a_business_pass(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "[model_providers.codex_local_access]\n"
        'base_url = "https://gateway.example:8443/capability/v1"\n'
        'experimental_bearer_token = "SECRET"\n',
        encoding="utf-8",
    )
    claim = hg03_contract.build_access_bundle(
        codex_config=config,
        timeout=60,
        model="gpt-6-luna",
        request_probe=False,
        role="HOME",
        commit="abc123",
        route="D",
        layer="repo_verified",
        hotspot_disconnected=None,
        route_fields={"authorization": "existing approved route"},
    )
    accepted = hg03_contract.validate_bundle(claim)
    assert accepted["status"] == "UNVERIFIED"
    assert accepted["checks"][0]["outcome"] == "UNVERIFIED"
    assert accepted["checks"][0]["evidence"]["endpoint"] == (
        "https://gateway.example:8443/<protected>"
    )
    assert "capability" not in json.dumps(accepted)
    assert (
        hg03_contract.main(
            [
                "--cpa-from-codex-config",
                str(config),
                "--layer",
                "repo_verified",
                "--output",
                str(tmp_path / "readiness.json"),
            ]
        )
        == 0
    )


def test_local_readiness_missing_config_writes_safe_unverified_receipt(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "[model_providers.codex_local_access]\n"
        'base_url = "https://gateway.example:8443/capability/v1"\n'
        'experimental_bearer_token = "SECRET"\n',
        encoding="utf-8",
    )
    claim = hg03_contract.build_access_bundle(
        codex_config=config,
        timeout=60,
        model="gpt-6-luna",
        request_probe=False,
        role="HOME",
        commit="abc123",
        route="D",
        layer="repo_verified",
        hotspot_disconnected=None,
        route_fields={"authorization": "existing approved route"},
    )
    accepted = hg03_contract.validate_bundle(claim)
    assert accepted["status"] == "UNVERIFIED"
    assert accepted["checks"][0]["reason_code"] == "probe_not_requested"
    assert accepted["checks"][0]["evidence"]["endpoint"] == (
        "https://gateway.example:8443/<protected>"
    )

    claim = hg03_contract.build_access_bundle(
        codex_config=tmp_path / "missing.toml",
        timeout=60,
        model="gpt-6-luna",
        request_probe=False,
        role="HOME",
        commit="abc123",
        route="D",
        layer="repo_verified",
        hotspot_disconnected=None,
        route_fields={"authorization": "existing approved route"},
    )
    accepted = hg03_contract.validate_bundle(claim)
    assert accepted["status"] == "UNVERIFIED"
    assert accepted["checks"][0]["reason_code"] == "config_unavailable"
    assert accepted["checks"][0]["evidence"]["endpoint"] == "unavailable"


def test_no_observations_is_rejected_without_a_status_exception() -> None:
    with pytest.raises(hg03_contract.ContractError, match="1..32"):
        hg03_contract.validate_bundle(_bundle(checks=[]))
    with pytest.raises(hg03_contract.ContractError, match="1..32"):
        hg03_contract.validate_bundle(_bundle(checks=[], status="PASS"))
    with pytest.raises(hg03_contract.ContractError, match="conflicts"):
        hg03_contract.validate_bundle(
            _bundle(
                status="PASS",
                checks=[
                    {
                        "kind": "web",
                        "outcome": "UNVERIFIED",
                        "summary": "not yet tested",
                    }
                ],
            )
        )

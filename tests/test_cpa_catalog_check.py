"""Contract tests for scripts/cpa_catalog_check.py.

The checker exists to collapse the manual six-place catalog edit listed in
docs/runbooks/cpa-catalog-change-checklist.md into one local command. These
tests pin the two properties that make it worth running: it passes on the
current repository, and it fails on the drift shapes a real catalog mistake
produces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import cpa_catalog_check


def test_current_repository_catalog_is_consistent() -> None:
    assert cpa_catalog_check.check() == []


def test_derived_lanes_match_the_deployable_admission_config() -> None:
    manifest = json.loads(cpa_catalog_check.MANIFEST.read_text(encoding="utf-8"))
    admission = json.loads(
        cpa_catalog_check.ADMISSION_CONFIG.read_text(encoding="utf-8")
    )
    derived = cpa_catalog_check.derive_lanes(manifest, [])
    deployed = {lane["name"]: lane["models"] for lane in admission["lanes"]}
    assert derived == deployed


def test_plugin_lanes_use_their_own_realm_not_a_union() -> None:
    manifest = json.loads(cpa_catalog_check.MANIFEST.read_text(encoding="utf-8"))
    assert cpa_catalog_check.plugin_lane_models(
        manifest, "workbuddy-provider", "cn"
    ) == ["deepseek-v4.1-flash"]
    intl_models = cpa_catalog_check.plugin_lane_models(
        manifest, "workbuddy-ai-provider", "intl"
    )
    assert intl_models == ["deepseek-v4.1-flash-sg"]
    # The fork bridge unfolds to the client names: sg keeps its bare name
    # (fork=true adds the alias alongside it). hy4flash-free retired
    # 2026-11-17 (user directive); deepseek-v4.1-flash-sg stays declared but
    # is closed dark with the INTL plugin disabled in config.yaml.
    assert cpa_catalog_check.plugin_lane_aliases(
        manifest, "workbuddy-ai-provider", intl_models
    ) == ["deepseek-v4.1-flash-intl", "deepseek-v4.1-flash-sg"]
    assert (
        cpa_catalog_check.plugin_lane_models(
            manifest, "workbuddy-ai-provider", "missing"
        )
        == []
    )
    errors: list[str] = []
    derived = cpa_catalog_check.derive_lanes(manifest, errors)
    assert errors == []
    assert derived["workbuddy-oauth"] == ["deepseek-v4.1-flash"]
    assert derived["workbuddy-intl-oauth"] == [
        "deepseek-v4.1-flash-intl",
        "deepseek-v4.1-flash-sg",
    ]


def test_missing_lane_source_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        cpa_catalog_check.LANE_SOURCES,
        "ghost-lane",
        ("oauth_route", "does-not-exist"),
    )
    errors = cpa_catalog_check.check()
    assert any("ghost-lane" in error for error in errors)


def test_lane_model_drift_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    original = cpa_catalog_check.derive_lanes

    def drifted(manifest: dict[str, object], errors: list[str]) -> dict[str, list[str]]:
        lanes = original(manifest, errors)
        lanes["chatgpt-oauth"] = list(lanes["chatgpt-oauth"]) + ["ghost-model"]
        return lanes

    monkeypatch.setattr(cpa_catalog_check, "derive_lanes", drifted)
    errors = cpa_catalog_check.check()
    assert any("ghost-model" in error for error in errors)


def test_deployed_guardrail_lane_tables_must_match_the_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both deployed copies carry a hand-kept lane table.

    The checklist calls those tables an intentional independent oracle, but
    nothing verified that they still match the manifest. A catalog change that
    updates the manifest, the admission config and the test oracle while missing
    one deployed copy failed only at -Apply or in the doctor, against a live
    host -- which is exactly the six-place edit the checker exists to collapse.
    """
    manifest = json.loads(cpa_catalog_check.MANIFEST.read_text(encoding="utf-8"))
    derived = cpa_catalog_check.derive_lanes(manifest, [])
    lane = cpa_catalog_check.CHOICE_LANE
    live = derived[lane]
    assert len(live) >= 2, live

    doctor = cpa_catalog_check.DEPLOYED_LANE_TABLES[-1]
    original = doctor.read_text(encoding="utf-8")
    body = (
        'expected = {\n    "'
        + lane
        + '": [\n'
        + "".join(f'        "{name}",\n' for name in live[:-1])
        + "    ],\n}\n"
    )
    assert 'expected = {\n    "' + lane + '":' in original, "anchor changed"
    start = original.index('expected = {\n    "' + lane + '":')
    end = original.index("\n}\n", start) + 3
    broken = original[:start] + body + original[end:]
    target = tmp_path / "cpa-guardrail-doctor.sh"
    target.write_text(broken, encoding="utf-8")

    monkeypatch.setattr(cpa_catalog_check, "DEPLOYED_LANE_TABLES", (target,))
    errors = cpa_catalog_check.check()
    assert any(lane in error and live[-1] in error for error in errors), errors


def test_lane_that_serves_and_tombstones_the_same_name_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = json.loads(cpa_catalog_check.MANIFEST.read_text(encoding="utf-8"))
    manifest["oauth_exclusions"] = list(manifest["oauth_exclusions"]) + ["gpt-6-luna"]
    drifted = tmp_path / "cpa_provider_routes.json"
    drifted.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(cpa_catalog_check, "MANIFEST", drifted)
    errors = cpa_catalog_check.check()
    assert any("gpt-6-luna" in error and "tombstones" in error for error in errors)


def test_entry_points_must_offer_every_live_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other direction of the choice-site check.

    Only the "retired name must not be offered" half existed, so when
    gpt-6.1-sol/terra were revived on 2026-11-14 the manifest gained two aliases
    while the three operator entry points kept offering the previous two: the
    gateway served names an operator could not select.
    """
    manifest = json.loads(cpa_catalog_check.MANIFEST.read_text(encoding="utf-8"))
    derived = cpa_catalog_check.derive_lanes(manifest, [])
    live = derived[cpa_catalog_check.CHOICE_LANE]
    assert len(live) >= 2, live

    # An entry point offering only the first live alias is behind the manifest.
    entry = tmp_path / "entry.py"
    entry.write_text(
        'parser.add_argument("--model", choices=("' + live[0] + '"))',
        encoding="utf-8",
    )
    monkeypatch.setattr(cpa_catalog_check, "CHOICE_SITES", (entry,))
    errors = cpa_catalog_check.check()
    assert any(
        "does not offer live" in error and live[-1] in error for error in errors
    ), errors

    # Offering all of them clears the finding.
    entry.write_text(
        'parser.add_argument("--model", choices=('
        + ", ".join(f'"{name}"' for name in live)
        + "))",
        encoding="utf-8",
    )
    assert not any(
        "does not offer live" in error for error in cpa_catalog_check.check()
    )


def test_retired_name_in_an_entry_point_choice_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = json.loads(cpa_catalog_check.MANIFEST.read_text(encoding="utf-8"))
    derived = cpa_catalog_check.derive_lanes(manifest, [])
    retired = sorted(
        cpa_catalog_check.tombstones(manifest)
        - cpa_catalog_check.live_aliases(manifest, derived)
    )[0]
    entry = tmp_path / "entry.py"
    body = 'parser.add_argument("--model", choices=("' + retired + '"))'
    entry.write_text(body, encoding="utf-8")
    monkeypatch.setattr(cpa_catalog_check, "CHOICE_SITES", (entry,))
    errors = cpa_catalog_check.check()
    assert any(retired in error for error in errors)


def test_local_cockpit_policy_cannot_retire_a_live_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy = tmp_path / "cockpit_local_provider_policy.json"
    policy.write_text(
        json.dumps({"retired_hosts": [], "retired_models": ["deepseek-v4.1-flash-oc"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cpa_catalog_check, "COCKPIT_POLICY", policy)
    errors = cpa_catalog_check.check()
    assert any(
        "deepseek-v4.1-flash-oc" in error and "retired_models" in error
        for error in errors
    ), errors


def test_context_overrides_must_match_live_manifest_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overrides = tmp_path / "codex-model-context-overrides.json"
    overrides.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {
                        "slug": "hy4flash-free",
                        "context_window": 1000000,
                        "max_context_window": 1000000,
                        "auto_compact_token_limit": 900000,
                    },
                    {
                        "slug": "gpt-6.1-sol",
                        "context_window": 272000,
                        "max_context_window": 272000,
                        "auto_compact_token_limit": 244800,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cpa_catalog_check, "CONTEXT_OVERRIDES", overrides)
    errors = cpa_catalog_check.check()
    assert any("hy4flash-free" in error for error in errors), errors
    assert any("gpt-6.1-sol" in error and "500000" in error for error in errors), errors

    manifest = json.loads(cpa_catalog_check.MANIFEST.read_text(encoding="utf-8"))
    manifest["oauth_settings"]["antigravity"] = [
        entry
        for entry in manifest["oauth_settings"]["antigravity"]
        if entry.get("name") != "gemini-4-argon-high"
    ]
    unpinned = tmp_path / "cpa_provider_routes.json"
    unpinned.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.undo()
    monkeypatch.setattr(cpa_catalog_check, "MANIFEST", unpinned)
    errors = cpa_catalog_check.check()
    assert any(
        "gemini-4-argon-high" in error and "no max-context-length" in error
        for error in errors
    ), errors


def test_context_overrides_compact_threshold_must_be_int_within_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overrides = tmp_path / "codex-model-context-overrides.json"
    overrides.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {
                        "slug": "deepseek-v4.1-flash-oc",
                        "context_window": 1000000,
                        "max_context_window": 1000000,
                        "auto_compact_token_limit": 900001,
                    },
                    {
                        "slug": "glm-5.3",
                        "context_window": 1000000,
                        "max_context_window": 1000000,
                        "auto_compact_token_limit": "256000",
                    },
                    {
                        "slug": "gemini-3.8-flash-high",
                        "context_window": 1048576,
                        "max_context_window": 1048576,
                        "auto_compact_token_limit": 256000,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cpa_catalog_check, "CONTEXT_OVERRIDES", overrides)
    errors = cpa_catalog_check.check()
    assert any(
        "deepseek-v4.1-flash-oc" in error and "between 1 and 900000" in error
        for error in errors
    ), errors
    assert any(
        "glm-5.3" in error and "auto_compact_token_limit='256000'" in error
        for error in errors
    ), errors
    assert not any(
        "gemini-3.8-flash-high" in error and "auto_compact_token_limit" in error
        for error in errors
    ), errors

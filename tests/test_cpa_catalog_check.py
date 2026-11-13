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


def test_plugin_lane_uses_the_widest_realm_not_a_union() -> None:
    manifest = json.loads(cpa_catalog_check.MANIFEST.read_text(encoding="utf-8"))
    # The intl realm is the superset; a union would keep cn's order and the
    # checker would report a phantom drift against cpa-admission.json.
    assert cpa_catalog_check.plugin_lane_models(manifest, "workbuddy") == [
        "deepseek-v4.1-flash",
        "deepseek-v4.1-flash-sg",
        "hy4free",
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

"""WorkBuddy client-surface contract after the 2026-11-17 luode0320 cutover.

The legacy models_cn/models_intl plugin pins died with the mmqz single
plugin. The client surface now has three cooperating layers, each pinned
here: (1) the route manifest's plugin_routes declare the upstream ids each
realm's dynamic catalog must keep narrowed to via oauth-excluded-models,
(2) the oauth_model_aliases fork bridge maps them to their client names,
and (3) hy4flash-free is RETIRED (2026-11-17, user directive) while
deepseek-v4.1-flash-sg stays declared but closed dark — the INTL plugin is
disabled in config.yaml, so re-enabling is one enabled-flag flip. The CN
pool carries the paid primary and glm-5.4 (opened 2026-11-26, user directive).
"""

from __future__ import annotations

import cpa_catalog_expectations
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "scripts" / "cpa_workbuddy_model_pins.ps1"
MANIFEST = ROOT / "scripts" / "remote" / "cpa_provider_routes.json"


def _manifest() -> dict:
    loaded = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_pin_scripts_reference_the_bridge_not_dead_plugin_keys() -> None:
    source = PINS.read_text(encoding="utf-8")
    assert "oauth_model_aliases" in source
    assert "workbuddy-ai-provider" in source
    # The dead mmqz plugin keys must not reappear in the tool that used to
    # write them.
    assert "models_cn" not in source
    assert "models_intl" not in source


def test_manifest_cn_plugin_pins_are_the_primary_and_glm54() -> None:
    routes = _manifest()["plugin_routes"]
    cn = [r for r in routes if r["realm"] == "cn"]
    assert len(cn) == 1
    assert cn[0]["provider"] == "workbuddy-provider"
    # The paid primary, plus glm-5.4 opened as a second pin on the same lane
    # and credential pool (2026-11-26, user directive).
    assert cn[0]["models"] == [
        "deepseek-v4.1-flash",
        "glm-5.3-flash",
        "glm-5.4",
    ]
    assert cn[0]["admission_lane"] == "workbuddy-oauth"


def test_manifest_intl_pin_is_the_single_declared_upstream_id() -> None:
    routes = _manifest()["plugin_routes"]
    intl = [r for r in routes if r["realm"] == "intl"]
    assert len(intl) == 1
    assert intl[0]["provider"] == "workbuddy-ai-provider"
    # deepseek-v4.1-flash-sg keeps the bare client name (fork alias rides on
    # it). It is currently closed dark with the INTL plugin disabled but
    # stays DECLARED for one-flag re-enabling; hy4flash-free retired
    # 2026-11-17 (user directive) and must not return to the pin.
    assert intl[0]["models"] == ["deepseek-v4.1-flash-sg"]
    assert intl[0]["admission_lane"] == "workbuddy-intl-oauth"


def test_fork_bridge_maps_the_intl_client_alias_only() -> None:
    aliases = _manifest()["oauth_model_aliases"]
    # The legacy single-plugin channel must stay gone.
    assert "workbuddy" not in aliases
    entries = aliases["workbuddy-ai-provider"]
    by_alias = {e["alias"]: e for e in entries}
    assert set(by_alias) == {"deepseek-v4.1-flash-intl"}
    # sg keeps its bare client name, so the intl alias is a fork alongside it.
    assert by_alias["deepseek-v4.1-flash-intl"] == {
        "name": "deepseek-v4.1-flash-sg",
        "alias": "deepseek-v4.1-flash-intl",
        "fork": True,
    }


def test_hy4flash_free_is_retired_and_sg_stays_declared() -> None:
    """hy4flash-free retired 2026-11-17; sg/intl kept declared but closed."""

    routes = _manifest()
    # Retired: not pooled, not pinned, not in any expectation list.
    assert "hy4flash-free" not in routes["pooled_aliases"]
    assert "hy4free" not in routes["pooled_aliases"]
    assert "hy4flash-free" not in json.dumps(
        [r["models"] for r in routes["plugin_routes"]]
    )
    models = cpa_catalog_expectations.WORKBUDDY_PLUGIN_MODELS
    admission_models = cpa_catalog_expectations.WORKBUDDY_PLUGIN_ADMISSION_MODELS
    assert "hy4flash-free" not in models["cn"] + models["intl"]
    assert "hy4flash-free" not in admission_models["cn"] + admission_models["intl"]
    assert "hy4free" not in models["cn"] + models["intl"]
    assert "hy4free" not in admission_models["cn"] + admission_models["intl"]
    # Closed-dark keepers: sg stays declared upstream-side and both client
    # names stay on the admission lane for one-line re-enabling.
    assert models["intl"] == ["deepseek-v4.1-flash-sg"]
    assert admission_models["intl"] == [
        "deepseek-v4.1-flash-intl",
        "deepseek-v4.1-flash-sg",
    ]


def test_legacy_workbuddy_provider_route_is_gone() -> None:
    routes = _manifest()["plugin_routes"]
    assert [r["provider"] for r in routes] == [
        "workbuddy-provider",
        "workbuddy-ai-provider",
    ]
    assert not re.search(
        r'"provider":\s*"workbuddy"', MANIFEST.read_text(encoding="utf-8")
    )

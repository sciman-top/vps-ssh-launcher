"""WorkBuddy client-surface contract after the 2026-11-17 luode0320 cutover.

The legacy models_cn/models_intl plugin pins died with the mmqz single
plugin. The client surface now has three cooperating layers, each pinned
here: (1) the route manifest's plugin_routes declare the upstream ids each
realm's dynamic catalog must keep narrowed to via oauth-excluded-models,
(2) the oauth_model_aliases fork bridge maps them to their client names,
and (3) deepseek-v4.1-flash-sg stays declared but closed dark — the INTL
plugin is disabled in config.yaml, so re-enabling is one enabled-flag flip.
The CN pool carries the paid primary, the -wb aliased GLM pair (opened
2026-11-26), and the hy4 flash tier as the PAID pin hy4flash (x0.03;
2026-11-28 user directive — the day opened the x0.00 promo pin
hy4flash-free and moved it to the paid sibling the same day, ahead of the
promo's 2026-11-30 expiry).
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


def test_manifest_cn_plugin_pins_are_the_declared_set() -> None:
    routes = _manifest()["plugin_routes"]
    cn = [r for r in routes if r["realm"] == "cn"]
    assert len(cn) == 1
    assert cn[0]["provider"] == "workbuddy-provider"
    # The paid primary, glm-5.4 opened as a second pin on the same lane and
    # credential pool (2026-11-26, user directive), and the PAID hy4 flash
    # tier (2026-11-28, user directive; the promo pin hy4flash-free lasted
    # most of one day before the user moved it to the paid sibling).
    assert cn[0]["models"] == [
        "deepseek-v4.1-flash",
        "glm-5.3-flash",
        "glm-5.4",
        "hy4flash",
    ]
    assert cn[0]["admission_lane"] == "workbuddy-oauth"


def test_manifest_intl_pin_is_retired() -> None:
    routes = _manifest()["plugin_routes"]
    intl = [r for r in routes if r["realm"] == "intl"]
    assert intl == []


def test_fork_bridge_drops_intl_channel_and_keeps_cn_aliases() -> None:
    aliases = _manifest()["oauth_model_aliases"]
    assert "workbuddy" not in aliases
    assert "workbuddy-ai-provider" not in aliases
    cn_aliases = {e["alias"]: e for e in aliases["workbuddy-provider"]}
    assert set(cn_aliases) == {"glm-5.3-flash-wb", "glm-5.4-wb"}


def test_hy4free_and_promo_pin_never_return_and_intl_stays_retired() -> None:
    """hy4free's upstream id died with the 2026-11-17 rename and must never
    resurface; the INTL lane stays retired; the hy4 flash tier serves on CN
    only as the PAID hy4flash since 2026-11-28 (user directive) — the promo
    id hy4flash-free is back to a tombstone and must not resurface either."""

    routes = _manifest()
    assert "hy4free" not in routes["pooled_aliases"]
    assert "hy4flash-free" not in routes["pooled_aliases"]
    cn_models = [
        model
        for route in routes["plugin_routes"]
        if route["realm"] == "cn"
        for model in route["models"]
    ]
    assert "hy4flash" in cn_models
    assert "hy4free" not in cn_models
    assert "hy4flash-free" not in cn_models
    tombstones = _manifest()["plugin_exclusions"]["workbuddy-provider"]
    assert "hy4flash-free" in tombstones
    assert "hy4flash" not in tombstones
    models = cpa_catalog_expectations.WORKBUDDY_PLUGIN_MODELS
    admission_models = cpa_catalog_expectations.WORKBUDDY_PLUGIN_ADMISSION_MODELS
    assert "intl" not in models
    assert "intl" not in admission_models
    assert models["cn"][-1] == "hy4flash"
    assert admission_models["cn"][-1] == "hy4flash"


def test_legacy_workbuddy_provider_route_is_gone() -> None:
    routes = _manifest()["plugin_routes"]
    assert [r["provider"] for r in routes] == [
        "workbuddy-provider",
    ]
    assert not re.search(
        r'"provider":\s*"workbuddy"', MANIFEST.read_text(encoding="utf-8")
    )

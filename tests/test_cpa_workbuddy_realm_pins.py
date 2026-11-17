"""WorkBuddy client-surface contract after the 2026-11-17 luode0320 cutover.

The legacy models_cn/models_intl plugin pins died with the mmqz single
plugin. The client surface now has three cooperating layers, each pinned
here: (1) the route manifest's plugin_routes declare the upstream ids each
realm's dynamic catalog must keep narrowed to via oauth-excluded-models,
(2) the oauth_model_aliases fork bridge maps them to the legacy client
names, and (3) hy4free stays an INTL-only zero-cost fallback - the CN pool
still carries only the paid primary.
"""

from __future__ import annotations

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


def test_manifest_cn_plugin_pin_is_the_paid_primary_only() -> None:
    routes = _manifest()["plugin_routes"]
    cn = [r for r in routes if r["realm"] == "cn"]
    assert len(cn) == 1
    assert cn[0]["provider"] == "workbuddy-provider"
    assert cn[0]["models"] == ["deepseek-v4.1-flash"]
    assert cn[0]["admission_lane"] == "workbuddy-oauth"


def test_manifest_intl_pins_are_the_two_served_upstream_ids() -> None:
    routes = _manifest()["plugin_routes"]
    intl = [r for r in routes if r["realm"] == "intl"]
    assert len(intl) == 1
    assert intl[0]["provider"] == "workbuddy-ai-provider"
    # deepseek-v4.1-flash-sg keeps the bare client name; hy4flash-free is
    # hy4free's upstream implementation id and must never surface bare.
    assert intl[0]["models"] == ["deepseek-v4.1-flash-sg", "hy4flash-free"]
    assert intl[0]["admission_lane"] == "workbuddy-intl-oauth"


def test_fork_bridge_maps_both_legacy_client_names() -> None:
    aliases = _manifest()["oauth_model_aliases"]
    # The legacy single-plugin channel must stay gone.
    assert "workbuddy" not in aliases
    entries = aliases["workbuddy-ai-provider"]
    by_alias = {e["alias"]: e for e in entries}
    assert set(by_alias) == {"deepseek-v4.1-flash-intl", "hy4free"}
    # sg keeps its bare client name, so the intl alias is a fork alongside it.
    assert by_alias["deepseek-v4.1-flash-intl"] == {
        "name": "deepseek-v4.1-flash-sg",
        "alias": "deepseek-v4.1-flash-intl",
        "fork": True,
    }
    # hy4flash-free must never surface bare: no fork means the alias
    # replaces the upstream id in the client surface.
    assert by_alias["hy4free"] == {"name": "hy4flash-free", "alias": "hy4free"}


def test_hy4free_is_not_a_pooled_alias() -> None:
    """hy4free is exclusive to the independent INTL lane."""

    routes = _manifest()
    assert "hy4free" not in routes["pooled_aliases"]
    holders = [
        r["realm"] for r in routes["plugin_routes"] if "hy4flash-free" in r["models"]
    ]
    assert holders == ["intl"]
    bare_holders = [
        r["realm"] for r in routes["plugin_routes"] if "hy4free" in r["models"]
    ]
    assert bare_holders == []


def test_legacy_workbuddy_provider_route_is_gone() -> None:
    routes = _manifest()["plugin_routes"]
    assert [r["provider"] for r in routes] == [
        "workbuddy-provider",
        "workbuddy-ai-provider",
    ]
    assert not re.search(
        r'"provider":\s*"workbuddy"', MANIFEST.read_text(encoding="utf-8")
    )

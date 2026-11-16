"""Realm pin defaults and the hy4free exclusivity decision.

2026-11-15: hy4free moved out of models_cn. It is a zero-cost (x0.00 credits)
fallback on both realms, so pinning it on both made the CN credentials eligible
for it too. The decision keeps it exclusive to intl so the CN pool carries only
the paid primary. Both realms answer the same upstream id hy4-free, so this is a
candidate-pool narrowing, not a new model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "scripts" / "cpa_workbuddy_model_pins.ps1"
MANIFEST = ROOT / "scripts" / "remote" / "cpa_provider_routes.json"


def _default(flag: str) -> str:
    source = PINS.read_text(encoding="utf-8")
    match = re.search(r"\[string\]" + re.escape(flag) + r' = "([^"]*)"', source)
    assert match, f"{flag} default not found"
    return match.group(1)


def test_cn_default_excludes_the_free_fallback() -> None:
    assert _default("$ModelsCn") == "deepseek-v4.1-flash"


def test_intl_default_keeps_the_free_fallback() -> None:
    assert _default("$ModelsIntl") == (
        "deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free"
    )


def test_realms_share_the_upstream_primary_on_separate_lanes() -> None:
    cn = set(_default("$ModelsCn").split(","))
    intl = set(_default("$ModelsIntl").split(","))
    assert cn == {"deepseek-v4.1-flash"}
    assert "deepseek-v4.1-flash" in intl
    assert "hy4free" in intl and "hy4free" not in cn
    assert cn & intl == {"deepseek-v4.1-flash"}


def test_manifest_matches_the_pin_defaults() -> None:
    routes = json.loads(MANIFEST.read_text(encoding="utf-8"))
    realms = {r["realm"]: set(r["models"]) for r in routes["plugin_routes"]}
    assert realms["cn"] == set(_default("$ModelsCn").split(","))
    assert realms["intl"] == set(_default("$ModelsIntl").split(","))
    roles = {
        route["realm"]: route["admission_lane"] for route in routes["plugin_routes"]
    }
    assert roles == {
        "cn": "workbuddy-oauth",
        "intl": "workbuddy-intl-oauth",
    }


def test_hy4free_is_not_a_pooled_alias() -> None:
    """hy4free is exclusive to the independent INTL lane."""

    routes = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "hy4free" not in routes["pooled_aliases"]
    holders = [r["realm"] for r in routes["plugin_routes"] if "hy4free" in r["models"]]
    assert holders == ["intl"]


def test_shared_upstream_pin_maps_to_the_intl_workbuddy_alias() -> None:
    routes = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # CN and INTL are separate client/admission identities. The shared
    # upstream name is translated only in the INTL plugin realm; it is not a
    # cross-provider pooled alias.
    assert routes["pooled_aliases"] == []
    alias_entries = routes["oauth_model_aliases"]["workbuddy"]
    assert alias_entries == [
        {
            "name": "deepseek-v4.1-flash",
            "alias": "deepseek-v4.1-flash-intl",
            "fork": True,
        }
    ]
    intl = next(r for r in routes["plugin_routes"] if r["realm"] == "intl")
    assert "deepseek-v4.1-flash" in intl["models"]
    assert "deepseek-v4.1-flash-intl" not in intl["models"]

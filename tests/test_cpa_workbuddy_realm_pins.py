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


def test_realms_stay_disjoint_except_the_paid_primary() -> None:
    cn = set(_default("$ModelsCn").split(","))
    intl = set(_default("$ModelsIntl").split(","))
    assert cn & intl == {"deepseek-v4.1-flash"}
    assert "hy4free" in intl and "hy4free" not in cn


def test_manifest_matches_the_pin_defaults() -> None:
    routes = json.loads(MANIFEST.read_text(encoding="utf-8"))
    realms = {r["realm"]: set(r["models"]) for r in routes["plugin_routes"]}
    assert realms["cn"] == set(_default("$ModelsCn").split(","))
    assert realms["intl"] == set(_default("$ModelsIntl").split(","))


def test_hy4free_is_no_longer_a_pooled_alias() -> None:
    """Only one route declares hy4free, so keeping it pooled would fail the audit."""

    routes = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "hy4free" not in routes["pooled_aliases"]
    holders = [r["realm"] for r in routes["plugin_routes"] if "hy4free" in r["models"]]
    assert holders == ["intl"]


def test_deepseek_flash_stays_pooled_across_realms() -> None:
    routes = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "deepseek-v4.1-flash" in routes["pooled_aliases"]
    holders = [
        r["realm"]
        for r in routes["plugin_routes"]
        if "deepseek-v4.1-flash" in r["models"]
    ]
    assert holders == ["cn", "intl"]

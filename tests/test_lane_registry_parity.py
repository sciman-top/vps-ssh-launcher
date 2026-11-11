"""Lane-name parity between the drift gate and the recovery dispatcher.

proxy_hardening_lane_recover fails closed on any drift name it does not
know, so a lane registered in the drift gate must also appear in the
dispatcher's laneScripts table, and a name only present in that table is
either an operator-decision lane or registry debt. This test turns the
runtime failure on a zz scheduled task into a gate-time failure here.

The lane-acceptance probe IDs (``record <probe>``) are deliberately NOT
part of this parity check: probes are per-transport checks (reality-xray,
cdn-ws, ...) that intentionally do not map 1:1 onto drift lane names.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIFT = ROOT / "scripts" / "proxy_hardening_drift.ps1"
RECOVER = ROOT / "scripts" / "proxy_hardening_lane_recover.ps1"

_DRIFT_NAME = re.compile(r"drift\+=\('([a-z0-9-]+)'\)")
_RECOVER_KEY = re.compile(r'^\s*"([a-z0-9-]+)"\s+= @\(', re.MULTILINE)

# Lanes with no projector are deliberate operator decisions, not registry
# debt; converting one into a blind replay must be a reviewed change.
_OPERATOR_DECISION_LANES = (
    "reality-target",
    "reality-flow",
    "xray-port-collision",
    "cdn-fallback",
    "tls-expiry",
    "google-ipv4",
    "v2ray-updater",
    "host-security",
)


def _lane_scripts_table() -> str:
    text = RECOVER.read_text(encoding="utf-8")
    start = text.index("$laneScripts = [ordered]@{")
    end = text.index("}", start)
    return text[start:end]


class LaneRegistryParityTests(unittest.TestCase):
    def test_drift_names_match_recovery_dispatch_table(self) -> None:
        drift_names = set(_DRIFT_NAME.findall(DRIFT.read_text(encoding="utf-8")))
        recover_keys = set(_RECOVER_KEY.findall(_lane_scripts_table()))
        self.assertTrue(
            drift_names, "the drift gate must report at least one lane name"
        )
        self.assertEqual(
            drift_names,
            recover_keys,
            "Lane registries diverged; a lane must be registered in BOTH the "
            "drift gate (drift+=('name')) and the recovery dispatcher "
            "(laneScripts). drift-only="
            f"{sorted(drift_names - recover_keys)} recover-only="
            f"{sorted(recover_keys - drift_names)}",
        )

    def test_operator_decision_lanes_stay_unmapped(self) -> None:
        table = _lane_scripts_table()
        for name in _OPERATOR_DECISION_LANES:
            with self.subTest(lane=name):
                self.assertIsNotNone(
                    re.search(rf'"{name}"\s+= @\(\)', table),
                    f"lane {name!r} must stay an operator decision (@()); "
                    "converting it into a blind replay is a reviewed change",
                )


if __name__ == "__main__":
    unittest.main()

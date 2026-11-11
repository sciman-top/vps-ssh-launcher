"""Contract tests for the opt-in hardening recovery dispatcher."""

from __future__ import annotations

import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "proxy_hardening_lane_recover.ps1"
)


class ProxyHardeningLaneRecoverTests(ScriptValidationMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_preview_is_default_and_scope_is_explicit(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn("RECOVERY_APPLY_REQUIRED", text)
        self.assertIn(
            "Automatic hardening recovery is currently restricted to zz.", text
        )

    def test_reuses_existing_lanes_and_fails_closed(self) -> None:
        text = self.text
        for script in (
            "nginx_camouflage_site.ps1",
            "reality_shortid_harden.ps1",
            "reality_alt_sni_lane.ps1",
            "nginx_tls_stealth.ps1",
            "zz_xhttp_nginx_front.ps1",
            "hysteria2_obfs_lane.ps1",
            "hysteria2_masquerade_lane.ps1",
            "stale_firewall_hygiene.ps1",
            "merged_subscription_lane.ps1",
            "merged-sub-fetch",
            "zz_subscription_republish.ps1",
            "firewall_baseline.ps1",
        ):
            with self.subTest(script=script):
                self.assertIn(script, text)
        self.assertIn("require operator decisions, not blind lane replay", text)
        # zz-only dispatcher: the xhttp-front lane must map to the zz variant.
        self.assertIn('"xhttp-front"          = @("zz_xhttp_nginx_front.ps1")', text)
        # The updater lane is audited by drift but not blindly replayable:
        # recovery needs the pin-bearing projector -Apply.
        self.assertIn('"v2ray-updater"        = @()', text)
        # Obfs replay stays fail-closed on the separated v2 password file;
        # resolution lives in the shared lib (the v2/v1 literals are pinned
        # by the maintenance-cron contract test against the lib itself).
        self.assertIn(
            "Resolve-LaneObfsPasswordFile -ConfigPath $Config -Profile $Profile -SeparatedOnly",
            text,
        )
        self.assertIn("Separated obfs password file missing for replay", text)
        self.assertIn('"-ObfsPasswordFile", $passwordFile', text)
        self.assertIn("RECOVERY_RESULT=RECOVERED", text)

    def test_recovery_requires_post_gate_green(self) -> None:
        text = self.text
        self.assertIn("proxy_hardening_drift.ps1", text)
        self.assertIn("HARDENING_DRIFT=NONE", text)
        self.assertIn("Recovery did not restore a clean hardening gate", text)
        self.assertIn("RECOVERY_RESULT=NONE", text)

    def test_drift_exit_code_survives_shared_ssh_wrapper(self) -> None:
        # The shared transport throws on every non-zero remote exit. Without
        # an explicit remap, the drift helper exits 1 and this dispatcher
        # mistakes an actionable `HARDENING_DRIFT=...` verdict for a tool
        # failure before it can print a PLAN or enforce the unmapped-lane
        # fail-closed decision.
        drift = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "proxy_hardening_drift.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Remote command failed with exit code 3\\.",
            drift,
        )
        self.assertIn("exit 3", drift)


if __name__ == "__main__":
    unittest.main()

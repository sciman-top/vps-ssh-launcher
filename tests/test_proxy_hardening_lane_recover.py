"""Contract tests for the opt-in hardening recovery dispatcher."""

from __future__ import annotations

import subprocess
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
        self.assertIn('"$Profile-hysteria2-obfs-password-v2.txt"', text)
        self.assertIn('"-ObfsPasswordFile", $passwordFile', text)
        self.assertIn("RECOVERY_RESULT=RECOVERED", text)

    def test_embedded_drift_payload_is_bash_syntax(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "proxy_hardening_drift.ps1"
        ).read_text(encoding="utf-8")
        payload = text.split("$remoteCommand = @'\n", 1)[1].split("\n'@", 1)[0]
        payload = payload.replace("\r\n", "\n").replace("\r", "\n")
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        result = subprocess.run(
            self._bash_command(bash, "-n"),
            input=payload,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_requires_post_gate_green(self) -> None:
        text = self.text
        self.assertIn("proxy_hardening_drift.ps1", text)
        self.assertIn("HARDENING_DRIFT=NONE", text)
        self.assertIn("Recovery did not restore a clean hardening gate", text)
        self.assertIn("RECOVERY_RESULT=NONE", text)


if __name__ == "__main__":
    unittest.main()

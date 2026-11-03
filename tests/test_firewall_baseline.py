"""Contract tests for the managed host firewall baseline."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "firewall_baseline.ps1"
LIB = ROOT / "scripts" / "lib" / "proxy_firewall_contract.ps1"


class FirewallBaselineTests(ScriptValidationMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")
        self.lib = LIB.read_text(encoding="utf-8")

    def test_defaults_to_read_only_and_owns_only_its_table(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn("[ \"$APPLY\" != '1' ]", text)
        self.assertIn("table inet vps_launcher_guard", self.lib)
        self.assertIn("systemctl enable --now", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertNotIn("ufw enable", text)
        self.assertNotIn("iptables -F", text)
        self.assertNotIn("nft flush ruleset", text)

    def test_contract_allows_only_known_public_services(self) -> None:
        lib = self.lib
        self.assertIn(
            "bwg = @(443, 2087, 8443, 10094, 21470, 28317, 29712, 33387)",
            lib,
        )
        self.assertIn(
            "zz  = @(443, 2087, 10094, 12378, 21470, 31854, 35622)",
            lib,
        )
        self.assertIn("bwg = @(443, 8444)", lib)
        self.assertIn("zz  = @(443, 8444)", lib)
        self.assertIn("policy drop;", lib)
        self.assertIn("ct state established,related accept", lib)
        self.assertIn('iif "lo" accept', lib)
        self.assertIn("Hysteria2HopStart = 20000", lib)
        self.assertIn("Hysteria2HopEnd = 20999", lib)
        self.assertIn("redirect to :__HOP_TARGET__", lib)
        self.assertIn(
            'Replace("__HOP_TARGET__", "$($contract.Hysteria2HopTarget)")', lib
        )
        self.assertIn("HYSTERIA2_HOP_PORTS=", self.text)

    def test_apply_validates_backs_up_and_disarms_signals(self) -> None:
        text = self.text
        self.assertIn('nft -c -f "$tmp_rules"', text)
        self.assertIn("cp -a", text)
        self.assertIn("trap rollback ERR", text)
        self.assertIn("trap 'rollback 129' HUP", text)
        self.assertIn("trap - ERR HUP INT TERM", text)
        self.assertIn("systemctl daemon-reload", text)
        self.assertIn("systemctl is-enabled --quiet", text)
        self.assertIn("systemctl is-active --quiet", text)

    def test_embedded_bash_payload_is_syntax_valid(self) -> None:
        payload = self.text.split("$remoteCommand = @'\n", 1)[1].split("\n'@", 1)[0]
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


if __name__ == "__main__":
    unittest.main()

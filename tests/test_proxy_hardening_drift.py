"""Contract tests for scripts/proxy_hardening_drift.ps1.

The drift gate answers one question: did a vasma reinstall (or menu
re-configuration) quietly undo a hardening lane while every service still
looks healthy? It must be read-only, name the regressed lanes, and exit
non-zero only when something drifted.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "proxy_hardening_drift.ps1"


class ProxyHardeningDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_reports_single_greppable_verdict(self) -> None:
        text = self.text
        self.assertIn("HARDENING_DRIFT=NONE", text)
        self.assertIn('echo "HARDENING_DRIFT=$(IFS=,; echo "${drift[*]}")"', text)
        self.assertIn("exit 3", text)

    def test_names_each_hardening_lane(self) -> None:
        text = self.text
        for lane in ("camouflage", "reality-shortid", "hysteria2-obfs"):
            with self.subTest(lane=lane):
                self.assertIn(f"drift+=('{lane}')", text)

    def test_is_read_only(self) -> None:
        text = self.text
        # No writes, no restarts, no Apply gate: this lane only observes.
        self.assertNotIn("[switch]$Apply", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("sing-box merge", text)
        self.assertNotIn("nginx -t", text)
        self.assertNotIn("rm -f", text)

    def test_stock_nginx_page_counts_as_drift(self) -> None:
        # The exact fingerprint the camouflage lane removes; its return means
        # the page was reverted even if the site still answers 200.
        self.assertIn("Welcome to nginx!", self.text)

    def test_listener_probe_is_exact_suffix(self) -> None:
        # ":443" must not also match ":4433".
        self.assertIn("index($4, p) == length($4) - length(p) + 1", self.text)

    def test_invalid_json_is_drift_not_clean(self) -> None:
        # A malformed source fragment must not be swallowed as an empty scan.
        self.assertIn("invalid = True", self.text)
        self.assertIn("raise SystemExit(2 if invalid else 0)", self.text)


if __name__ == "__main__":
    unittest.main()

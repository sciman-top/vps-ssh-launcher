"""Fleet-wide signal-safety contract for the hardening lane payloads.

A dropped SSH channel Hangups the remote payload. b4437e8 routed INT/TERM
into rollback; the 2026-11-03 orphaned proof clients then proved that HUP
arrives the same way and kills an untrapped payload without ever running
its EXIT trap. Every lane payload must therefore roll back on
HUP/INT/TERM, and the alt-SNI lane must additionally kill its spawned
proof client during restore.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

ROLLBACK_FORM_LANES = (
    "cdn_fallback_lane.ps1",
    "hysteria2_masquerade_lane.ps1",
    "hysteria2_obfs_lane.ps1",
    "nginx_camouflage_site.ps1",
    "nginx_tls_stealth.ps1",
    "reality_shortid_harden.ps1",
    "stale_firewall_hygiene.ps1",
    "xhttp_nginx_front.ps1",
    "zz_xhttp_nginx_front.ps1",
)


class LaneSignalSafetyTests(unittest.TestCase):
    def test_rollback_lanes_roll_back_on_hup_int_term(self) -> None:
        for name in ROLLBACK_FORM_LANES:
            with self.subTest(lane=name):
                text = (SCRIPTS / name).read_text(encoding="utf-8")
                self.assertIn("trap 'rollback 129' HUP", text)
                self.assertIn("trap 'rollback 130' INT", text)
                self.assertIn("trap 'rollback 143' TERM", text)

    def test_rollback_traps_appear_exactly_once_per_lane(self) -> None:
        # A duplicated trap would silently override the first registration
        # and desynchronize the rollback code from the intended exit code.
        for name in ROLLBACK_FORM_LANES:
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            for trap in (
                "trap 'rollback 129' HUP",
                "trap 'rollback 130' INT",
                "trap 'rollback 143' TERM",
            ):
                with self.subTest(lane=name, trap=trap):
                    self.assertEqual(text.count(trap), 1)

    def test_alt_sni_proof_client_is_signal_safe(self) -> None:
        # The alt-SNI lane restores through an EXIT trap instead of a
        # rollback ERR trap, so its signal form is `exit` into restore, and
        # restore must kill the spawned proof client (3 orphans observed on
        # 2026-11-03 across both machines).
        text = (SCRIPTS / "reality_alt_sni_lane.ps1").read_text(encoding="utf-8")
        self.assertIn("trap 'exit 129' HUP", text)
        self.assertIn("trap 'exit 130' INT", text)
        self.assertIn("trap 'exit 143' TERM", text)
        self.assertIn('kill "$CLIENT_PID" 2>/dev/null || true', text)
        self.assertIn('wait "$CLIENT_PID" 2>/dev/null || true', text)
        self.assertIn('CLIENT_PID=""', text)


if __name__ == "__main__":
    unittest.main()

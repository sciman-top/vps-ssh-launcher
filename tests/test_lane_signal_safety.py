"""Fleet-wide signal-safety contract for transactional proxy payloads.

A dropped SSH channel Hangups the remote payload. b4437e8 routed INT/TERM
into rollback; the 2026-11-03 orphaned proof clients then proved that HUP
arrives the same way and kills an untrapped payload without ever running
its EXIT trap. Every write payload must therefore roll back on HUP/INT/TERM,
then disarm all three once the write is proven, and the alt-SNI/acceptance
lanes must additionally kill spawned clients during restore.
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

TEMPLATE_ROLLBACK_LANES = (
    "cdn_origin_cert_sync.ps1",
    "merged_subscription_lane.ps1",
    "zz_subscription_republish.ps1",
)

EXIT_ROLLBACK_TEMPLATES = (
    "v2ray_agent_renewtls_cron.ps1",
    "v2ray_agent_script_update_cron.ps1",
)

ERR_ROLLBACK_TEMPLATES = (
    "system_maintenance_cron.ps1",
    "vasma_kernel_update_cron.ps1",
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

    def test_hup_trap_is_disarmed_for_rollback_and_commit(self) -> None:
        # `trap - ERR INT TERM` leaves SIGHUP armed. Re-entering rollback on a
        # second hangup, or rolling back after a successful apply, would turn
        # transport loss into a production config change.
        for name in ROLLBACK_FORM_LANES:
            with self.subTest(lane=name):
                text = (SCRIPTS / name).read_text(encoding="utf-8")
                self.assertNotIn("trap - ERR INT TERM", text)
                self.assertGreaterEqual(text.count("trap - ERR HUP INT TERM"), 2)

    def test_subscription_and_certificate_payloads_disarm_hup(self) -> None:
        for name in TEMPLATE_ROLLBACK_LANES:
            with self.subTest(template=name):
                text = (SCRIPTS / name).read_text(encoding="utf-8")
                self.assertIn("trap 'rollback 129' HUP", text)
                self.assertNotIn("trap - ERR INT TERM", text)
                self.assertGreaterEqual(text.count("trap - ERR HUP INT TERM"), 2)

    def test_exit_rollback_payloads_disarm_hup(self) -> None:
        for name in EXIT_ROLLBACK_TEMPLATES:
            with self.subTest(template=name):
                text = (SCRIPTS / name).read_text(encoding="utf-8")
                self.assertNotIn("trap - EXIT INT TERM", text)
                self.assertGreaterEqual(text.count("trap - EXIT HUP INT TERM"), 2)

    def test_vasma_and_maintenance_payloads_disarm_hup(self) -> None:
        for name in ERR_ROLLBACK_TEMPLATES:
            with self.subTest(template=name):
                text = (SCRIPTS / name).read_text(encoding="utf-8")
                self.assertNotIn("trap - ERR INT TERM", text)
                self.assertGreaterEqual(text.count("trap - ERR HUP INT TERM"), 2)

    def test_remote_vasma_updater_disarms_hup_after_commit(self) -> None:
        text = (SCRIPTS / "remote" / "v2ray-agent-script-update.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("trap - ERR INT TERM", text)
        self.assertGreaterEqual(text.count("trap - ERR HUP INT TERM"), 2)

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

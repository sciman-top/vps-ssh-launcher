"""Safety-contract assertions for scripts/hysteria2_masquerade_lane.ps1."""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "hysteria2_masquerade_lane.ps1"
)


class Hysteria2MasqueradeLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_apply_is_opt_in_and_idempotent(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn("if [ \"$APPLY\" != '1' ]; then", text)
        self.assertIn("masquerade=already_configured", text)
        self.assertIn("write_status noop 0", text)
        # bwg-only: zz runs a reality-only sing-box lane with no Hysteria2.
        self.assertIn('[ValidateSet("bwg")]', text)

    def test_masquerade_contract_targets_base_lane_only(self) -> None:
        text = self.text
        # Proxy masquerade reaches the HTTPS camouflage vhost with a rewritten
        # Host (sing-box 1.14 file masquerade has no path/name field).
        self.assertIn('"type": "proxy"', text)
        self.assertIn('"rewrite_host": True', text)
        self.assertIn("__MASQUERADE_URL__", text)
        self.assertIn('"log": {"level": "info"}', text)
        # Only the un-obfuscated base lane on UDP 443 is covered; the obfs
        # lane rejects non-obfuscated packets before the protocol layer.
        self.assertIn("ib.get('listen_port') == 443", text)
        self.assertIn("97_vps_ssh_launcher_singbox_log.json", text)
        self.assertIn("06_hysteria2_inbounds.json", text)
        self.assertIn("HYSTERIA2_MASQUERADE_APPLIED", text)
        self.assertIn("BASE_HY2_PORT_NOT_LISTENING", text)
        # Upstream vasma stays untouched.
        self.assertNotIn("/etc/v2ray-agent/install.sh", text)

    def test_backup_first_and_verified_rollback(self) -> None:
        text = self.text
        self.assertIn("flock -n 9", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("ROLLBACK_FAILED", text)
        self.assertIn("SINGBOX_MERGE_FAILED", text)
        self.assertIn("SINGBOX_CHECK_FAILED", text)
        self.assertIn("MERGE_CONTRACT_FAILED", text)
        self.assertIn("service_active_before=0", text)
        self.assertIn(
            'cmp -s "$backup_dir/config.json" "$SB_CONF_DIR/config.json"', text
        )
        self.assertIn('"$SB_BINARY" check -c "$SB_CONF_DIR/config.json"', text)
        # The projected log fragment must disappear again when it was absent
        # before the failed apply.
        self.assertIn("log-fragment.absent", text)
        self.assertIn('rm -f "$LOG_FRAGMENT" || rollback_failed=1', text)

    def test_masquerade_url_validation(self) -> None:
        text = self.text
        self.assertIn(
            "-MasqueradeUrl must be an https:// URL literal without shell syntax",
            text,
        )


if __name__ == "__main__":
    unittest.main()

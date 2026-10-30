"""Contract tests for scripts/hysteria2_obfs_lane.ps1.

The lane adds an obfuscated Hysteria2 inbound on its own UDP port without
touching the existing lane or its subscription. Tests pin that shape.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hysteria2_obfs_lane.ps1"


class Hysteria2ObfsLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_apply_is_opt_in_and_idempotent(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn("if [ \"$APPLY\" != '1' ]; then", text)
        self.assertIn("obfs_lane=already_configured", text)
        self.assertIn("obfs_lane=absent", text)
        self.assertIn("write_status noop 0", text)

    def test_additive_only_and_base_lane_guarded(self) -> None:
        text = self.text
        # The existing 443 Hysteria2 lane must still be listening after apply.
        self.assertIn("BASE_HY2_PORT_LOST", text)
        self.assertIn("OBFS_PORT_NOT_LISTENING", text)
        self.assertIn("salamander", text)
        self.assertIn("base_port=", text)
        self.assertIn('grep -q ":$base_port "', text)
        self.assertIn("config.absent", text)
        self.assertIn('"$SB_BINARY" check -c "$SB_CONF_DIR/config.json"', text)
        self.assertIn(
            'cmp -s "$backup_dir/config.json" "$SB_CONF_DIR/config.json"', text
        )
        # An inactive-before service in the config.absent edge may restore to
        # inactive; only a previously running (or restarted) service must come
        # back active, so a correct absent-state rollback is not a false FAIL.
        self.assertIn("service_active_before=0", text)
        self.assertIn(
            'if [ "$service_active_before" -eq 1 ] ||'
            ' [ "$service_restarted" -eq 1 ]; then',
            text,
        )
        # Writes are serialized and backup-first with a verified rollback.
        self.assertIn("flock -n 9", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("ROLLBACK_FAILED", text)
        # Upstream vasma stays untouched.
        self.assertNotIn("/etc/v2ray-agent/install.sh", text)

    def test_apply_requires_obfs_password(self) -> None:
        self.assertIn("REFUSE obfs password is required for -Apply", self.text)
        self.assertIn("[string]$ObfsPassword", self.text)


if __name__ == "__main__":
    unittest.main()

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
        # Port presence alone can be a stale merged config: the daemon may be
        # listening with the obfs object removed or a different salted key.
        self.assertIn("OBFS_RUNTIME_CONTRACT_MISSING", text)
        self.assertIn("OBFS_RUNTIME_PASSWORD_DRIFT", text)
        self.assertIn("OBFS_RUNTIME_CONTRACT_FAILED", text)
        self.assertIn('runtime_password="$(python3 - "$SB_CONF_DIR/config.json"', text)
        self.assertIn("reason=stale_merge", text)
        self.assertIn("obfs_lane=rotation_pending", text)
        self.assertIn("obfs_lane=drift reason=key_reuse", text)
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

    def test_apply_uses_a_distinct_generated_obfs_secret(self) -> None:
        text = self.text
        self.assertIn("REFUSE obfs password is required for -Apply", text)
        self.assertIn("[string]$ObfsPassword", text)
        self.assertIn("[string]$ObfsPasswordFile", text)
        self.assertIn("hysteria2-obfs-password-v2.txt", text)
        self.assertIn(
            "[Security.Cryptography.RandomNumberGenerator]::Fill($passwordBytes)",
            text,
        )
        self.assertIn("passwordGenerated = $true", text)
        # Hysteria authentication and salamander obfs are separate protocol
        # layers. Reusing the base user's password as the obfs key defeats that
        # separation and the old fragment used exactly that unsafe shape.
        self.assertIn("auth_password=", text)
        self.assertIn("base_auth_password", text)
        self.assertIn("obfs_password != auth_password", text)
        self.assertIn(
            '"users": [{"password": auth_password, "name": "hy2-obfs"}]', text
        )
        self.assertIn('"obfs": {"type": "salamander", "password": obfs_password}', text)
        self.assertIn("obfs_lane=drift reason=key_reuse", text)

    def test_local_obfs_secret_file_is_acl_protected(self) -> None:
        text = self.text
        self.assertIn("SetAccessRuleProtection($true, $false)", text)
        self.assertIn('SecurityIdentifier("S-1-5-18")', text)
        self.assertIn('SecurityIdentifier("S-1-5-32-544")', text)
        self.assertIn("WindowsIdentity]::GetCurrent().User", text)
        self.assertIn("AreAccessRulesProtected", text)
        self.assertIn("unverifiable private ACL", text)


if __name__ == "__main__":
    unittest.main()

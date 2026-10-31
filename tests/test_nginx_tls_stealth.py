"""Contract tests for scripts/nginx_tls_stealth.ps1.

The lane removes two observable fingerprints: the exact nginx build in the
`Server` header and the fallback vhost answering any SNI with this host's own
certificate. It must stay read-only until -Apply, reload rather than restart,
and roll back if the real SNI stops working.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nginx_tls_stealth.ps1"


class NginxTlsStealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_is_read_only_until_apply(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn('APPLY="__APPLY__"', text)
        self.assertIn("if [ \"$APPLY\" != '1' ]; then", text)
        self.assertIn("LOCK_FILE", text)
        self.assertIn("flock -n 9", text)
        # A reload keeps existing tunnels alive; a restart would not.
        self.assertIn("systemctl reload nginx", text)
        self.assertNotIn("systemctl restart nginx", text)

    def test_ports_come_from_live_reality_config(self) -> None:
        text = self.text
        # bwg falls back to 33387 and zz to 35622; deriving both cores avoids a
        # per-profile table that would rot.
        self.assertIn("realitySettings", text)
        self.assertIn("handshake", text)
        self.assertIn("serverNames", text)
        self.assertIn("facts=not-applicable", text)
        self.assertIn("conflict port=", text)

    def test_renders_version_and_unknown_sni_fixes(self) -> None:
        text = self.text
        self.assertIn("server_tokens off;", text)
        self.assertIn("ssl_reject_handshake on;", text)
        self.assertIn("ssl default_server;", text)
        self.assertIn("vps-launcher-stealth.conf", text)
        self.assertIn("listen [::]:%s ssl default_server;", text)

    def test_verifies_both_directions_before_and_after_apply(self) -> None:
        text = self.text
        self.assertIn("real_sni_broken", text)
        self.assertIn("unknown_sni_rejected", text)
        self.assertIn("server_version_leak", text)
        self.assertIn("STEALTH_VERIFY_FAILED", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("nginx -t", text)


if __name__ == "__main__":
    unittest.main()

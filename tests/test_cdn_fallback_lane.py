"""Contract tests for scripts/cdn_fallback_lane.ps1.

The Cloudflare lane is the only entry that keeps working when the origin IP is
blocked, so its shape is pinned: proxied DNS, TLS terminated on the origin,
a loopback VLESS+WS hop, and the decoy page for every other path.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cdn_fallback_lane.ps1"


class CdnFallbackLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_is_read_only_until_apply(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn('APPLY="__APPLY__"', text)
        self.assertIn("if [ \"$APPLY\" != '1' ]; then", text)
        self.assertIn("flock -n 9", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("run -test -confdir", text)
        self.assertIn("STATUS_FILE", text)

    def test_dns_record_must_be_proxied(self) -> None:
        text = self.text
        # A DNS-only record resolves to the origin and silently removes the only
        # lane that survives an origin-IP block.
        self.assertIn("'proxied': True", text)
        self.assertIn("[ \"$dns_status\" = 'proxied' ]", text)
        self.assertIn("CDN_LANE_CF_API_ERROR", text)
        self.assertIn("SAVED_CF_Token", text)

    def test_reuses_the_existing_vless_credential(self) -> None:
        text = self.text
        # No new secret is invented: the lane borrows the existing VLESS UUID.
        self.assertIn("VPS_CDN_UUID", text)
        self.assertIn("CDN_LANE_UUID_MISSING", text)
        self.assertNotIn("xray uuid", text)

    def test_origin_tls_exposes_only_the_decoy_and_the_ws_path(self) -> None:
        text = self.text
        self.assertIn("listen $ORIGIN_PORT ssl default_server;", text)
        self.assertIn("ssl_reject_handshake on;", text)
        self.assertIn("location ^~ $WS_PATH {", text)
        self.assertIn("proxy_set_header Upgrade \\$http_upgrade;", text)
        self.assertIn('proxy_set_header Connection "upgrade";', text)
        self.assertIn("try_files \\$uri \\$uri/ =404;", text)
        self.assertIn("location / {", text)
        self.assertNotIn("proxy_set_header X-Real-IP", text)
        self.assertNotIn("proxy_set_header X-Forwarded-For", text)

    def test_wildcard_certificate_is_resolved_by_hostname(self) -> None:
        text = self.text
        # vasma stores the wildcard certificate under fq.sciman.top, so an
        # exact-name lookup would fail for cdn.sciman.top.
        self.assertIn("resolve_tls() {", text)
        self.assertIn('-checkhost "$PUBLIC_HOST"', text)
        self.assertIn("CDN_LANE_TLS_MATERIAL_MISSING", text)

    def test_proves_the_local_and_edge_paths(self) -> None:
        text = self.text
        self.assertIn("cdn_local_listening=", text)
        self.assertIn("origin_listening=", text)
        self.assertIn("cdn_edge_http=", text)
        self.assertIn("CDN_EDGE_NOT_SERVING_YET", text)
        self.assertIn("CDN_LANE_APPLIED", text)

    def test_template_only_change_skips_the_xray_restart(self) -> None:
        text = self.text
        self.assertIn("xray_changed=0", text)
        self.assertIn("xray_restart=skipped(unchanged inbound)", text)


if __name__ == "__main__":
    unittest.main()

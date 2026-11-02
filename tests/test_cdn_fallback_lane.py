"""Contract tests for scripts/cdn_fallback_lane.ps1.

The Cloudflare lane is the only entry that keeps working when the origin IP is
blocked, so its shape is pinned: proxied DNS, TLS terminated on the origin,
a loopback VLESS+WS hop, and the decoy page for every other path.
"""

from __future__ import annotations

import shutil
import subprocess
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
        # The no-token proxy proof compares the CDN record against this
        # host's own public IP; a failed getent must never abort the lane
        # under pipefail + set -e.
        self.assertIn(
            'origin_hint="$(curl -s -m 8 https://api.ipify.org 2>/dev/null || true)"',
            text,
        )
        self.assertIn(
            'cdn_hint="$(getent ahostsv4 "$PUBLIC_HOST" 2>/dev/null | awk \'NR==1{print $1}\' || true)"',
            text,
        )

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
        self.assertIn("error_page 404 =404 /404.html;", text)
        self.assertIn("add_header Strict-Transport-Security", text)
        self.assertIn("location / {", text)
        self.assertNotIn("proxy_set_header X-Real-IP", text)
        self.assertNotIn("proxy_set_header X-Forwarded-For", text)

    def test_wildcard_certificate_is_resolved_by_hostname(self) -> None:
        text = self.text
        # vasma stores the wildcard certificate under fq.sciman.top, so an
        # exact-name lookup would fail for cdn.sciman.top.
        self.assertIn("resolve_tls() {", text)
        self.assertIn('-checkhost "$PUBLIC_HOST"', text)
        self.assertIn("cert_covers_host()", text)
        # An unrelated certificate must never be selected as a last resort.
        self.assertNotIn(
            'for crt in "$TLS_DIR"/*.crt; do\n        [ -f "$crt" ] || continue\n        key="${crt%.crt}.key"\n        if [ -f "$key" ]; then',
            text,
        )
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

    def test_rendered_template_is_checked_on_the_healthy_path(self) -> None:
        text = self.text
        # The origin can return 200 and the edge can be proxied while the
        # managed headers and 404 fallback have gone stale.
        self.assertIn("cdn_conf_current()", text)
        self.assertIn("cdn_lane_template=stale", text)
        self.assertIn("[ \"$edge_code\" = '200' ] && decoys_ok &&", text)
        self.assertIn(
            'temporary_conf="$(mktemp "$NGINX_CONF_D/.cdn-lane.XXXXXX")"', text
        )
        self.assertIn('mv -f -- "$temporary_conf" "$TARGET_FILE"', text)

    def test_rollback_restores_or_deletes_the_lane_dns_record(self) -> None:
        text = self.text
        # The DNS upsert runs before the local writes, so a later failure must
        # not leave a proxied record pointing at this origin while the lane
        # itself rolled back (2026-11-02 review): the rollback carries a
        # restore/delete ledger with best-effort failure surfacing.
        self.assertIn("dns-restore", text)
        self.assertIn("CDN_DNS_NOT_ROLLED_BACK", text)
        self.assertIn("CDN_DNS_ROLLED_BACK", text)
        self.assertIn("dns_new_record_id=", text)
        # The ledger is armed at backup time, before rollback() even exists.
        rollback_start = text.index("rollback() {")
        self.assertLess(text.index('> "$backup_dir/dns-restore"'), rollback_start)
        # Inside rollback(), the DNS restore precedes the verdict line.
        verdict_idx = text.index("ROLLBACK_VERIFIED", rollback_start)
        restore_idx = text.index("CDN_DNS_NOT_ROLLED_BACK", rollback_start)
        self.assertLess(restore_idx, verdict_idx)
        # A created record is captured in the apply flow, after rollback is
        # armed, so the ledger learns the new id before any later step fails.
        self.assertGreater(text.index('dns_new_record_id="$(printf'), rollback_start)

    def test_payload_is_valid_bash(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")
        payload = self.text.split("$remoteCommand = @'\n", 1)[1].split("\n'@", 1)[0]
        payload = payload.replace("\r\n", "\n").replace("\r", "\n")
        completed = subprocess.run(
            [bash, "-n"],
            input=payload,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

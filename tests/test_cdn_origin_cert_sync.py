"""Contract tests for scripts/cdn_origin_cert_sync.ps1.

zz cannot issue the wildcard certificate itself (its acme account has no
Cloudflare DNS token), so its CDN origin serves a copy of bwg's pair. A renewal
on bwg used to leave that copy behind; this lane copies it safely and proves
the served leaf.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cdn_origin_cert_sync.ps1"


class CdnOriginCertSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_is_read_only_until_apply(self) -> None:
        text = self.text
        self.assertIn('[ValidateSet("zz")]', text)
        self.assertIn("[switch]$Apply", text)
        self.assertIn('APPLY="__APPLY__"', text)
        self.assertIn("if [ \"$APPLY\" != '1' ]; then", text)
        self.assertIn("flock -n 9", text)

    def test_copies_bwg_wildcard_to_the_zz_cdn_paths(self) -> None:
        text = self.text
        self.assertIn("/etc/v2ray-agent/tls/fq.sciman.top.crt", text)
        self.assertIn("/etc/v2ray-agent/tls/cdn.zz.sciman.top.crt", text)
        self.assertIn("zzcdn.sciman.top", text)

    def test_proves_the_pair_and_the_served_leaf(self) -> None:
        text = self.text
        # A copied file is not proof: the key must match the certificate, the
        # certificate must cover the CDN name, and nginx must actually serve it.
        self.assertIn("CDN_CERT_KEY_MISMATCH", text)
        self.assertIn('openssl pkey -in "$WORK/key.pem" -pubout', text)
        self.assertIn('-checkhost "$VERIFY_HOST"', text)
        self.assertIn("CDN_CERT_SERVED_MISMATCH", text)
        self.assertIn("openssl x509 -outform DER", text)
        self.assertIn("cdn_cert_served_sha=", text)

    def test_backs_up_and_rolls_back(self) -> None:
        text = self.text
        self.assertIn("cdn-cert-sync.", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("ROLLBACK_FAILED", text)
        self.assertIn("nginx -t", text)
        self.assertIn("nginx -s reload", text)

    def test_never_prints_key_material(self) -> None:
        text = self.text
        # Only SHA-256 fingerprints may reach the receipt.
        self.assertNotIn('echo "$KEY_B64"', text)
        self.assertNotIn('echo "$CERT_B64"', text)
        self.assertIn("cdn_cert_source_sha=", text)
        self.assertIn('chmod 600 "$WORK/cert.pem" "$WORK/key.pem"', text)


if __name__ == "__main__":
    unittest.main()

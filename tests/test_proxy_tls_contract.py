"""Contract tests for the shared proxy TLS cipher surface.

The REALITY fallback target pins an explicit TLS 1.2 cipher list, while the
lane vhosts this repo projects used to inherit the nginx/OpenSSL default. The
result was two different TLS parameter sets on ports of the same hostname, which
one ClientHello per IP:port is enough to group. These tests pin the contract and
the four projectors that must consume it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "scripts" / "lib" / "proxy_tls_contract.ps1"
LANES = (
    "cdn_fallback_lane.ps1",
    "xhttp_nginx_front.ps1",
    "zz_xhttp_nginx_front.ps1",
    "reality_alt_sni_lane.ps1",
)


class ProxyTlsContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_literal_is_the_single_source_of_truth(self) -> None:
        self.assertEqual(
            self.text.count("$ProxyTlsCipherSuite = "),
            1,
            "the literal must be declared once",
        )
        # Byte-identical to what vasma writes into subscribe.conf: the fallback
        # target and every lane port have to present the same TLS parameters.
        self.assertIn(
            "TLS13_AES_128_GCM_SHA256:TLS13_AES_256_GCM_SHA384:"
            "TLS13_CHACHA20_POLY1305_SHA256:"
            "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:"
            "ECDHE-ECDSA-CHACHA20-POLY1305",
            self.text,
        )

    def test_cbc_only_suites_are_not_reintroduced(self) -> None:
        # These are exactly the two suites the nginx default adds on top of the
        # fallback's list, i.e. the observable difference this contract removes.
        # Only the rendered literal matters; the comment above it names them on
        # purpose so the next reader knows what was dropped and why.
        match = re.search(r"\$ProxyTlsCipherSuite = '([^']+)'", self.text)
        self.assertIsNotNone(match)
        assert match is not None
        literal = match.group(1)
        self.assertNotIn("ECDHE-ECDSA-AES128-SHA256", literal)
        self.assertNotIn("ECDHE-ECDSA-AES256-SHA384", literal)
        self.assertNotIn("CBC", literal)

    def test_exposes_a_validating_accessor(self) -> None:
        self.assertIn("function Get-ProxyTlsCipherSuite", self.text)
        self.assertIn("-notmatch", self.text)
        self.assertIn("throw", self.text)


class LaneConsumesTlsContractTests(unittest.TestCase):
    def test_every_lane_dot_sources_and_renders_the_contract(self) -> None:
        for name in LANES:
            with self.subTest(lane=name):
                text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
                self.assertIn("lib\\proxy_tls_contract.ps1", text)
                self.assertIn("$tlsCiphers = Get-ProxyTlsCipherSuite", text)
                self.assertIn("ssl_ciphers __TLS_CIPHERS__;", text)
                self.assertIn('Replace("__TLS_CIPHERS__", $tlsCiphers)', text)
                # The bash heredoc must never carry the literal, or a lane that
                # forgets the replace would still render a stale list.
                self.assertNotIn("$ProxyTlsCipherSuite", text)

    def test_alt_sni_pins_both_vhosts(self) -> None:
        text = (ROOT / "scripts" / "reality_alt_sni_lane.ps1").read_text(
            encoding="utf-8"
        )
        # The fallback vhost and the subscription-port standby must agree, or
        # the recovery name keeps its own fingerprint. Each line appears twice:
        # once in the renderer and once in the idempotency check that refuses to
        # short-circuit a host whose vhost still lacks it.
        self.assertEqual(text.count("ssl_ciphers __TLS_CIPHERS__;"), 4)

    def test_drift_gate_audits_the_rendered_list(self) -> None:
        text = (ROOT / "scripts" / "proxy_hardening_drift.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("==tls-cipher-surface==", text)
        self.assertIn("tls_cipher_surface=drift", text)
        self.assertIn("tls_cipher_drift file=", text)
        self.assertIn("drift+=('nginx-stealth')", text)
        match = re.search(r"expected_ciphers='([^']+)'", text)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertNotIn("SHA256:ECDHE-ECDSA-AES128-SHA256", match.group(1))


if __name__ == "__main__":
    unittest.main()

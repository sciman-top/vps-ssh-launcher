"""Contract tests for the zz parity lanes.

zz used to run sing-box only: REALITY lived on sing-box port 22752 and the
host had no xray binary. These lanes bring zz to the bwg shape (xray REALITY on
443, XHTTP lanes, nginx-fronted XHTTP TLS) and republish vasma's subscription
so the published node follows the moved listener.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class ZzXhttpFrontTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (SCRIPTS / "zz_xhttp_nginx_front.ps1").read_text(encoding="utf-8")

    def test_is_read_only_until_apply(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn('APPLY="__APPLY__"', text)
        self.assertIn("flock -n 9", text)
        self.assertIn("ROLLBACK_VERIFIED", text)

    def test_published_port_stays_on_the_xray_wrapper(self) -> None:
        text = self.text
        # vasma republishes the subscription from inbound[1].port, so nginx must
        # not grab the public port.
        self.assertIn("ib['port']=published", text)
        self.assertIn("listen 127.0.0.1:$FRONT_PORT ssl;", text)
        self.assertIn("ssl_reject_handshake on;", text)
        self.assertIn("ZZ_XHTTP_FACTS_INCOMPLETE", text)
        self.assertIn("ZZ_XHTTP_TLS_HOST_MISMATCH", text)
        self.assertIn('-checkhost "$sni"', text)

    def test_does_not_forward_client_ip_headers(self) -> None:
        text = self.text
        self.assertNotIn("proxy_set_header X-Real-IP", text)
        self.assertNotIn("proxy_set_header X-Forwarded-For", text)


class ZzSubscriptionRepublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (SCRIPTS / "zz_subscription_republish.ps1").read_text(
            encoding="utf-8"
        )

    def test_only_touches_the_salt_named_file(self) -> None:
        text = self.text
        self.assertIn(
            'SUB_DIR="${VPS_ZZ_SUB_DIR:-/etc/v2ray-agent/subscribe_local/default}"',
            text,
        )
        self.assertIn("ZZ_SUB_AMBIGUOUS_SALT", text)
        self.assertIn('cp -a "$target" "$backup_dir/previous"', text)

    def test_publishes_the_xray_reality_port(self) -> None:
        text = self.text
        # The node has to follow the moved listener; a stale 22752 node is dead.
        self.assertIn("f.setdefault('front', {})[s.get('port')] = ib.get('port')", text)
        self.assertIn("dokodemo-door", text)
        self.assertIn("ZZ_SUB_INCOMPLETE", text)
        self.assertIn("zz_subscription=applied", text)


class ZzCurrentDocumentationTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.readme = (root / "README.md").read_text(encoding="utf-8")
        self.runbook = (
            root / "docs" / "runbooks" / "proxy-camouflage-lanes.md"
        ).read_text(encoding="utf-8")

    def test_public_reality_port_matches_the_projected_xray_lane(self) -> None:
        # A stale 22752 instruction caused a real public-name probe to miss the
        # live xray wrapper on 443 and report a false lane failure.
        self.assertIn("BWG 与 ZZ 当前都传 `443`", self.readme)
        self.assertNotIn("ZZ 当前传 `22752`", self.readme)
        self.assertIn("| zz | VLESS+REALITY+Vision（xray） | TCP 443 |", self.runbook)
        self.assertIn("ZZ 的 Reality 公开端口当前为 `443`", self.runbook)
        self.assertNotIn("ZZ 的 Reality 公开端口当前为 `22752`", self.runbook)


if __name__ == "__main__":
    unittest.main()

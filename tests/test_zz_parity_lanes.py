"""Contract tests for the zz parity lanes.

zz used to run sing-box only: REALITY lived on sing-box port 22752 and the
host had no xray binary. These lanes bring zz to the bwg shape (xray REALITY on
443, XHTTP lanes, nginx-fronted XHTTP TLS) and republish vasma's subscription
so the published node follows the moved listener.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
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
        # The superseded-retirement branch deletes a remote file and reloads
        # nginx; it must stay behind the -Apply gate and only report
        # "pending" on a dry-run (it once deleted the legacy conf in
        # read-only mode).
        self.assertIn("zz_xhttp_superseded_pending=maintained_front", text)
        self.assertIn(
            "if [ \"$APPLY\" != '1' ]; then\n"
            "            echo 'zz_xhttp_superseded_pending=maintained_front'",
            text,
        )

    def test_published_port_stays_on_the_xray_wrapper(self) -> None:
        text = self.text
        # vasma republishes the subscription from inbound[1].port, so nginx must
        # not grab the public port.
        self.assertIn("ib['port']=published", text)
        self.assertIn("listen 127.0.0.1:$FRONT_PORT ssl;", text)
        self.assertIn("ssl_reject_handshake on;", text)
        self.assertIn("ZZ_XHTTP_FACTS_INCOMPLETE", text)
        self.assertIn("ZZ_XHTTP_TLS_HOST_MISMATCH", text)
        self.assertIn("error_page 404 =404 /404.html;", text)
        self.assertIn('-checkhost "$sni"', text)
        self.assertIn("zz_xhttp_superseded=maintained_front", text)
        self.assertNotIn("requires_apply=1", text)

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

    def test_writes_the_client_visible_subscription_file(self) -> None:
        text = self.text
        # vasma serves /s/default/<md5(email+salt)>; the lane used to rewrite
        # only subscribe_local, so the published URL kept the dead 22752 node.
        self.assertIn(
            'SERVED_DIR="${VPS_ZZ_SUB_SERVED_DIR:-/etc/v2ray-agent/subscribe/default}"',
            text,
        )
        self.assertIn(
            'SALT_FILE="${VPS_ZZ_SUB_SALT_FILE:-/etc/v2ray-agent/subscribe_local/subscribeSalt}"',
            text,
        )
        self.assertIn('printf \'%s%s\\n\' "$email" "$salt" | md5sum', text)
        self.assertIn('printf \'%s\\n\' "$(base64 -w0 "$target")" > "$served"', text)
        self.assertIn('base64 -d < "$served"', text)
        self.assertIn("zz_sub_local=", text)
        self.assertIn("ZZ_SUB_VERIFY_FAILED", text)
        self.assertIn("zz_sub_http=", text)

    def test_publishes_the_xhttp_tls_lane(self) -> None:
        text = self.text
        # The live TLS inbound is plain HTTP on loopback after the nginx front,
        # so the node must be derived from the front conf; the previous render
        # skipped every non-REALITY inbound and dropped this lane entirely.
        self.assertIn("vps-launcher-zz-xhttp-front.conf", text)
        self.assertIn("f['xhttp_tls']", text)
        self.assertIn("security=tls&type=xhttp", text)
        self.assertIn("#zz-VLESS_XHTTP_TLS", text)
        self.assertIn("has_tls=", text)
        self.assertIn('[ "$has_tls" -ne 1 ]', text)

    def test_publishes_standard_hysteria2_uris(self) -> None:
        text = self.text
        # Keep the official sni field and v2rayN's current mport extension.
        # The old peer / insecure pair was Hysteria1 vocabulary.
        self.assertIn('HY2_HOP_RANGE = "20000-20999"', text)
        self.assertIn("hysteria2://%s@%s:%s?sni=%s&alpn=h3#zz-singbox_hysteria2", text)
        self.assertIn(
            "hysteria2://%s@%s:%s?sni=%s&alpn=h3&mport=%s&obfs=salamander", text
        )
        self.assertNotIn("peer=%s", text)

    def test_render_script_executes_with_standard_hysteria2_uris(self) -> None:
        match = re.search(
            r"render\(\) \{\r?\n"
            r"    printf '%s' \"\$facts\" \| python3 -c '\r?\n"
            r"(.*?)\r?\n'\r?\n\}",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        result = subprocess.run(
            [sys.executable, "-c", match.group(1)],
            input=json.dumps(
                {
                    "hy2_base": {
                        "password": "base-auth",
                        "sni": "example.com",
                        "port": 443,
                    },
                    "hy2_obfs": {
                        "password": "obfs-auth",
                        "sni": "example.com",
                        "port": 8444,
                        "obfs": "obfs-key",
                    },
                }
            ),
            check=True,
            capture_output=True,
            text=True,
        )
        links = result.stdout.splitlines()
        obfs = next(link for link in links if link.endswith("#zz-hy2-obfs"))
        authority = obfs.split("://", 1)[1].split("@", 1)[1].split("?", 1)[0]
        self.assertEqual(authority, "example.com:8444")
        self.assertIn("mport=20000-20999", obfs)
        self.assertTrue(
            any(
                link.startswith("hysteria2://")
                and "?sni=example.com&alpn=h3#" in link
                and "peer=" not in link
                for link in links
            )
        )


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

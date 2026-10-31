"""Contract tests for scripts/xhttp_nginx_front.ps1.

The XHTTP TLS lane must stop being a bare Xray listener: nginx terminates TLS
on the same port and proxies the configured path to the loopback inbound, so
unknown paths return the decoy site instead of an unmarked empty 404.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xhttp_nginx_front.ps1"


class XhttpNginxFrontTests(unittest.TestCase):
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

    def test_removes_wrapper_and_downgrades_inner_inbound(self) -> None:
        text = self.text
        # The wrapper stays (vasma publishes its port) but now points at nginx,
        # and the loopback inbound becomes plain HTTP because nginx ends TLS.
        self.assertIn("ib.get('protocol') == 'dokodemo-door'", text)
        self.assertIn("stream['security'] = 'none'", text)
        self.assertIn("stream.pop('tlsSettings', None)", text)
        self.assertIn("xhttp_front=not_applicable", text)
        self.assertIn("'vps-launcher-xhttp-front'", text)
        self.assertIn("ib['settings']['port'] = front", text)

    def test_published_port_stays_with_xray(self) -> None:
        text = self.text
        # vasma reads inbounds[1].port for the subscription and rewrites the
        # wrapper on a menu reconfiguration; giving the public port to nginx
        # would turn that into a bind conflict that takes xray down.
        self.assertIn("inbounds[1]", text)
        self.assertIn("listen 127.0.0.1:{port} ssl;", text)
        self.assertIn("listen 127.0.0.1:{port} ssl default_server;", text)
        self.assertIn("XHTTP_FRONT_PORT_COLLISION", text)

    def test_nginx_config_streams_xhttp(self) -> None:
        text = self.text
        # Buffering or a default 60s read timeout breaks packet-up/stream-up.
        self.assertIn("proxy_buffering off;", text)
        self.assertIn("proxy_request_buffering off;", text)
        self.assertIn("proxy_read_timeout 3600s;", text)
        self.assertIn("client_max_body_size 0;", text)
        self.assertIn('proxy_set_header Connection "";', text)
        self.assertIn("location ^~ {location}", text)
        self.assertIn("try_files $uri $uri/ =404;", text)

    def test_proves_the_lane_after_apply(self) -> None:
        text = self.text
        self.assertIn("FRONT_LOOPBACK_NOT_LISTENING", text)
        self.assertIn("PUBLISHED_PORT_NOT_LISTENING", text)
        self.assertIn("INNER_INBOUND_NOT_LISTENING", text)
        self.assertIn("DECOY_PAGE_NOT_SERVED", text)
        self.assertIn("front_loopback_listening", text)


if __name__ == "__main__":
    unittest.main()

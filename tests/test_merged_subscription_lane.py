"""Contract tests for scripts/merged_subscription_lane.ps1.

vasma publishes only the inbounds it owns, so the Hysteria2 obfs lane and the
Cloudflare lane never reach the subscription. This lane derives every node from
the live configs and publishes them as one base64 list, including the alternate
REALITY hostname when reality_alt_sni_lane.ps1 has added one.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "merged_subscription_lane.ps1"
)


class MergedSubscriptionLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_is_read_only_until_apply(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn('APPLY="__APPLY__"', text)
        self.assertIn("if [ \"$APPLY\" != '1' ]; then", text)
        self.assertIn("flock -n 9", text)
        self.assertIn("STATUS_FILE", text)

    def test_token_is_held_by_the_caller(self) -> None:
        text = self.text
        # The file name is a credential-bearing URL path, so the lane never
        # invents or prints it; the workstation keeps it in AppData.
        self.assertIn("[ValidatePattern('^$|^[0-9a-f]{16,64}$')]", text)
        self.assertIn("merged-sub-token.txt", text)
        self.assertIn("token_len=${#TOKEN}", text)
        self.assertNotIn('echo "MERGED_SUB_URL=', text)

    def test_generated_token_comes_from_a_csprng(self) -> None:
        text = self.text
        # The path is the only thing protecting every lane's credentials;
        # Get-Random is a non-cryptographic PRNG seeded from process state.
        self.assertIn("[Security.Cryptography.RandomNumberGenerator]::Fill", text)
        self.assertIn("[Convert]::ToHexString($tokenBytes)", text)
        self.assertNotIn("Get-Random", text)

    def test_derives_every_lane_from_live_configs(self) -> None:
        text = self.text
        for marker in (
            "__PREFIX__-reality-vision",
            "__PREFIX__-xhttp-reality",
            "__PREFIX__-xhttp-tls",
            "__PREFIX__-hy2-base",
            "__PREFIX__-hy2-obfs",
            "__PREFIX__-cdn-ws",
        ):
            with self.subTest(node=marker):
                self.assertIn(marker, text)
        self.assertIn("MERGED_SUB_INCOMPLETE", text)
        self.assertIn('if [ "$count" -lt 6 ]', text)
        # TLS-XHTTP stays on packet-up because the stream-one close path is
        # still affected by open upstream Go 1.27/H2 issues and was flaky on
        # zz; the REALITY-XHTTP node remains on auto.
        self.assertIn("mode=packet-up#__PREFIX__-xhttp-tls", text)
        self.assertIn("mode=auto#__PREFIX__-xhttp-reality", text)

    def test_prefix_placeholder_is_replaced_on_render(self) -> None:
        # a91597c renamed the node labels to #__PREFIX__-... but only
        # __NAME_PREFIX__ had a Replace counterpart, so both machines kept
        # publishing the literal placeholder in every node name. The render
        # chain must substitute the label placeholder too.
        text = self.text
        self.assertIn('Replace("__NAME_PREFIX__", $lane.Prefix)', text)
        self.assertIn('.Replace("__PREFIX__", $lane.Prefix)', text)

    def test_publishes_the_alternate_reality_hostname(self) -> None:
        text = self.text
        # A client that already holds the list has to be able to switch names
        # without fetching it over the name that just got blocked.
        self.assertIn("reality_alt = {", text)
        self.assertIn("reality_link(reality_alt, '-alt')", text)
        self.assertIn("'sni': names[1],", text)
        self.assertIn("__PREFIX__-reality-vision%s", text)

    def test_serves_from_the_existing_static_root(self) -> None:
        text = self.text
        # No nginx edit: the 33387 vhost and the Cloudflare vhost both fall
        # through to `location /` and serve this path from the shared root.
        self.assertIn("/usr/share/nginx/html", text)
        self.assertIn('MERGED_DIR="$ROOT/s/merged"', text)
        self.assertIn('chmod 644 "$TARGET"', text)
        self.assertIn("MERGED_SUB_VERIFY_FAILED", text)

    def test_reapply_is_idempotent_on_content(self) -> None:
        text = self.text
        self.assertIn("merged_sub_current=", text)
        self.assertIn("merged_sub=applied", text)
        self.assertIn("merged_sub=drift", text)
        # The file already holds base64 text; re-encoding it as the comparison
        # input made the idempotency check fail on every pass.
        self.assertIn('have="$(cat "$TARGET" 2>/dev/null || true)"', text)
        self.assertNotIn('have="$(base64 -w0 < "$TARGET"', text)

    def test_resolves_loopback_fronts_to_public_ports(self) -> None:
        text = self.text
        # The REALITY TCP lane is published through a dokodemo on 443 that
        # forwards to a loopback VLESS inbound; handing clients the loopback
        # port produced a node nothing outside the host can reach.
        self.assertIn("loopback_fronts[dokodemo.get('port')] = entry.get('port')", text)
        self.assertIn("'port': loopback_fronts.get(inner_port, inner_port),", text)
        self.assertIn("dead_port=", text)

    def test_fetch_uses_the_same_rendered_links_and_offers_a_standby(self) -> None:
        text = self.text
        # The old count accepted any auth-bearing prefix, so an imported Hy2
        # link dropping its password still looked healthy. Labels are now
        # ASCII-safe and the actual direct GET is exercised on every run.
        self.assertIn("__PREFIX__-hy2-base", text)
        self.assertIn("__PREFIX__-hy2-obfs", text)
        self.assertIn("merged_sub_fetch=healthy", text)
        self.assertIn("merged_sub_http=", text)
        self.assertIn("Standby =", text)
        self.assertIn("$($lane.Standby)/s/merged/$Token", text)

    def test_vision_flow_is_derived_from_the_live_inbound(self) -> None:
        text = self.text
        # Vision is a per-user VLESS field. Hardcoding it published Vision for
        # an inbound that had lost it, so the node authenticated but failed at
        # the first packet; the lane must advertise what the config carries.
        self.assertIn("'flow': clients[0].get('flow') or ''", text)
        self.assertNotIn("&type=tcp&flow=xtls-rprx-vision#", text)
        self.assertIn("flow = '&flow=%s' % quote(item['flow'])", text)

    def test_generated_vision_uri_matches_the_inbound_flow(self) -> None:
        match = re.search(
            r"build\(\) \{\r?\n"
            r'    python3 - "\$XRAY_CONF" "\$SB_CONF" "\$NGINX_CONF_D" <<\'PY\'\r?\n'
            r"(.*?)\r?\nPY\r?\n\}",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None

        def render(flow: str) -> str:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                xray = root / "xray"
                singbox = root / "sing-box"
                nginx = root / "nginx"
                for directory in (xray, singbox, nginx):
                    directory.mkdir()
                client: dict[str, str] = {"id": "11111111-2222-3333-4444-555555555555"}
                if flow:
                    client["flow"] = flow
                (xray / "reality.json").write_text(
                    json.dumps(
                        {
                            "inbounds": [
                                {
                                    "protocol": "vless",
                                    "port": 45987,
                                    "settings": {"clients": [client]},
                                    "streamSettings": {
                                        "network": "tcp",
                                        "security": "reality",
                                        "realitySettings": {
                                            "shortIds": ["6ba85179e30d4fc2"],
                                            "serverNames": ["example.com"],
                                            "publicKey": "pub",
                                        },
                                    },
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        match.group(1),
                        str(xray),
                        str(singbox),
                        str(nginx),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                facts = json.loads(result.stdout)
                return str(
                    next(
                        link
                        for link in facts["links"]
                        if link.endswith("__PREFIX__-reality-vision")
                    )
                )

        self.assertIn("flow=xtls-rprx-vision", render("xtls-rprx-vision"))
        self.assertNotIn("flow=", render(""))

    def test_obfs_node_advertises_the_managed_hop_range(self) -> None:
        text = self.text
        self.assertIn('HY2_HOP_RANGE = "20000-20999"', text)
        # v2rayN reads the port range from mport and rejects a comma in the
        # authority, so keep a single numeric base port plus the query field.
        self.assertIn("&mport=%s&obfs=salamander", text)
        self.assertIn("hy2_obfs['sni'], HY2_HOP_RANGE", text)

    def test_generated_obfs_uri_uses_v2rayn_port_hopping_query(self) -> None:
        match = re.search(
            r"build\(\) \{\r?\n"
            r'    python3 - "\$XRAY_CONF" "\$SB_CONF" "\$NGINX_CONF_D" <<\'PY\'\r?\n'
            r"(.*?)\r?\nPY\r?\n\}",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            xray = root / "xray"
            singbox = root / "sing-box"
            nginx = root / "nginx"
            for directory in (xray, singbox, nginx):
                directory.mkdir()
            (singbox / "hysteria2.json").write_text(
                json.dumps(
                    {
                        "inbounds": [
                            {
                                "type": "hysteria2",
                                "listen_port": 443,
                                "users": [{"password": "base-auth"}],
                                "tls": {"server_name": "example.com"},
                            },
                            {
                                "type": "hysteria2",
                                "listen_port": 8444,
                                "users": [{"password": "obfs-auth"}],
                                "obfs": {
                                    "type": "salamander",
                                    "password": "obfs-key",
                                },
                                "tls": {"server_name": "example.com"},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    match.group(1),
                    str(xray),
                    str(singbox),
                    str(nginx),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            facts = json.loads(result.stdout)
            obfs = next(
                link for link in facts["links"] if link.endswith("__PREFIX__-hy2-obfs")
            )
            authority = obfs.split("://", 1)[1].split("@", 1)[1].split("?", 1)[0]
            self.assertEqual(authority, "example.com:8444")
            self.assertIn("mport=20000-20999", obfs)

    def test_standby_entry_is_verified_before_it_is_published(self) -> None:
        # The failover runbook hands out the third URL exactly when the primary
        # name is blocked, so a standby whose certificate does not cover its own
        # name is worse than none: zz published :12378 and every strict client
        # failed with "no alternative certificate subject name matches" while
        # curl -k answered 200.
        text = self.text
        self.assertIn("CA_BUNDLE=", text)
        self.assertIn('--cacert "$CA_BUNDLE"', text)
        self.assertIn("merged_sub_standby_verify=", text)
        self.assertIn("merged_sub_standby_failed", text)
        self.assertIn('Replace("__STANDBY__", $lane.Standby)', text)
        # The probe must not weaken to -k: that is the exact false positive that
        # hid the broken entry for a whole release.
        self.assertNotIn('--cacert "$CA_BUNDLE" -k', text)

    def test_zz_standby_uses_the_subscription_port(self) -> None:
        # 12378 is the vhost that actually serves /s/merged/; 35622 carries the
        # alternate name too but does not serve vasma's subscription paths.
        text = self.text
        self.assertIn('Standby = "https://static-zz.sciman.top:12378"', text)
        self.assertIn('Standby = "https://static.sciman.top:33387"', text)


if __name__ == "__main__":
    unittest.main()

"""Contract tests for scripts/proxy_hardening_drift.ps1.

The drift gate answers one question: did a vasma reinstall (or menu
re-configuration) quietly undo a hardening lane while every service still
looks healthy? It must be read-only, name the regressed lanes, and exit
non-zero only when something drifted.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "proxy_hardening_drift.ps1"


class ProxyHardeningDriftTests(ScriptValidationMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_reports_single_greppable_verdict(self) -> None:
        text = self.text
        self.assertIn("HARDENING_DRIFT=NONE", text)
        self.assertIn('echo "HARDENING_DRIFT=$(IFS=,; echo "${drift[*]}")"', text)
        self.assertIn("exit 3", text)

    def test_names_each_hardening_lane(self) -> None:
        text = self.text
        for lane in (
            "camouflage",
            "reality-shortid",
            "reality-target",
            "nginx-stealth",
            "xhttp-front",
            "xray-port-collision",
            "hysteria2-obfs",
            "hysteria2-masquerade",
            "cdn-fallback",
            "merged-sub",
        ):
            with self.subTest(lane=lane):
                self.assertIn(f"drift+=('{lane}')", text)

    def test_merged_subscription_detects_a_stale_published_uuid(self) -> None:
        text = self.text
        # A vasma reinstall can rotate the UUID; the published list then hands
        # out credentials that no longer authenticate.
        self.assertIn("echo '==merged-sub=='", text)
        self.assertIn("merged-sub.token", text)
        self.assertIn("merged_state='stale'", text)
        self.assertIn('"$file_uuid" = "$live_uuid"', text)
        self.assertIn("merged_sub=not-applicable", text)

    def test_cloudflare_lane_is_bwg_only_and_checks_the_proxy(self) -> None:
        text = self.text
        # The fallback only survives an origin-IP block while the record stays
        # proxied, so a DNS-only revert has to be drift rather than a silent
        # downgrade to "same address as every other lane".
        self.assertIn(
            '$cdnHost = if ($Profile -eq "bwg") { "cdn.sciman.top" } else { "" }', text
        )
        self.assertIn("echo '==cdn-fallback=='", text)
        self.assertIn("cdn_dns_state='not-proxied'", text)
        self.assertIn("cdn=not-applicable", text)
        # A green origin with a dead edge (SSL-mode flip) must still drift.
        self.assertIn(
            "cdn_edge_code=\"$(curl -s -m 10 -o /dev/null -w '%{http_code}'", text
        )
        self.assertIn("cdn_edge=${cdn_edge_code:-000}", text)
        self.assertIn('"__CDN_HOST__"', text)

    def test_is_read_only(self) -> None:
        text = self.text
        # No writes, no restarts, no Apply gate: this lane only observes.
        self.assertNotIn("[switch]$Apply", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("sing-box merge", text)
        self.assertNotIn("nginx -t", text)
        self.assertNotIn("rm -f", text)

    def test_stock_nginx_page_counts_as_drift(self) -> None:
        # The exact fingerprint the camouflage lane removes; its return means
        # the page was reverted even if the site still answers 200.
        self.assertIn("Welcome to nginx!", self.text)

    def test_stock_nginx_artifacts_count_as_drift(self) -> None:
        # 50x.html answers 200 on every vhost serving the root and comes back
        # with an nginx (re)install; the gate must catch it independently of
        # the root index comparison.
        text = self.text
        self.assertIn("camouflage=stock-artifact", text)
        self.assertIn("50x.html", text)
        self.assertIn("index.nginx-debian.html", text)
        self.assertIn("drift+=(", text)

    def test_listener_probe_is_exact_suffix(self) -> None:
        # ":443" must not also match ":4433", and a shorter local address must
        # not match a longer probe: index() answers 0 for "not found" while the
        # suffix position of "*:443" against ":22752" also computes to 0, so an
        # unguarded comparison reported every >=5-digit port as up.
        needle = "length($4) >= length(p) && index($4, p) == length($4) - length(p) + 1"
        self.assertIn(needle, self.text)
        # Both tcp and udp branches must carry the guard.
        self.assertEqual(self.text.count(needle), 2)

    def test_invalid_json_is_drift_not_clean(self) -> None:
        # A malformed source fragment must not be swallowed as an empty scan.
        self.assertIn("invalid = True", self.text)
        self.assertIn("raise SystemExit(2 if invalid else 0)", self.text)

    def test_hysteria2_masquerade_is_drift_checked(self) -> None:
        text = self.text
        self.assertIn("==hysteria2-masquerade==", text)
        self.assertIn("[string]$MasqueradeUrl", text)
        self.assertIn("EXPECTED_MASQUERADE_URL", text)
        self.assertIn("base_count=1", text)
        self.assertIn("masquerade_set=", text)
        self.assertIn("log_level_pinned=", text)
        self.assertIn("97_vps_ssh_launcher_singbox_log.json", text)
        self.assertIn("ib.get('listen_port') == 443", text)

    def test_duplicate_xray_ports_are_drift(self) -> None:
        text = self.text
        self.assertIn("==xray-port-collision==", text)
        self.assertIn("xray_duplicate_ports=", text)
        self.assertIn("drift+=('xray-port-collision')", text)

    def test_reality_fallback_target_is_drift_checked(self) -> None:
        text = self.text
        # A vasma reinstall rewrites target/serverNames from whatever domain
        # the operator answers. Pointing REALITY at a third-party site would
        # serve that site's certificate under our SNI, so the gate must compare
        # both cores against this host's own camouflage host name.
        self.assertIn("==reality-target==", text)
        self.assertIn("EXPECTED_REALITY_HOST", text)
        self.assertIn("$realityHost = ([uri]$MasqueradeUrl).Host", text)
        self.assertIn("reality_target=ok", text)
        self.assertIn("reality_target=not-applicable", text)
        self.assertIn("drift+=('reality-target')", text)
        # `dest` remains a supported alias of `target` in Xray.
        self.assertIn("rs.get('target') or rs.get('dest')", text)

    def test_nginx_stealth_drift_is_behavioural(self) -> None:
        text = self.text
        # The file existing is not proof: the gate re-probes unknown SNI and
        # the Server header, because a wrong default_server ordering can still
        # pass `nginx -t` while taking the camouflage page offline.
        self.assertIn("==nginx-stealth==", text)
        self.assertIn("unknown_sni_answered", text)
        self.assertIn("server_version_leak", text)
        self.assertIn("vps-launcher-stealth.conf", text)
        self.assertIn("drift+=('nginx-stealth')", text)
        # The true SNI must still answer 200: a reject vhost that swallowed
        # the camouflage vhost kills the REALITY fallback while every config
        # check stays green.
        self.assertIn("true_sni_ok", text)
        self.assertIn("true_sni_broken", text)

    def test_xhttp_front_drift_is_detected(self) -> None:
        text = self.text
        # A vasma menu reconfiguration restores the dokodemo wrapper and turns
        # the public port back into a bare Xray listener.
        self.assertIn("==xhttp-front==", text)
        self.assertIn("xhttp_front=bare", text)
        self.assertIn("front_loopback_not_listening", text)
        self.assertIn("published_port_not_listening", text)
        self.assertIn("drift+=('xhttp-front')", text)
        # The nginx location prefix must track the xray path: a renamed path
        # leaves the front proxying to a location nginx no longer routes.
        self.assertIn("xhttp_front=path-mismatch", text)
        self.assertIn(r"location \^~ (\S+)", text)

    def test_obfs_fragment_contract_is_drift_checked(self) -> None:
        # Fragment existence plus a live listener is not the contract: the
        # fragment must still be a salamander-obfuscated TLS hysteria2
        # inbound on the obfs port, or the backup UDP lane is silently gone.
        text = self.text
        self.assertIn("obfs_contract=", text)
        self.assertIn("== 'salamander'", text)
        self.assertIn("obfs_contract=invalid", text)

    def test_embedded_payload_is_bash_syntax(self) -> None:
        text = self.text.split("$remoteCommand = @'\n", 1)[1].split("\n'@", 1)[0]
        payload = text.replace("\r\n", "\n").replace("\r", "\n")
        payload = (
            payload.replace("__OBFS_PORT__", "8444")
            .replace("__EXPECTED_MASQUERADE_URL__", "https://example.com/")
            .replace("__EXPECTED_REALITY_HOST__", "example.com")
        )
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        result = subprocess.run(
            self._bash_command(bash, "-n"),
            input=payload,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

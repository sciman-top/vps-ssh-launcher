"""Contract tests for scripts/proxy_lane_acceptance.ps1.

The acceptance probe must prove a real client handshake, never treat
"service active" as acceptance, and must classify lanes that do not apply to a
host architecture as N/A rather than FAIL.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "proxy_lane_acceptance.ps1"


def _dict_field(node: ast.expr, name: str) -> ast.expr | None:
    if not isinstance(node, ast.Dict):
        return None
    for key, value in zip(node.keys, node.values, strict=False):
        if isinstance(key, ast.Constant) and key.value == name:
            return value
    return None


def _dict_keys(node: ast.expr) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


class ProxyLaneAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_reports_greppable_verdict(self) -> None:
        text = self.text
        self.assertIn("ACCEPTANCE_RESULT=PASS", text)
        self.assertIn("ACCEPTANCE_RESULT=FAIL", text)
        self.assertIn("RESULT=PASS", text)
        self.assertIn("RESULT=N/A", text)
        self.assertIn("ACCEPTANCE_RESULT=PARTIAL", text)

    def test_proves_handshake_not_service_state(self) -> None:
        text = self.text
        # Every verdict is decided by a real 204 through the tunnel, not by
        # systemctl / config hash.
        self.assertIn("generate_204", text)
        self.assertIn("socks5-hostname", text)
        self.assertNotIn("systemctl is-active", text)

    def test_is_read_only_and_cleans_up(self) -> None:
        text = self.text
        self.assertIn("mktemp -d", text)
        self.assertIn('rm -rf -- "$WORK"', text)
        self.assertIn("next_local_port()", text)
        # A dropped channel Hangups the payload before EXIT fires; the 14
        # remote probe clients must die on HUP like on INT/TERM (2026-11-03
        # orphan evidence: an untrapped HUP skips every cleanup).
        self.assertIn("trap cleanup EXIT INT TERM HUP", text)
        self.assertIn("trap - EXIT INT TERM HUP", text)
        # 15 spawn sites, all registered for the central cleanup (12 direct
        # `$!` captures plus 3 named pids for the negative-control probes).
        self.assertEqual(text.count("PIDS+=("), 15)
        self.assertIn('HOP_PORT="__HOP_PORT__"', text)
        self.assertIn("hysteria2-obfs-hop-public", text)
        # The public no-obfs classification must stay derivable from
        # assigned variables: a reference to the never-assigned
        # $public_noobfs_failure died under set -u before any verdict line
        # could be recorded.
        self.assertNotIn("$public_noobfs_failure", text)
        # A timeout below is not a valid port failure: UDP has no connected
        # TCP-style refusal. The lane must compare with an unobfuscated
        # request and a deliberately wrong obfs password.
        self.assertIn("probe_last_exit=0", text)
        self.assertIn("record_nonfatal_failure()", text)
        self.assertIn("noobfs_204=obfs_ineffective", text)
        self.assertIn("wrong_password_204=auth_bypass", text)
        self.assertIn("udp-unfiltered-QUIC-inconclusive", text)
        self.assertIn('elif [ "$exit_code" -eq 7 ]; then', text)
        self.assertIn("ho-public-noobfs.json", text)
        self.assertNotIn("port:19101", text)
        self.assertNotIn('listen_port":19102', text)
        # No secrets are interpolated into the raw one-quoted payload. An
        # optional operator salt is validated as bash data before transport.
        self.assertNotIn('OBFS_PASSWORD="__OBFS_PASSWORD__"', text)
        self.assertIn("OBFS_PASSWORD_FILE", text)
        self.assertIn("[string]$ObfsPasswordFile", text)
        self.assertIn(
            "-ObfsPassword and -ObfsPasswordFile are mutually exclusive.", text
        )
        self.assertIn("[A-Za-z0-9._-]{1,64}", text)
        # No remote config mutation: no merge/restart/apply in this lane.
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("sing-box merge", text)
        self.assertNotIn("nginx -t", text)

    def test_na_does_not_fail_the_run(self) -> None:
        # Two N/A tiers: `none` is a genuinely absent optional lane and stays
        # free (an Xray-only host must not be permanently PARTIAL); `na`
        # marks a lane that applies to this host but could not be probed, and
        # downgrades an otherwise clean run to PARTIAL so the gap is visible.
        text = self.text
        record = text.split("record() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("none) echo", record)
        self.assertIn("na)  echo", record)
        self.assertIn("fail=1", record)
        self.assertIn("na=1", record)
        # Applicable-but-unprobed lanes must use the penalty tier so a
        # half-missing lane cannot read as a clean architecture difference.
        self.assertIn('record reality-fallback na "no decoy root index"', text)
        self.assertIn('record reality na "no usable reality source"', text)
        self.assertIn('record hysteria2-base na "no base hysteria2 inbound"', text)
        # Genuinely absent optional lanes stay on the free tier.
        self.assertIn('record xhttp none "no publicly reachable xhttp inbound"', text)
        self.assertIn('record hysteria2-base none "sing-box lane not present"', text)
        self.assertIn('record hysteria2-obfs none "obfs lane not configured"', text)

    def test_source_of_truth_and_honest_partial_verdict(self) -> None:
        text = self.text
        # A generated subscription can be absent or stale. It is not proof of
        # the active sing-box inbound or public key, so the public key is
        # derived locally from the private key in the live source fragment.
        self.assertNotIn("subscribe', 'sing-box'", text)
        self.assertIn("derive_reality_public_key()", text)
        self.assertIn("RFC 7748", text)
        self.assertIn('if [ "$na" -eq 0 ]; then', text)
        self.assertIn("expected_obfs != want_obfs", text)
        self.assertIn('record hysteria2-base none "sing-box lane not present"', text)
        self.assertIn('record hysteria2-obfs none "sing-box lane not present"', text)
        self.assertIn("finally {", text)
        self.assertIn("rm -f -- '$remotePasswordFile'", text)

    def test_present_but_unusable_singbox_is_failure_not_na(self) -> None:
        text = self.text
        # N/A is reserved for a genuinely absent optional lane.  A binary or
        # source tree that is present but unusable must surface as FAIL so a
        # broken sing-box installation cannot masquerade as an Xray-only host.
        self.assertIn('elif [ ! -x "$SB_BINARY" ]; then', text)
        self.assertIn('record hysteria2-base 1 "binary_unusable"', text)
        self.assertIn('elif [ ! -d "$SB_SOURCE_DIR" ]; then', text)
        self.assertIn('record hysteria2-base 1 "source_unreadable"', text)
        self.assertNotIn('if [ "$hy2_facts_ok" -ne 1 ]; then', text)
        # A malformed source fragment must not be downgraded to an omitted
        # lane; all three fact extractors return a distinct non-zero code and
        # the caller records a FAIL.
        self.assertIn("invalid = True", text)
        self.assertIn("raise SystemExit(4)", text)
        self.assertIn("if found is None:", text)
        self.assertIn('record hysteria2-base 1 "source_invalid"', text)
        self.assertIn('record reality-singbox 1 "source_invalid"', text)

    def test_present_but_unusable_xray_is_failure_not_na(self) -> None:
        text = self.text
        # A leftover conf directory on a sing-box-only host is still N/A; a
        # present-but-unusable binary or a binary with missing source is FAIL.
        self.assertIn(
            'if [ ! -e "$XRAY_BINARY" ] && [ ! -L "$XRAY_BINARY" ]; then', text
        )
        self.assertIn("dormant conf directory", text)
        self.assertIn('elif [ ! -x "$XRAY_BINARY" ]; then', text)
        self.assertIn('elif [ ! -d "$XRAY_CONF" ]; then', text)
        self.assertIn('record reality-xray 1 "binary_or_source_unusable"', text)
        self.assertIn('if [ ! -e "$SB_BINARY" ] && [ ! -L "$SB_BINARY" ]; then', text)

    def test_public_name_adds_external_path_probes(self) -> None:
        text = self.text
        self.assertIn("[string]$PublicName", text)
        self.assertIn('[Alias("XrayPublicPort")]', text)
        self.assertIn("[int]$RealityPublicPort", text)
        self.assertIn("-RealityPublicPort is required with -PublicName", text)
        self.assertIn("PUBLIC_NAME=", text)
        self.assertIn("reality-xray-public", text)
        self.assertIn("reality-singbox-public", text)
        self.assertIn("hysteria2-base-public", text)
        self.assertIn("hysteria2-obfs-public", text)
        # Hysteria2 probes must validate the deployed certificate and SNI.
        # insecure=true hid expired, mismatched, or incomplete chains while
        # the SOCKS tunnel itself still returned 204.
        self.assertNotIn('"insecure": True', text)
        self.assertIn('"server_name": sni, "alpn": ["h3"]', text)
        # A single post-reboot REALITY miss must receive one bounded retry.
        # Repeated misses still return the final non-204 code and fail.
        self.assertIn("for attempt in 1 2; do", text)
        self.assertIn('if [ "$attempt" -eq 1 ]; then', text)
        self.assertIn("sleep 2", text)
        # PublicName is interpolated into the bash payload, so host syntax must
        # be rejected before transport.
        self.assertIn("[A-Za-z0-9._:\\[\\]-]+", text)

    def test_detail_probe_preserves_curl_exit_outside_command_substitution(
        self,
    ) -> None:
        text = self.text
        # curl runs in command substitution so its stdout can become the HTTP
        # code, but probe_last_exit must be assigned in the function's parent
        # shell. Assigning it inside $(...) made every failure curl-exit-0.
        self.assertIn('probe_last_code=""', text)
        self.assertIn('probe_last_code="$(curl', text)
        self.assertIn("probe_via_socks_once()", text)
        self.assertIn("if [ \"$probe_last_code\" = '204' ]; then", text)
        self.assertNotIn("[ \"$probe_last_code\" = '204' ] && break", text)
        self.assertNotIn('[ "$attempt" -eq 1 ] && sleep 2', text)
        self.assertNotIn('code="$(probe_via_socks_detail', text)
        self.assertNotIn('noobfs_code="$(probe_via_socks_detail', text)
        self.assertNotIn('wrong_code="$(probe_via_socks_detail', text)
        self.assertNotIn('public_noobfs_code="$(probe_via_socks_detail', text)

    def test_xhttp_fallback_transports_are_probed(self) -> None:
        text = self.text
        # Two of the three subscription nodes are XHTTP. They must be proven
        # with a real handshake, and the TLS lane has to be dialled through its
        # dokodemo-door wrapper rather than the loopback inbound.
        self.assertIn("xhttp_facts()", text)
        self.assertIn("dokodemo-door", text)
        self.assertIn('record "xhttp-$xkind" 0', text)
        self.assertIn('record "xhttp-$xkind" 1', text)
        self.assertIn('"mode": "packet-up" if kind == "tls" else "auto"', text)
        self.assertIn('stream["tlsSettings"] = {', text)
        # A loaded host can exceed a fixed three-second client boot on one run
        # while the same lane passes immediately afterwards. Wait on the local
        # SOCKS listener and, if the probe still fails, retain curl's failure
        # class instead of collapsing every transport problem to code=000.
        self.assertIn("wait_for_local_socks_listener()", text)
        self.assertIn("client_not_listening", text)
        self.assertIn('probe_via_socks_detail "$x_socks"', text)
        self.assertIn('code="$probe_last_code"', text)
        self.assertIn("failure=$failure", text)
        self.assertIn('stop_background_process "$x_client_pid"', text)

    def test_xray_lanes_wait_for_readiness_and_classify_failure(self) -> None:
        text = self.text
        self.assertIn("client_log_class()", text)
        self.assertIn("bind-conflict", text)
        self.assertIn("reality-rejected", text)
        self.assertIn("peer-close", text)
        self.assertIn("stop_background_process()", text)
        # All four direct Xray lanes must wait on the SOCKS listener instead of
        # relying on a fixed sleep. Failure output keeps a sanitized class,
        # never the raw client log or credential-bearing configuration.
        for variable in (
            "x_socks_port",
            "alt_socks_port",
            "public_socks_port",
            "c_socks",
        ):
            self.assertIn(f'wait_for_local_socks_listener "${variable}"', text)
        self.assertIn("client_log=$(client_log_class", text)
        self.assertNotIn('cat "$WORK/x.log"', text)

    def test_xhttp_tls_front_is_found_on_both_hostname_shapes(self) -> None:
        text = self.text
        # zz names its managed front vps-launcher-zz-xhttp-front.conf; looking
        # only for bwg's file skipped the 10094 lane while still reporting PASS.
        self.assertIn(
            "for name in ('vps-launcher-xhttp-front.conf', "
            "'vps-launcher-zz-xhttp-front.conf'):",
            text,
        )

    def test_cloudflare_ws_lane_is_probed_end_to_end(self) -> None:
        text = self.text
        # The CDN lane is the last one standing after an origin-IP block; a
        # decoy 200 only proves the origin, so the edge needs a real handshake.
        self.assertIn("cdn_facts()", text)
        self.assertIn("vps-launcher-cdn.conf", text)
        self.assertIn("vps-launcher-cdn-ws", text)
        self.assertIn("record cdn-ws 0", text)
        self.assertIn("record cdn-ws 1", text)
        self.assertIn('"network": "ws"', text)
        self.assertIn('"alpn": ["http/1.1"]', text)

    def test_reality_fallback_serves_the_decoy_page(self) -> None:
        text = self.text
        # What an active prober sees on the REALITY port must still be the
        # decoy site, not a foreign certificate or an empty error page.
        self.assertIn("reality-fallback", text)
        self.assertIn("NGINX_ROOT", text)
        self.assertIn("decoy_match=no", text)

    def test_probe_uses_a_neutral_endpoint_with_a_bound_fallback(self) -> None:
        # 2026-11-09: www.gstatic.com goes through this fleet google_ipv4_out
        # (the Google IPv4 pin) and lost ~1 attempt in 20 from the host while
        # cp.cloudflare.com was 20/20 through the same tunnel in the same
        # window. A lane verdict must not inherit the destination flakiness.
        self.assertIn("cp.cloudflare.com/generate_204", self.text)
        self.assertIn("VPS_ACCEPT_PROBE_URL", self.text)
        self.assertIn("PROBE_FALLBACK_URL", self.text)
        source = self.text.split("probe_via_socks_once() {", 1)[1].split(
            chr(10) + "}", 1
        )[0]
        # Exactly one retry, at a DIFFERENT destination, so a persistent lane
        # failure can never be retried into a PASS.
        self.assertEqual(source.count("curl -sS"), 2)
        self.assertIn(chr(36) + "PROBE_URL", source)
        self.assertIn(chr(36) + "PROBE_FALLBACK_URL", source)

    def _probe_renderers(self) -> list[str]:
        return [
            body
            for body in re.findall(r"<<'PY'\n(.*?)\nPY\n", self.text, re.S)
            if "data = {" in body
        ]

    def test_probe_outbounds_are_pinned_to_ipv4(self) -> None:
        # zz has no global IPv6 address and no v6 default route, yet getaddrinfo
        # still returns AAAA records for the public endpoints. A dialer with the
        # default strategy can therefore pick an unreachable v6 destination:
        # measured 2026-11-09 as curl-exit-35 on a rotating subset of lanes
        # while "curl -4" was 10/10 on the same host. The probe is not an IPv6
        # reachability test, so every outbound that dials a hostname pins IPv4
        # - at its real schema position. Three of the four original Xray probes
        # carried `"sockopt"` as a sibling of "streamSettings", a key Xray
        # silently drops, so the documented pin did not exist; this test parses
        # the rendered configs so a misplaced pin can never pass again.
        xray_probes = 0
        singbox_host_probes = 0
        for body in self._probe_renderers():
            tree = ast.parse(body)
            for node in tree.body:
                if not (
                    isinstance(node, ast.Assign)
                    and any(getattr(t, "id", "") == "data" for t in node.targets)
                ):
                    continue
                outbounds = _dict_field(node.value, "outbounds")
                assert isinstance(outbounds, ast.List)
                for outbound in outbounds.elts:
                    keys = _dict_keys(outbound)
                    stream = _dict_field(outbound, "streamSettings")
                    if "streamSettings" in keys:
                        xray_probes += 1
                        # sockopt belongs inside streamSettings (the transport
                        # dialer), never as an outbound sibling key.
                        self.assertNotIn("sockopt", keys, body)
                        if isinstance(stream, ast.Dict):
                            sock = _dict_field(stream, "sockopt")
                            assert isinstance(sock, ast.Dict), body
                            self.assertEqual(_dict_keys(sock), {"domainStrategy"})
                            value = _dict_field(sock, "domainStrategy")
                            assert isinstance(value, ast.Constant), body
                            self.assertEqual(value.value, "UseIPv4")
                        else:
                            # The xhttp renderer builds streamSettings in a
                            # variable; the pin must be assigned onto it.
                            self.assertIn(
                                'stream["sockopt"] = {"domainStrategy": "UseIPv4"}',
                                body,
                            )
                    server = _dict_field(outbound, "server")
                    if server is None:
                        continue
                    if isinstance(server, ast.Constant) and (
                        server.value == "127.0.0.1"
                    ):
                        continue
                    singbox_host_probes += 1
                    # sing-box 1.14 removed the legacy outbound-level
                    # domain_strategy option (the client FATALs at startup
                    # under it); the ipv4_only pin lives on the route-level
                    # default_domain_resolver instead.
                    self.assertNotIn("domain_strategy", _dict_keys(outbound), body)
                    route = _dict_field(node.value, "route")
                    assert isinstance(route, ast.Dict), body
                    resolver = _dict_field(route, "default_domain_resolver")
                    assert isinstance(resolver, ast.Dict), body
                    resolver_server = _dict_field(resolver, "server")
                    assert isinstance(resolver_server, ast.Constant), body
                    self.assertEqual(resolver_server.value, "local-resolver")
                    strategy = _dict_field(resolver, "strategy")
                    assert isinstance(strategy, ast.Constant), body
                    self.assertEqual(strategy.value, "ipv4_only")
        # Five Xray probes (REALITY x2 + alt, public, CDN WS, xhttp) and five
        # public-name sing-box probes (REALITY, hy2 base/obfs/no-obfs/hop).
        self.assertEqual(xray_probes, 5)
        self.assertEqual(singbox_host_probes, 5)

    def test_probe_timeout_covers_the_cold_start(self) -> None:
        # 2026-11-09: zz failed a different 3-of-12 subset on two consecutive
        # passes with curl-exit-35 while the server answered 30/30 handshakes
        # and 3/3 manual probes at the harness's exact timing. The identical
        # command passed 12/12 with -TimeoutSeconds 30, so the old 12s default
        # encoded a fast-machine assumption into a fleet gate.
        match = re.search(r"\[int\]\$TimeoutSeconds\s*=\s*(\d+)", self.text)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertGreaterEqual(int(match.group(1)), 30)
        # The knob must stay available for a deliberately strict run.
        self.assertIn("-TimeoutSeconds must be between", self.text)


if __name__ == "__main__":
    unittest.main()

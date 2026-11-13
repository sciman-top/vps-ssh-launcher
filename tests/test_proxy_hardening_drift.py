"""Contract tests for scripts/proxy_hardening_drift.ps1.

The drift gate answers one question: did a vasma reinstall (or menu
re-configuration) quietly undo a hardening lane while every service still
looks healthy? It must be read-only, name the regressed lanes, and exit
non-zero only when something drifted.
"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import tempfile
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
        # The verdict is a set of lanes. Several sub-checks can observe the
        # same lane (google-ipv4 x2, v2ray-updater x4), and passing the raw
        # array through emitted duplicates to the recovery dispatcher.
        self.assertIn(
            'echo "HARDENING_DRIFT=$(IFS=,; echo "${unique_drift[*]}")"', text
        )
        self.assertIn("unique_drift=()", text)
        self.assertIn('*" $lane "*) continue ;;', text)
        self.assertIn("exit 3", text)

    def test_import_scratch_file_is_namespaced(self) -> None:
        # The read-only gate truncates (never deletes) its subscription
        # scratch file, so every run leaves a 0-byte file behind by design;
        # the namespaced mktemp template keeps those leftovers attributable
        # on the hosts, and the age-guarded sweep below bounds them to our
        # own namespace without ever touching concurrent runs or host state.
        self.assertIn(
            'import_file="$(mktemp "${TMPDIR:-/tmp}/vps-launcher-drift.XXXXXXXXXX")"',
            self.text,
        )
        self.assertIn("-name 'vps-launcher-drift.*' -mmin +1440 -delete", self.text)

    def test_firewall_baseline_checks_persistent_drop_policy(self) -> None:
        text = self.text
        self.assertIn("==firewall-baseline==", text)
        self.assertIn("/etc/vps-ssh-launcher/firewall.nft", text)
        self.assertIn("__FIREWALL_RULES_SHA__", text)
        self.assertIn("systemctl is-active --quiet", text)
        self.assertIn("firewall_input_state", text)
        self.assertIn("__FIREWALL_TCP__", text)
        self.assertIn("__FIREWALL_UDP__", text)
        self.assertIn("live-rules", text)

    def test_merged_subscription_detects_a_stale_published_uuid(self) -> None:
        text = self.text
        # A vasma reinstall can rotate the UUID; the published list then hands
        # out credentials that no longer authenticate.
        self.assertIn("echo '==merged-sub=='", text)
        self.assertIn("merged-sub.token", text)
        self.assertIn("merged_state='stale'", text)
        self.assertIn('"$file_uuid" = "$live_uuid"', text)
        self.assertIn("merged_sub=not-applicable", text)
        # The published Vision node must not claim a flow the live inbound no
        # longer carries, in either direction: a stale list that keeps the
        # claim and a re-rendered list that should have dropped it.
        self.assertIn("flow_published", text)
        self.assertIn("flow_expected", text)
        self.assertIn('"$flow_published" = "$flow_expected"', text)

    def test_merged_sub_flow_probe_survives_a_flow_less_node(self) -> None:
        text = self.text
        # The published-node probe ends in `grep -o`, which exits 1 when the
        # node carries no flow - the steady state once the lane is converged.
        # Under `set -Eeuo pipefail` that non-zero aborted the whole gate at
        # `==merged-sub==` instead of reporting "absent" (caught by running the
        # payload against a simulated root, not by reading it).
        source = text.split("flow_published='absent'", 1)[1].split(
            "flow_expected='absent'", 1
        )[0]
        self.assertIn("{ grep -o 'flow=[^&]*' || true; }", source)

    def test_flow_probe_reports_absent_and_present(self) -> None:
        text = self.text
        match = re.search(
            r"flow_published=\"\$\(base64 -d < \"\$merged_file\".*?\)\"",
            text,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        body = match.group(0).split("$(", 1)[1].rsplit(")", 1)[0]
        body = body.replace('base64 -d < "$merged_file" 2>/dev/null', 'cat "$1"')
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "node"
            fixture.write_text(
                "vless://u@h:443?encryption=none&security=reality&sni=a"
                "&type=tcp&flow=xtls-rprx-vision#x\n",
                encoding="utf-8",
            )
            present = subprocess.run(
                self._bash_command(
                    bash,
                    "-o",
                    "pipefail",
                    "-c",
                    body,
                    "probe",
                    self._bash_path(bash, fixture),
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(present.returncode, 0, present.stderr)
            self.assertEqual(present.stdout.strip(), "xtls-rprx-vision")

            fixture.write_text(
                "vless://u@h:443?encryption=none&security=reality&sni=a&type=tcp#x\n",
                encoding="utf-8",
            )
            absent = subprocess.run(
                self._bash_command(
                    bash,
                    "-o",
                    "pipefail",
                    "-c",
                    body,
                    "probe",
                    self._bash_path(bash, fixture),
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(absent.returncode, 0, absent.stderr)
            self.assertEqual(absent.stdout.strip(), "")

    def test_merged_subscription_detects_an_unfetchable_or_corrupt_import(self) -> None:
        text = self.text
        # Node count plus the first VLESS UUID does not prove the Hy2 auth
        # field survived URI import or that the URL is reachable at the
        # direct host. Both are required for real clients to recover.
        self.assertIn("==merged-sub-fetch==", text)
        self.assertIn("merged_sub_fetch=drift", text)
        self.assertIn("obfs-credential", text)
        self.assertIn('--resolve "$EXPECTED_REALITY_HOST', text)
        # v2rayN reads Hysteria2 port hopping from mport and rejects a comma in
        # the authority; the drift gate must enforce the importable shape.
        self.assertIn("obfs-hop", text)
        self.assertIn("single_port", text)
        self.assertIn("hop_query", text)
        self.assertIn("mport=20000-20999", text)

    def test_audits_the_alternate_name_standby_entry(self) -> None:
        # The third published URL is the failover entry used when the primary
        # SNI is blocked. zz served it on a port whose certificate did not cover
        # the name, so the recovery path failed hostname verification while the
        # direct entry stayed green; only an explicit check catches that.
        text = self.text
        self.assertIn('MERGED_STANDBY="__MERGED_STANDBY__"', text)
        self.assertIn('Replace("__MERGED_STANDBY__", $mergedStandby)', text)
        self.assertIn("merged_sub_standby=$standby_state", text)
        self.assertIn('--cacert "$CA_BUNDLE"', text)
        # A standby miss is owned by the lane that projects the alternate name,
        # so the gate reports it there instead of inventing a new drift family.
        self.assertIn(
            "standby_state='drift'\n            drift+=('reality-alt-sni')", text
        )

    def test_alt_standby_vhost_must_serve_the_alternate_name(self) -> None:
        text = self.text
        self.assertIn("vps-launcher-alt-sni-sub.conf", text)
        self.assertIn("alt_standby_vhost=drift", text)
        self.assertIn('-checkhost "$alt_name"', text)
        # bwg's fallback port is its subscription port, so there is no separate
        # standby server to expect there; only a port where the lane had to
        # project one may report "missing".
        self.assertIn(
            'echo "alt_standby_vhost=not-needed port=$standby_port same-as-fallback"',
            text,
        )

    def test_import_gate_accepts_v2rayn_and_rejects_authority_multiport(self) -> None:
        source = self.text.split("<<'PY_IMPORT'\n", 1)[1].split("\nPY_IMPORT", 1)[0]
        tls = (
            "vless://secret@example.com:10094?encryption=none&security=tls"
            "&type=xhttp&sni=example.com&host=example.com&mode=packet-up#xhttp-tls"
        )
        old = (
            "hysteria2://secret@example.com:8444?sni=example.com&alpn=h3"
            "&mport=20000-20999&obfs=salamander&obfs-password=obfs#example-hy2-obfs"
        )
        current = (
            "hysteria2://secret@example.com:8444,20000-20999?sni=example.com"
            "&alpn=h3&obfs=salamander&obfs-password=obfs#example-hy2-obfs"
        )

        def classify(link: str) -> str:
            with tempfile.TemporaryDirectory() as temp:
                payload = Path(temp) / "subscription"
                payload.write_text(
                    base64.b64encode((tls + "\n" + link).encode()).decode(),
                    encoding="ascii",
                )
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        source,
                        "2",
                        str(payload),
                        str(Path(temp) / "missing"),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return result.stdout.strip()

        self.assertEqual(classify(old), "ok")
        self.assertEqual(classify(current), "obfs-hop")

    def test_import_gate_requires_tls_xhttp_packet_up(self) -> None:
        source = self.text.split("<<'PY_IMPORT'\n", 1)[1].split("\nPY_IMPORT", 1)[0]
        packet_up = (
            "vless://secret@example.com:10094?encryption=none&security=tls"
            "&type=xhttp&sni=example.com&host=example.com&mode=packet-up#xhttp-tls"
        )
        stream_one = packet_up.replace("mode=packet-up", "mode=stream-one")
        obfs = (
            "hysteria2://secret@example.com:8444?sni=example.com&alpn=h3"
            "&mport=20000-20999&obfs=salamander&obfs-password=obfs#hy2-obfs"
        )

        def classify(xhttp_link: str) -> str:
            with tempfile.TemporaryDirectory() as temp:
                payload = Path(temp) / "subscription"
                payload.write_text(
                    base64.b64encode((xhttp_link + "\n" + obfs).encode()).decode(),
                    encoding="ascii",
                )
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        source,
                        "2",
                        str(payload),
                        str(Path(temp) / "missing"),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return result.stdout.strip()

        self.assertEqual(classify(packet_up), "ok")
        self.assertEqual(classify(stream_one), "xhttp-tls-mode")

    def test_cloudflare_lane_is_bwg_only_and_checks_the_proxy(self) -> None:
        text = self.text
        # The fallback only survives an origin-IP block while the record stays
        # proxied, so a DNS-only revert has to be drift rather than a silent
        # downgrade to "same address as every other lane".
        self.assertIn(
            '$cdnHost = @{ bwg = "cdn.sciman.top"; zz = "zzcdn.sciman.top" }[$Profile]',
            text,
        )
        self.assertIn("echo '==cdn-fallback=='", text)
        self.assertIn("print('not-proxied')", text)
        self.assertIn("cdn=not-applicable", text)
        # A green origin with a dead edge (SSL-mode flip) must still drift.
        self.assertIn(
            "cdn_edge_code=\"$(curl -s -m 10 -o /dev/null -w '%{http_code}'", text
        )
        self.assertIn("cdn_edge=${cdn_edge_code:-000}", text)
        self.assertIn('"__CDN_HOST__"', text)
        self.assertIn("cdn_cert_ok=0", text)
        self.assertIn('-checkhost "$CDN_HOST"', text)
        self.assertIn("https://www.cloudflare.com/ips-v4", text)
        self.assertIn("ipaddress.ip_address(address) in network", text)
        self.assertIn("print('unverified')", text)
        self.assertNotIn(
            'getent ahostsv4 "$EXPECTED_REALITY_HOST" 2>/dev/null | awk',
            text,
        )

    def test_served_vasma_subscription_is_checked(self) -> None:
        text = self.text
        # zz republishes subscribe_local but the client-visible file is
        # subscribe/default/<md5(email+salt)>; checking only the local copy
        # reported "applied" while the served URL kept handing out the dead
        # 22752 REALITY node.
        self.assertIn("echo '==vasma-sub=='", text)
        self.assertIn("/etc/v2ray-agent/subscribe/default", text)
        self.assertIn("security=reality[^#]*type=tcp", text)
        self.assertIn("security=tls[^#]*type=xhttp", text)
        self.assertIn("vasma_sub_served=", text)
        self.assertIn("vasma_sub=not-applicable", text)

    def test_certificate_expiry_is_checked_through_nginx(self) -> None:
        text = self.text
        # The CDN lane is the only one left after an origin-IP block, and its
        # zz origin certificate is a manual copy of bwg's wildcard. A failed
        # renewal must surface as drift before the lane dies.
        self.assertIn("echo '==tls-expiry=='", text)
        self.assertIn("ssl_certificate[[:space:]]", text)
        self.assertIn('openssl x509 -in "$cert_path" -noout -checkend 1209600', text)
        self.assertIn("tls_expiry=drift", text)

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

    def test_dpkg_exclude_absence_counts_as_drift(self) -> None:
        text = self.text
        # nginx package upgrades (monthly maintenance) re-unpack the stock
        # pages and revert the promotion; without the dpkg path-exclude the
        # camouflage lane is silently undone on every nginx release
        # (2026-11-01: 1.31.x -> 1.31.8 wiped the root on both hosts). The
        # gate must audit the exclusion and name it as camouflage drift.
        self.assertIn(
            'DPKG_EXCLUDE_CONF="${VPS_DRIFT_DPKG_CONF:-'
            '/etc/dpkg/dpkg.cfg.d/vps-launcher-camouflage}"',
            text,
        )
        self.assertIn("dpkg_exclude=missing", text)
        self.assertIn("dpkg_exclude=partial", text)
        self.assertIn("dpkg_exclude=present", text)
        self.assertIn("path-exclude=/usr/share/nginx/html/index.html", text)
        self.assertIn("path-exclude=/usr/share/nginx/html/50x.html", text)
        self.assertIn(
            "path-exclude=/usr/share/nginx/html/index.nginx-debian.html", text
        )
        self.assertIn('if [ "$dpkg_hits" -ne 3 ]; then', text)

    def test_v2ray_updater_lane_is_audited_against_repo_source(self) -> None:
        text = self.text
        # The weekly vasma updater is a root-executed projected file outside
        # the CPA doctor hash pairs; this lane is its only drift auditor. The
        # expected script sha comes from the repo source verbatim and the
        # canonical schedule is parsed from the projector, never restated.
        self.assertIn("==v2ray-updater==", text)
        self.assertIn("v2ray_updater=drift script-sha-mismatch", text)
        self.assertIn("v2ray_updater_cron=drift missing", text)
        self.assertIn('"__UPDATER_SHA__"', text)
        self.assertIn('"__UPDATER_CRON_SHA__"', text)
        self.assertIn(
            'Join-Path $PSScriptRoot "remote\\v2ray-agent-script-update.sh"', text
        )
        self.assertIn("v2ray_agent_script_update_cron.ps1", text)
        self.assertIn("Canonical v2ray updater schedule not found", text)
        # The expected cron text must carry real newlines (bash printf wrote
        # the remote file), not a literal backslash-n sequence.
        self.assertIn('"SHELL=/bin/bash`n"', text)
        self.assertIn('--apply`n"', text)

    def test_v2ray_install_script_is_audited_against_the_source_pin(self) -> None:
        text = self.text
        self.assertIn("v2ray-agent-source-pin.json", text)
        self.assertIn("__V2RAY_INSTALL_SHA__", text)
        self.assertIn("v2ray_install=drift script-missing", text)
        self.assertIn("v2ray_install=drift script-sha-mismatch", text)

    def test_alt_sni_marker_loss_with_live_artifacts_is_drift(self) -> None:
        text = self.text
        # A vasma full reinstall can wipe /etc/v2ray-agent including the
        # lane marker while the alt vhost / multi-SNI REALITY trees survive.
        # The audit must not silently retire in that state: the marker-missing
        # branch probes for lane artifacts and reports reality-alt-sni drift.
        self.assertIn(
            "reality_alt_sni=drift audit-marker-missing-but-lane-artifacts-present",
            text,
        )
        self.assertIn("reality_alt_sni=not-applied", text)
        marker_probe_idx = text.index("audit-marker-missing-but-lane-artifacts-present")
        not_applied_idx = text.index("echo 'reality_alt_sni=not-applied'")
        self.assertLess(marker_probe_idx, not_applied_idx)
        self.assertIn("len(rs.get('serverNames') or []) > 1", text)

    def test_builder_check_marker_is_residue(self) -> None:
        text = self.text
        # zz ships a 0-byte builder `check` marker in the document root; the
        # residue scan must classify it like the other template debris, and
        # prose containing the word "check" must not protect the marker via
        # the reference filter.
        self.assertIn("-o -iname 'check'", text)
        self.assertIn("*/check", text)

    def test_stock_nginx_artifacts_count_as_drift(self) -> None:
        # 50x.html answers 200 on every vhost serving the root and comes back
        # with an nginx (re)install; the gate must catch it independently of
        # the root index comparison.
        text = self.text
        self.assertIn("camouflage=stock-artifact", text)
        self.assertIn("50x.html", text)
        self.assertIn("index.nginx-debian.html", text)

    def test_branded_404_wiring_counts_as_drift(self) -> None:
        # A vasma/nginx reinstall that drops the http-level error_page drop-in
        # restores nginx's stock 404 page, an active-probe tell, while every
        # service keeps answering. The gate must notice the regression.
        text = self.text
        self.assertIn("camouflage=404-branded", text)
        self.assertIn("camouflage=404-stock-tell", text)
        self.assertIn("vps-launcher-camouflage-errors.conf", text)
        self.assertIn(
            "error_page[[:space:]]+404([[:space:]]+=[0-9]+)?[[:space:]]+/404\\.html",
            text,
        )

    def test_template_residue_counts_as_drift(self) -> None:
        # Theme zips, PSD sources, __MACOSX sidecars and builder
        # Dockerfile/README/manifest/service-worker files are a hand-assembled
        # decoy tell that a vasma reinstall re-fetches. The camouflage lane
        # quarantines them; the gate must flag their reappearance. Referenced
        # assets are skipped so live content is never reported.
        text = self.text
        self.assertIn("camouflage=residue", text)
        self.assertIn("residue_drift=1", text)
        self.assertIn("--exclude-dir=s", text)
        for marker in (
            "*.psd",
            "*.zip",
            "__MACOSX",
            "precache-manifest.*",
            "*.worker.js",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_listener_probe_is_exact_suffix(self) -> None:
        # ":443" must not also match ":4433", and a shorter local address must
        # not match a longer probe: index() answers 0 for "not found" while the
        # suffix position of "*:443" against ":22752" also computes to 0, so an
        # unguarded comparison reported every >=5-digit port as up.
        needle = "length($4) >= length(p) && index($4, p) == length($4) - length(p) + 1"
        self.assertIn(needle, self.text)
        # Both tcp and udp branches must carry the guard.
        self.assertEqual(self.text.count(needle), 2)

    def test_stale_argo_firewall_rules_are_scoped_and_visible(self) -> None:
        # zz's disabled temporary tunnel still left named 30000-30010/8080
        # ACCEPT rows. Do not flag arbitrary firewall rules: only these exact
        # rows, with their listener precondition in the operator runbook.
        text = self.text
        self.assertIn("==stale-firewall==", text)
        self.assertIn('--comment "ArgoX PORT ', text)
        self.assertIn("stale_firewall=argo-leftover", text)

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

    def test_orphan_xray_processes_are_visible_but_not_drift(self) -> None:
        # The alt-SNI proof client leaks as an orphaned `xray run -c
        # /tmp/...` when the channel dies mid-proof (2026-11-03, both
        # machines). Like the listeners, it is not lane-replayable, so it
        # must not enter HARDENING_DRIFT; it must still name the PID and its
        # args so the operator sees what leaked and from which scratch dir.
        text = self.text
        self.assertIn("==xray-process==", text)
        self.assertIn("xray_extra_pid=", text)
        self.assertIn("xray_process=ok", text)
        self.assertIn("xray_process=orphan extras=", text)
        self.assertNotIn("drift+=('xray-process')", text)
        # A worker fork parented by the service PID is not an orphan.
        self.assertIn('"$xray_ppid" != "$xray_service_pid" ]', text)

    def test_host_security_tracks_mptcp_app_mitigation_and_kernel_cve(self) -> None:
        # Xray-v26.10.31's MPTCP default is an application-layer mitigation,
        # not the kernel fix for CVE-2026-98432. Ubuntu 24.04 has not shipped
        # a fixed 6.8.0 kernel at all (the earlier "fixed at -149" threshold
        # was a false positive), so until upstream publishes one the gate must
        # keep the kernel marked vulnerable and require the guard firewall's
        # IP-options drop plus the MPTCP controls for a green verdict.
        text = self.text
        self.assertIn("echo '==host-security=='", text)
        self.assertIn("/proc/sys/net/mptcp/enabled", text)
        self.assertIn("net[.]mptcp[.]enabled", text)
        self.assertIn("26.10.31", text)
        self.assertIn("CVE-2026-98432", text)
        self.assertIn("noble_fixed_abi = None", text)
        self.assertNotIn('fixed_kernel = "6.8.0-149"', text)
        self.assertIn("hdrlength > 5", text)
        # The detection must not use grep -q: under pipefail, -q exits at the
        # first match and SIGPIPEs nft into a false "missing" (2026-11-10).
        self.assertNotIn("grep -q 'hdrlength", text)
        self.assertIn("ip_options_drop=%s", text)
        self.assertIn("kernel_cve_2026_98432=%s fixed_kernel=%s", text)
        self.assertIn("kernel_cve_2026_98432=", text)

        recovery = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("scripts", "proxy_hardening_lane_recover.ps1")
            .read_text(encoding="utf-8")
        )
        self.assertIn('"host-security"        = @()', recovery)
        self.assertIn("CVE-2026-98432", recovery)

        source = text.split("<<'PY_HOST_SECURITY'\n", 1)[1].split(
            "\nPY_HOST_SECURITY", 1
        )[0]

        def classify(
            os_id: str,
            os_version: str,
            kernel: str,
            runtime: str,
            persisted: str,
            xray: str,
            ip_options: str = "missing",
        ) -> dict[str, str]:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    source,
                    os_id,
                    os_version,
                    kernel,
                    runtime,
                    persisted,
                    xray,
                    ip_options,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return dict(line.split("=", 1) for line in result.stdout.splitlines())

        vulnerable = classify(
            "ubuntu", "24.04", "6.8.0-147-generic", "0", "yes", "26.10.31"
        )
        self.assertTrue(vulnerable["kernel_cve_2026_98432"].startswith("vulnerable "))
        self.assertIn("fixed_kernel=none-shipped", vulnerable["kernel_cve_2026_98432"])
        self.assertEqual(vulnerable["host_security"], "drift")

        # No fixed noble kernel exists yet, so even -149 stays "vulnerable";
        # with both mitigations live the posture is accepted, without the
        # firewall rule it is not.
        mitigated = classify(
            "ubuntu", "24.04", "6.8.0-149-generic", "0", "yes", "26.10.31", "present"
        )
        self.assertTrue(mitigated["kernel_cve_2026_98432"].startswith("vulnerable "))
        self.assertEqual(mitigated["ip_options_drop"], "present")
        self.assertEqual(mitigated["host_security"], "ok")

        missing_rule = classify(
            "ubuntu", "24.04", "6.8.0-149-generic", "0", "yes", "26.10.31"
        )
        self.assertEqual(missing_rule["ip_options_drop"], "missing")
        self.assertEqual(missing_rule["host_security"], "drift")

        missing_app_mitigation = classify(
            "ubuntu", "24.04", "6.8.0-149-generic", "1", "yes", "26.10.21", "present"
        )
        self.assertEqual(missing_app_mitigation["xray_mptcp_mitigation"], "missing")
        self.assertEqual(missing_app_mitigation["host_security"], "drift")

    def test_command_substitutions_survive_pipefail(self) -> None:
        # Under `set -Eeuo pipefail`, an unguarded `var="$(...)"` whose
        # pipeline can fail aborts the whole gate with a non-protocol exit:
        # a dead true-SNI answer (stealth_header), a corrupt published list
        # (file_uuid), or a cert path that vanished from disk (not_after) are
        # all audited states that must surface as drift rows, not as a
        # dispatcher-level throw. And a `grep -q` over a still-writing stream
        # SIGPIPEs into a false "missing" verdict, which on zz would send the
        # wired recovery to replay the lane remotely (the nft check here and
        # the 2026-11-02 single false reality-alt-sni are the same family).
        text = self.text
        self.assertIn('| head -1 || true)"', text)
        self.assertIn("sed 's/^notAfter=//' || true)\"", text)
        # The loaded-config cross-check judges the captured variable instead
        # of grepping a pipe, exactly like the guarded nft check.
        self.assertNotIn('| grep -qs "server_name $alt_name;"', text)
        self.assertIn('case "$loaded_config" in', text)
        self.assertIn('case "$decoded_list" in', text)
        self.assertNotIn('grep -q "sni=$alt_expected"', text)

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
        # `dest` remains a supported alias of `target` in Xray.
        self.assertIn("rs.get('target') or rs.get('dest')", text)

    def test_reality_flow_is_drift_checked(self) -> None:
        text = self.text
        # Vision is a per-user vless field, not a transport setting: a vasma
        # re-configuration that answers "no" to the Vision prompt leaves a
        # plain VLESS-over-TLS inbound that still authenticates, and its
        # inner-handshake shape is the TLS-in-TLS signature upstream calls
        # already-targeted. Both cores must advertise the flow.
        self.assertIn("==reality-flow==", text)
        self.assertIn("xtls-rprx-vision", text)
        self.assertIn("reality_flow=ok", text)
        self.assertIn("reality_flow=not-applicable", text)

    def test_xhttp_drift_checks_the_front_certificate_name(self) -> None:
        text = self.text
        self.assertIn("cert_ok", text)
        xhttp = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("scripts", "xhttp_nginx_front.ps1")
            .read_text(encoding="utf-8")
        )
        self.assertIn("XHTTP_FRONT_TLS_HOST_MISMATCH", xhttp)

    def test_alt_sni_requires_a_certificate_covering_the_new_name(self) -> None:
        text = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("scripts", "reality_alt_sni_lane.ps1")
            .read_text(encoding="utf-8")
        )
        self.assertIn("ALT_SNI_CERT_NOT_FOUND", text)
        self.assertIn("-checkhost", text)
        self.assertIn("does match", text)
        self.assertIn("tls_material=", text)

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
        # The published TLS node selects packet-up, while the server keeps
        # auto so other client modes remain available. A server-mode rewrite
        # must surface instead of publishing a mode the server no longer owns.
        self.assertIn("xhttp_mode == 'auto'", text)
        self.assertIn("xhttp_front=server-mode", text)

    def test_obfs_fragment_contract_is_drift_checked(self) -> None:
        # Fragment existence plus a live listener is not the contract: the
        # fragment must still be a salamander-obfuscated TLS hysteria2
        # inbound on the obfs port, or the backup UDP lane is silently gone.
        text = self.text
        self.assertIn("obfs_contract=", text)
        self.assertIn("!= 'salamander'", text)
        self.assertIn("obfs_contract=invalid", text)
        # A correct source fragment plus an open UDP port still does not prove
        # the running merged config carried it through, or that both copies
        # still share one obfs password.
        self.assertIn("runtime-missing", text)
        self.assertIn("runtime-password-drift", text)
        self.assertIn('runtime_config="${VPS_DRIFT_SB_CONF_DIR:', text)

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

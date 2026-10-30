"""Contract tests for scripts/proxy_lane_acceptance.ps1.

The acceptance probe must prove a real client handshake, never treat
"service active" as acceptance, and must classify lanes that do not apply to a
host architecture as N/A rather than FAIL.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "proxy_lane_acceptance.ps1"


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
        self.assertNotIn("port:19101", text)
        self.assertNotIn('listen_port":19102', text)
        # No secrets are interpolated into the raw one-quoted payload. An
        # optional operator salt is validated as bash data before transport.
        self.assertNotIn('OBFS_PASSWORD="__OBFS_PASSWORD__"', text)
        self.assertIn("OBFS_PASSWORD_FILE", text)
        self.assertIn("[A-Za-z0-9._-]{1,64}", text)
        # No remote config mutation: no merge/restart/apply in this lane.
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("sing-box merge", text)
        self.assertNotIn("nginx -t", text)

    def test_na_does_not_fail_the_run(self) -> None:
        # A lane absent on this host (e.g. Hysteria2 on the Xray-only host) is
        # N/A: it must not set the failure flag, but it does downgrade an
        # otherwise clean run to PARTIAL so an omitted lane is visible.
        text = self.text
        record = text.split("record() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("none) echo", record)
        self.assertIn("fail=1", record)
        self.assertIn("na=1", record)

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

    def test_present_but_unusable_xray_is_failure_not_na(self) -> None:
        text = self.text
        # A leftover conf directory on a sing-box-only host is still N/A; a
        # present-but-unusable binary or a binary with missing source is FAIL.
        self.assertIn('if [ ! -e "$XRAY_BINARY" ]; then', text)
        self.assertIn("dormant conf directory", text)
        self.assertIn('elif [ ! -x "$XRAY_BINARY" ]; then', text)
        self.assertIn('elif [ ! -d "$XRAY_CONF" ]; then', text)
        self.assertIn('record reality-xray 1 "binary_or_source_unusable"', text)

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
        # PublicName is interpolated into the bash payload, so host syntax must
        # be rejected before transport.
        self.assertIn("[A-Za-z0-9._:\\[\\]-]+", text)


if __name__ == "__main__":
    unittest.main()

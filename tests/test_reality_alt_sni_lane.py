"""Contract tests for scripts/reality_alt_sni_lane.ps1.

All direct lanes used one SNI, so an exact-hostname block took five of the six
lanes down at once and left only the CDN lane. This lane adds a second name to
the same REALITY inbounds, serves it from the existing camouflage vhost, and
records a marker so the drift gate can tell "never applied" from "regressed".
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reality_alt_sni_lane.ps1"


class RealityAltSniLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_is_read_only_until_apply(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn('APPLY="__APPLY__"', text)
        self.assertIn("if [ \"$APPLY\" != '1' ]; then", text)
        self.assertIn("ALT_SNI_DRY_RUN=1", text)

    def test_refuses_an_unresolvable_or_foreign_hostname(self) -> None:
        text = self.text
        # The name has to resolve to this host before anything is written,
        # otherwise the lane publishes a node that can never connect.
        self.assertIn("ALT_SNI_DNS_MISSING", text)
        self.assertIn("ALT_SNI_DNS_MISMATCH", text)
        self.assertIn("getent ahostsv4", text)

    def test_derives_the_serving_port_from_the_live_reality_target(self) -> None:
        text = self.text
        # vasma picks the fallback port, so a hardcoded one would serve the
        # alt name from a vhost the REALITY handshake never reaches.
        self.assertIn("'targetPort': int(target.rsplit(':', 1)[1])", text)
        self.assertIn('ALT_PORT="$(python3 -c', text)

    def test_alt_vhost_matches_the_fallback_port_family(self) -> None:
        text = self.text
        # When nginx itself listens on [::]:port, an IPv4-only alt vhost sends
        # alt-name clients to the [::] default_server and gets rejected.
        self.assertIn('ALT_V6_LISTEN="    listen [::]:$ALT_PORT ssl;"', text)
        self.assertIn('grep -qx "\\[::\\]:$ALT_PORT"', text)
        self.assertIn("    listen $ALT_PORT ssl;\n$ALT_V6_LISTEN\n", text)

    def test_adds_the_name_without_touching_existing_clients(self) -> None:
        text = self.text
        # serverNames is append-only: the primary SNI stays first so every
        # already-published node keeps authenticating unchanged.
        self.assertIn("if alt not in names:", text)
        self.assertIn("names.append(alt)", text)
        self.assertNotIn("rs['serverNames'] = [alt]", text)

    def test_apply_backs_up_and_rolls_back(self) -> None:
        text = self.text
        self.assertIn("ALT_SNI_ROLLBACK", text)
        self.assertIn("ALT_SNI_APPLY_FAILED", text)
        self.assertIn('cp -a "$f" "$BACKUP/$(basename "$f")"', text)
        self.assertIn('rm -f -- "$MARKER"', text)
        # A rollback also clears the plan file and the proof client's scratch
        # dir, both of which hold material from the live config.
        self.assertIn("rm -f -- /tmp/alt-sni-plan.json", text)
        self.assertIn('if [ -n "${WORK:-}" ]; then rm -rf -- "$WORK"; fi', text)

    def test_proves_the_lane_end_to_end_before_reporting_success(self) -> None:
        text = self.text
        self.assertIn("alt_sni_handshake=ok", text)
        self.assertIn("alt_sni_lane=ok", text)
        self.assertIn("ALT_SNI_APPLIED", text)
        self.assertIn("exit 6", text)
        # The restore trap must stay armed until the marker is written, so a
        # failure inside the end-to-end proof still rolls the lane back.
        self.assertNotIn("trap 'cleanup' EXIT", text)
        self.assertIn("cleanup || true", text)
        # The proof client is Vision-specific: selection must stay on the
        # raw-tcp REALITY inbound even when an XHTTP one carries the front.
        self.assertIn(
            "tcp_targets = [t for t in plan['targets'] if t.get('network', 'tcp') == 'tcp']",
            text,
        )
        self.assertIn("ALT_SNI_NO_VISION_TARGET", text)
        self.assertIn("raise SystemExit(7)", text)


if __name__ == "__main__":
    unittest.main()

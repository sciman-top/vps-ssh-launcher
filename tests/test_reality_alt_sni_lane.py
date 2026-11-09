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
        # dir, both of which hold material from the live config. The plan is
        # a 0600 mktemp file now; the fixed /tmp path landed 0644 because the
        # shell redirection is set up before any subshell umask could apply.
        self.assertIn('rm -f -- "$PLAN_FILE"', text)
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

    def test_proof_client_is_signal_safe(self) -> None:
        # 2026-11-03: a transport timeout Hangups the payload, and an untrapped
        # HUP dies without ever running the EXIT trap - exactly how the proof
        # clients were left orphaned on both machines. The catchable signals
        # must route through `exit` so restore stays the single rollback path,
        # and restore itself must kill the spawned client.
        text = self.text
        self.assertIn("trap 'exit 129' HUP", text)
        self.assertIn("trap 'exit 130' INT", text)
        self.assertIn("trap 'exit 143' TERM", text)
        self.assertIn('kill "$CLIENT_PID" 2>/dev/null || true', text)
        self.assertIn('wait "$CLIENT_PID" 2>/dev/null || true', text)
        self.assertIn('CLIENT_PID=""', text)

    def test_serves_the_alt_name_on_the_subscription_port(self) -> None:
        # The alternate name is the recovery subscription entry. Serving it only
        # on the REALITY fallback port forced operators to guess a port, and zz
        # published a URL whose certificate did not cover the name at all.
        text = self.text
        self.assertIn(
            'STANDBY_VHOST="$NGINX_CONF_D/vps-launcher-alt-sni-sub.conf"', text
        )
        self.assertIn(
            'standby_port="$(python3 - "$NGINX_CONF_D/subscribe.conf" "$ALT_PORT"', text
        )
        self.assertIn("alt_sni_standby_handshake=ok", text)
        self.assertIn('-checkhost "$ALT"', text)
        # The extra vhost serves the shared static root only: no proxy, no new
        # credential surface, and never a second bind on the fallback port.
        self.assertIn("if port and port != fallback:", text)

    def test_rollback_removes_the_standby_vhost_it_created(self) -> None:
        # The server is created by this run; a failed apply must not leave a
        # second name served on the subscription port.
        text = self.text
        self.assertIn('rm -f -- "$STANDBY_VHOST"', text)
        self.assertIn(
            'cp -a "$STANDBY_VHOST" "$BACKUP/$(basename "$STANDBY_VHOST")"', text
        )
        # Declared before the apply branch so the EXIT trap can act even when the
        # failure happens before the file is rendered.
        self.assertLess(
            text.index('STANDBY_VHOST=""'),
            text.index("1b. Serve the same name on the subscription port"),
        )


if __name__ == "__main__":
    unittest.main()

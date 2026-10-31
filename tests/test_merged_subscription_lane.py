"""Contract tests for scripts/merged_subscription_lane.ps1.

vasma publishes only the inbounds it owns, so the Hysteria2 obfs lane and the
Cloudflare lane never reach the subscription. This lane derives all six nodes
from the live configs and publishes them as one base64 list.
"""

from __future__ import annotations

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

    def test_derives_every_lane_from_live_configs(self) -> None:
        text = self.text
        for marker in (
            "xtls-rprx-vision#bwg-reality-vision",
            "#bwg-xhttp-reality",
            "#bwg-xhttp-tls",
            "#bwg-hy2-base",
            "#bwg-hy2-obfs",
            "#bwg-cdn-ws",
        ):
            with self.subTest(node=marker):
                self.assertIn(marker, text)
        self.assertIn("MERGED_SUB_INCOMPLETE", text)
        self.assertIn('if [ "$count" -lt 6 ]', text)

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


if __name__ == "__main__":
    unittest.main()

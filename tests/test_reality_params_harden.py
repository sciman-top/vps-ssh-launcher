"""Contract tests for scripts/reality_params_harden.ps1.

The lane blanks REALITY's minClientVer / maxTimeDiff, which upstream evaluates
only after the AEAD handshake: set, they cannot stop a probe and only widen
what gets relayed to the fallback. vasma restores its 2021 defaults on a
reinstall, so the lane must stay opt-in, backup-first, locked and
rollback-verified.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reality_params_harden.ps1"


class RealityParamsHardenTests(ScriptValidationMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_apply_is_opt_in_and_locked(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn("if [ \"$APPLY\" != '1' ]; then", text)
        self.assertIn("flock -n 9", text)
        self.assertIn("/run/vps-ssh-launcher-maintenance.lock", text)

    def test_backup_precedes_the_write_and_rollback_is_verified(self) -> None:
        text = self.text
        backup = text.index('cp -a "$XRAY_CONF/." "$backup_dir/xray/"')
        write = text.index('cleared="$(run_helper clear')
        self.assertLess(backup, write)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("ROLLBACK_FAILED", text)
        self.assertIn("trap 'rollback 129' HUP", text)

    def test_apply_tests_the_whole_confdir_before_restart(self) -> None:
        text = self.text
        test = text.index('"$XRAY_BINARY" run -test -confdir "$XRAY_CONF"')
        restart = text.index('systemctl restart "$XRAY_SERVICE"')
        self.assertLess(test, restart)
        self.assertIn("XRAY_CONFIG_TEST_FAILED", text)
        self.assertIn("POST_CLEAR_STILL_WEAK", text)

    def test_never_touches_shortids_or_keys(self) -> None:
        # shortIds stay published in the subscription; rotating them is a
        # different, coordinated change and must not ride along here.
        text = self.text
        # Only these two assignments may exist; any other REALITY field write
        # (shortIds, serverNames, keys) would mean the lane outgrew its slice.
        writes = re.findall(r'rs\["(\w+)"\] = ', text)
        self.assertEqual(sorted(writes), ["maxTimeDiff", "minClientVer"])
        self.assertIn('rs["minClientVer"] = ""', text)
        self.assertIn('rs["maxTimeDiff"] = 0', text)

    def test_hardened_pattern_requires_the_empty_ver_before_the_zero_diff(self) -> None:
        # The helper prints "port=N min_client_ver= max_time_diff=0". A pattern
        # anchored on the wrong side (max_time_diff=0 before min_client_ver=)
        # matched every line and reported "hardened" for the unhardened host.
        self.assertIn("*min_client_ver=*max_time_diff=0)", self.text)
        self.assertNotIn("*max_time_diff=0*min_client_ver=)", self.text)

    def test_invalid_config_is_an_error_not_a_noop(self) -> None:
        text = self.text
        self.assertIn("raise SystemExit(2)", text)
        self.assertIn("reality_params=invalid", text)

    def test_dry_run_reports_state_without_writing(self) -> None:
        text = self.text
        self.assertIn("reality_params=hardened", text)
        self.assertIn("reality_params=needs_clear", text)
        dry = text.index(
            "if [ \"$APPLY\" != '1' ]; then", text.index("reality_params=needs_clear")
        )
        first_write = text.index('mktemp -d "$BACKUP_PARENT/reality-params')
        self.assertLess(dry, first_write)


if __name__ == "__main__":
    unittest.main()

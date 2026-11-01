"""Contract tests for scripts/run_proxy_drift_task.ps1.

The scheduled wrapper exists so an unattended zz drift verdict is not lost:
Task Scheduler discards stdout and its exit code only shows as a task result.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_proxy_drift_task.ps1"


class RunProxyDriftTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_runs_the_read_only_gate_for_both_profiles(self) -> None:
        text = self.text
        self.assertIn('[ValidateSet("bwg", "zz")]', text)
        self.assertIn("proxy_hardening_drift.ps1", text)
        self.assertNotIn("[switch]$Apply", text)

    def test_keeps_the_verdict_in_a_log_and_the_exit_code(self) -> None:
        text = self.text
        self.assertIn('"drift-$Profile-$stamp.log"', text)
        self.assertIn("$output | Set-Content -LiteralPath $logPath", text)
        self.assertIn("$exitCode = $LASTEXITCODE", text)
        self.assertIn("exit $exitCode", text)
        self.assertIn("DRIFT_LOG=", text)


if __name__ == "__main__":
    unittest.main()

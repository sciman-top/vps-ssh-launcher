"""Regression tests for the projected remote runtimes.

Each case pins a defect that only a live host could have shown: a subshell that
dropped the rollback-image inventory, an empty backup directory that evicted
real ones, a partially destructive validation loop, and a success marker that
was not the last line.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin


ROOT = Path(__file__).resolve().parents[1]
REMOTE = ROOT / "scripts" / "remote"


def _bash() -> str | None:
    return ScriptValidationMixin._resolve_bash()


def _run_bash(bash: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ScriptValidationMixin._bash_command(bash, *args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )


class ShellSyntaxTests(unittest.TestCase):
    def test_every_projected_shell_runtime_still_parses(self) -> None:
        bash = _bash()
        if bash is None:
            self.skipTest("bash is not available")
        for name in (
            "cpa-auto-update.sh",
            "cpa-guardrail-apply.sh",
            "cpa-guardrail-doctor.sh",
            "cpa-guardrail-deactivate-oauth-luna.sh",
            "cpa-guardrail-quarantine-oauth-luna.sh",
            "cpa-admission-launch.sh",
            "cpa-admission-integrity-check.sh",
        ):
            with self.subTest(script=name):
                target = ScriptValidationMixin._bash_path(bash, REMOTE / name)
                completed = _run_bash(bash, "-n", target)
                self.assertEqual(completed.returncode, 0, completed.stderr)


class RollbackImageInventoryTests(unittest.TestCase):
    def test_the_backup_inventory_is_collected_in_the_calling_shell(self) -> None:
        # `retained_backup_paths | while ...` ran the body in a subshell, so every
        # "$inventory" assignment was discarded and prune_images lost the whole
        # keep-8 protection set.
        source = (REMOTE / "cpa-auto-update.sh").read_text(encoding="utf-8")
        self.assertNotIn('retained_backup_paths "" |', source)
        self.assertIn('if ! retained_backup_paths "" >"$inventory_file"; then', source)
        self.assertIn('done <"$inventory_file"', source)
        self.assertIn("inventory_status", source)

    def test_a_process_substitution_keeps_the_value(self) -> None:
        bash = _bash()
        if bash is None:
            self.skipTest("bash is not available")
        script = "\n".join(
            [
                "set -Eeuo pipefail",
                "inventory=''",
                "paths() { printf '%s\\0' /backup-a /backup-b; }",
                "while IFS= read -r -d '' entry; do",
                '  inventory="${inventory}${entry}"$NL',
                "done < <(paths)",
                'echo "len=${#inventory}"',
            ]
        ).replace("$NL", "$" + chr(39) + chr(92) + "n" + chr(39))
        completed = _run_bash(bash, "-c", script)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "len=20", completed.stdout)


class RefusalCleanupTests(unittest.TestCase):
    def test_a_refused_apply_removes_its_empty_backup_directory(self) -> None:
        # The empty directory is newer than every real backup, so it occupied a
        # keep-8 slot and evicted one that actually held rollback material.
        source = (REMOTE / "cpa-guardrail-apply.sh").read_text(encoding="utf-8")
        self.assertIn("discard_empty_backup()", source)
        self.assertIn(
            "discard_empty_backup",
            source.split("REFUSE cpa_apply_requires_idle_lanes")[0][-200:],
        )


class OAuthRemovalTests(unittest.TestCase):
    def test_oauth_material_is_collected_before_anything_is_deleted(self) -> None:
        # The old loop unlinked as it walked, so one unexpected JSON later in the
        # walk aborted the run with earlier credentials already destroyed.
        source = (REMOTE / "cpa-guardrail-deactivate-oauth-luna.sh").read_text(
            encoding="utf-8"
        )
        collect = source.index("doomed = []")
        refusal = source.index('raise SystemExit("REFUSE unexpected OAuth JSON type")')
        unlink = source.index("path.unlink()")
        self.assertLess(collect, refusal)
        self.assertLess(refusal, unlink, "validation must precede any deletion")


class MarkerOrderTests(unittest.TestCase):
    def test_the_success_marker_is_the_last_line(self) -> None:
        # maintenance/adapters.py matches the FINAL stdout line exactly.
        lines = [
            line.strip()
            for line in (REMOTE / "cpa-guardrail-apply.sh")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        self.assertEqual(lines[-1], 'echo "GUARDRAILS_APPLIED"')


class CheckHeartbeatTests(unittest.TestCase):
    def test_a_manual_check_does_not_overwrite_the_daily_heartbeat(self) -> None:
        source = (REMOTE / "cpa-auto-update.sh").read_text(encoding="utf-8")
        self.assertIn('STATUS_FILE="${STATUS_FILE%.status}-check.status"', source)
        self.assertIn('STATUS_NAME="cpa-update-check"', source)


if __name__ == "__main__":
    unittest.main()

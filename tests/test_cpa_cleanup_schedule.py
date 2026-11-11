"""Cleanup must run on every updater pass, not only when a release lands.

Defect found 2026-11-09: cpa-auto-update.sh exited 0 when CUR == TARGET
("no newer mature release") and every backup prune sat after that exit. So
cleanup only ran on days when upstream both published AND cleared the 72h
soak, while the families that actually grow (admission-*, plugin-*,
converge-* from manual transactions) have nothing to do with releases.
Measured: 117 dirs / ~800 MiB with no policy touching them.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

UPDATER = Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
DOCTOR = Path(__file__).parents[1] / "scripts/remote/cpa-guardrail-doctor.sh"


class DailyCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = UPDATER.read_text(encoding="utf-8")
        self.no_update_exit = self.source.index('if [[ "$CUR" == "$TARGET" ]]; then')

    def test_local_prunes_run_before_the_no_update_exit(self) -> None:
        for function in (
            "prune_backups",
            "prune_manual_backup_families",
            "prune_verified_build_source_archives",
            "prune_build_cache",
            "prune_images",
        ):
            with self.subTest(function=function):
                marker = "\n" + function + "\n"
                call = self.source.index(marker)
                self.assertLess(
                    call,
                    self.no_update_exit,
                    function + " must run on the no-update path too",
                )

    def test_image_prune_is_for_deterministic_unreferenced_generations(self) -> None:
        call = self.source.index("\nprune_images\n")
        self.assertLess(call, self.no_update_exit)

    def test_prune_runs_exactly_once_each(self) -> None:
        for function in (
            "prune_backups",
            "prune_manual_backup_families",
            "prune_images",
        ):
            with self.subTest(function=function):
                self.assertEqual(
                    self.source.count("\n" + function + "\n"),
                    1,
                    function + " must not be duplicated across paths",
                )

    def test_no_update_path_still_sends_no_upstream_traffic(self) -> None:
        # The 2026-09-21 rule: the no-update pass must not consume the
        # subscription account. Cleanup moving up must not drag a generation
        # or provider probe with it.
        block = self.source[
            self.no_update_exit : self.source.index('log "OK: no newer mature release;')
        ]
        self.assertNotIn("health generation", block)
        self.assertNotIn("relay-soft", block)
        self.assertIn("non_consuming=true", block)

    def test_doctor_reports_backup_volume_advisory(self) -> None:
        doctor = DOCTOR.read_text(encoding="utf-8")
        self.assertIn("update_backups_advisory=", doctor)
        # An advisory, not a gate: a large retained set is legitimate after a
        # migration, so this must not call mark_fail.
        section = doctor.split("update_backups_advisory=", 1)[0].rsplit(
            'echo "==inventory=="', 1
        )[-1]
        self.assertNotIn("mark_fail", section)

    def test_build_cache_prune_is_pressure_gated_and_recent_cache_survives(
        self,
    ) -> None:
        import subprocess
        import tempfile

        bash = ScriptValidationMixin._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        function = self.source[
            self.source.index("prune_build_cache() {") : self.source.index(
                "secure_error_dumps() {"
            )
        ]
        for free_kib, result, expected in (
            (8388608, 0, False),
            (1048576, 0, True),
            (1048576, 1, True),
        ):
            with tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "cleanup.log"
                harness = "\n".join(
                    [
                        "set -euo pipefail",
                        f"DIR='{ScriptValidationMixin._bash_path(bash, Path(directory))}'",
                        f"LOG='{ScriptValidationMixin._bash_path(bash, log)}'",
                        "RETENTION_MIN_FREE_KIB=4194304",
                        "DOCKER_BUILD_CACHE_KEEP_HOURS=24",
                        'log() { printf "%s\\n" "$*"; }',
                        f"df() {{ printf '\\nfs 1 1 %s 1 /\\n' {free_kib}; }}",
                        "docker() {",
                        '  if [[ "$1" == system ]]; then printf "Build Cache 8GB\\n";',
                        '  elif [[ "$1" == builder ]]; then',
                        "    printf 'Total: 1GB\\n';",
                        '    printf \'ARGS=%s\\n\' "$*" >> "$LOG";',
                        f"    return {result}",
                        "  else return 1; fi",
                        "}",
                        function,
                        "prune_build_cache",
                    ]
                )
                completed = subprocess.run(
                    ScriptValidationMixin._bash_command(bash),
                    input=harness,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(log.exists(), expected, completed.stdout)
                if result != 0:
                    self.assertIn("result=prune_failed", completed.stdout)
                elif expected:
                    self.assertIn("until=24h", log.read_text(encoding="utf-8"))
                    self.assertNotIn("prune --all", log.read_text(encoding="utf-8"))
                    self.assertIn("result=pruned", completed.stdout)
                else:
                    self.assertFalse(log.exists())
                    self.assertIn("skipped=free_space_ok", completed.stdout)

    def test_build_cache_prune_never_uses_cross_state_docker_cleanup(self) -> None:
        script = self.source[
            self.source.index("prune_build_cache() {") : self.source.index(
                "secure_error_dumps() {"
            )
        ]
        self.assertNotIn("--all", script)
        self.assertNotIn("system prune", script)
        self.assertNotIn("volume prune", script)
        self.assertNotIn("image prune", script)
        self.assertNotIn('> "$DIR/auth', script)
        self.assertNotIn('>> "$DIR/auth', script)
        self.assertNotIn('"$DIR/config.yaml"', script)
        self.assertIn("until=${DOCKER_BUILD_CACHE_KEEP_HOURS}h", script)


if __name__ == "__main__":
    unittest.main()

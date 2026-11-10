"""Checks for narrow CPA and monthly local garbage retention policies."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

UPDATER = Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"


def extract_function(name: str) -> str:
    source = UPDATER.read_text(encoding="utf-8")
    start = source.index(name + "() {")
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("unterminated function: " + name)


class LocalHygieneTests(ScriptValidationMixin, unittest.TestCase):
    def test_space_gate_recent_retention_and_failure_are_nonfatal(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        function = extract_function("prune_build_cache")
        for free_kib, prune_status, expected, busy, expected_result in (
            (8 * 1024 * 1024, 0, False, False, ""),
            (1024 * 1024, 0, True, False, "result=pruned"),
            (1024 * 1024, 1, True, False, "result=prune_failed"),
            (1024 * 1024, 1, True, True, "result=prune_busy"),
        ):
            with self.subTest(free_kib=free_kib, prune_status=prune_status, busy=busy):
                with tempfile.TemporaryDirectory() as directory:
                    log = Path(directory) / "auto-update.log"
                    builder_result = (
                        "    printf 'Error response from daemon: cache is being used by another process\\n'"
                        if busy
                        else "    printf 'Total: 1GB\\n'"
                    )
                    script = "\n".join(
                        [
                            "set -euo pipefail",
                            f"DIR='{self._bash_path(bash, Path(directory))}'",
                            f"LOG='{self._bash_path(bash, log)}'",
                            "RETENTION_MIN_FREE_KIB=4194304",
                            "DOCKER_BUILD_CACHE_KEEP_HOURS=24",
                            'log() { printf "%s\\n" "$*"; }',
                            f"df() {{ printf '\\nfs 1 1 {free_kib} 1 /\\n'; }}",
                            "docker() {",
                            '  if [[ "$1" == system ]]; then printf "Build Cache 8GB\\n";',
                            '  elif [[ "$1" == builder ]]; then',
                            '    printf "ARGS=%s\\n" "$*" >> "$LOG"',
                            builder_result,
                            f"    return {prune_status}",
                            "  else return 1; fi",
                            "}",
                            function,
                            "prune_build_cache",
                        ]
                    )
                    result = subprocess.run(
                        self._bash_command(bash),
                        input=script.encode(),
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                    output = result.stdout.decode()
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    self.assertEqual(log.exists(), expected, output)
                    if not expected:
                        self.assertIn("skipped=free_space_ok", output)
                    elif expected_result == "result=prune_busy":
                        self.assertIn(expected_result, output)
                    else:
                        invocation = log.read_text(encoding="utf-8")
                        self.assertIn(
                            "builder prune --force --filter until=24h", invocation
                        )
                        self.assertNotIn("--all", invocation)
                        self.assertIn(expected_result, output)

    def test_cache_hygiene_has_no_image_volume_config_or_credential_writes(
        self,
    ) -> None:
        script = extract_function("prune_build_cache")
        self.assertNotIn("system prune", script)
        self.assertNotIn("volume prune", script)
        self.assertNotIn("image prune", script)
        self.assertNotIn("docker rmi", script)
        self.assertNotIn("$DIR/auth", script)
        self.assertNotIn("$DIR/config.yaml", script)

    def test_monthly_wrapper_reuses_reviewed_orphan_contract(self) -> None:
        wrapper = (
            Path(__file__).parents[1] / "scripts/system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")
        function = wrapper.split("prune_verified_orphan_containers() {", 1)[1].split(
            "\n}\n", 1
        )[0]
        self.assertIn("cpa-wb-failover-build-v3", function)
        self.assertIn("skipped=image_present", function)
        self.assertIn("skipped=name_mismatch", function)
        self.assertIn("verified orphan container hygiene", wrapper)
        self.assertNotIn("docker volume prune", wrapper)
        self.assertNotIn("docker system prune", wrapper)
        self.assertNotIn("docker image prune", wrapper)

    def test_monthly_cleanup_runs_only_after_a_complete_package_phase(self) -> None:
        wrapper = (
            Path(__file__).parents[1] / "scripts/system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'step "verified orphan container hygiene" prune_verified_orphan_containers',
            wrapper,
        )
        self.assertLess(
            wrapper.index('step "journalctl --vacuum-time=30d"'),
            wrapper.index('step "verified orphan container hygiene"'),
        )
        self.assertLess(
            wrapper.index('step "verified orphan container hygiene"'),
            wrapper.index("PACKAGE_STATE_AFTER="),
        )

    def test_manual_family_policy_cannot_claim_updater_backups(self) -> None:
        function = extract_function("prune_manual_backup_families")
        self.assertEqual(function.count("! -name '*-from-v[0-9]*'"), 2)

    def test_only_exact_stopped_orphan_build_containers_are_removed(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        harness = """set -e
LOG="$1"
log() { printf "%s\\n" "$*"; }
"""
        removable = "1" * 64
        unnamed = "2" * 64
        unavailable = "sha256:" + "3" * 64
        present = "sha256:" + "4" * 64
        script = "\n".join(
            [
                harness,
                f"removable='{removable}'",
                f"unnamed='{unnamed}'",
                f"unavailable='{unavailable}'",
                f"present='{present}'",
                "docker() {",
                '  if [[ "$1 $2" == "inspect --format" ]]; then',
                '    case "$3:$4" in',
                "      '{{.State.Running}}:cpa-wb-failover-build') printf 'false\\n' ;;",
                "      '{{.State.Running}}:cpa-wb-failover-build-v2') printf 'false\\n' ;;",
                "      '{{.State.Running}}:cpa-wb-failover-build-v3') printf 'false\\n' ;;",
                "      '{{.Image}}:cpa-wb-failover-build') printf '%s\\n' \"$unavailable\" ;;",
                "      '{{.Image}}:cpa-wb-failover-build-v3') printf '%s\\n' \"$present\" ;;",
                "      '{{.Id}}:cpa-wb-failover-build') printf '%s\\n' \"$removable\" ;;",
                "      '{{.Name}}:'*) printf '/cpa-wb-failover-build-v2\\n' ;;",
                "      '{{.Image}}:cpa-wb-failover-build-v2') printf 'sha256:%s\\n' \"$unnamed\" ;;",
                "      *) return 1 ;;",
                "    esac",
                "    return 0",
                '  elif [[ "$1 $2" == "ps --all" ]]; then',
                '    [[ "$3 $4" == "--quiet --no-trunc" && "$6" == "name=^/cpa-wb-failover-build$" ]] || return 1',
                '    printf "%s\\n" "$removable"',
                '  elif [[ "$1 $2 $3" == "ps --all --quiet" ]]; then',
                '    [[ "$4 $5" == "--no-trunc --filter" ]] || return 1',
                '    if [[ "$6" == "name=^/cpa-wb-failover-build-v2$" ]]; then',
                '      printf "%s\\n" "$unnamed"',
                "    fi",
                '  elif [[ "$1 $2" == "image inspect" ]]; then',
                '    if [[ "$4" == "$unavailable" ]]; then return 1; fi',
                '    if [[ "$4" == "$present" || "$4" == "sha256:$unnamed" ]]; then return 0; fi',
                "    return 1",
                '  elif [[ "$1" == rm ]]; then',
                '    [[ "$2" == "$removable" ]] || return 1',
                '    printf "removed=%s\\n" "$2" >> "$LOG"',
                "  else return 1; fi",
                "}",
                extract_function("prune_orphaned_containers"),
                "prune_orphaned_containers",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "cleanup.log"
            self.assertFalse(log.exists())
            result = subprocess.run(
                self._bash_command(bash, "-c", script, "harness", log.as_posix()),
                capture_output=True,
                timeout=30,
                check=False,
            )
            output = result.stdout.decode() + result.stderr.decode()
            self.assertEqual(result.returncode, 0, output)
            self.assertEqual(log.read_text().count("removed="), 1)
            self.assertIn("removed=" + removable, log.read_text())
            self.assertIn(
                "skipped=identity_unreadable name=cpa-wb-failover-build-v2", output
            )
            self.assertEqual(
                output.count("skipped=identity_unreadable"),
                2,
                output,
            )
            self.assertIn("scope=orphan_container removed=1", output)

    def test_maintenance_is_apply_and_exact_identity_scoped(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'step "verified orphan container hygiene"',
            source,
        )
        escaped_section = source.split("prune_verified_orphan_containers() {", 1)[
            1
        ].split("\nif [ \"`$apply\" = '1' ]; then", 1)[0]
        self.assertIn("skipped=image_present", escaped_section)
        self.assertIn("skipped=identity_unreadable", escaped_section)
        self.assertIn("skipped=name_mismatch", escaped_section)
        self.assertIn('docker rm "`$id"', escaped_section)
        for broad_prune in (
            "docker volume prune",
            "docker system prune",
            "docker image prune",
        ):
            self.assertNotIn(broad_prune, source)


if __name__ == "__main__":
    unittest.main()

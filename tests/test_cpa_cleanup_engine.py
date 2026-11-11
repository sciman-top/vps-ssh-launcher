"""Checks for narrow CPA and monthly local garbage retention policies."""

from __future__ import annotations

import hashlib
import os
import re
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

    def test_build_cache_busy_is_not_logged_as_successful_prune(self) -> None:
        function = extract_function("prune_build_cache")
        busy = function.index(
            "if [[ \"$output\" == *'cache is being used by another process'* ||"
        )
        pruned = function.index("result=pruned")
        self.assertLess(busy, pruned)
        self.assertIn("result=prune_busy", function[busy:pruned])

    def test_manual_family_policy_cannot_claim_updater_backups(self) -> None:
        function = extract_function("prune_manual_backup_families")
        self.assertEqual(function.count("! -name '*-from-v[0-9]*'"), 2)

    def test_manual_family_retention_tightens_only_under_disk_pressure(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        policy = extract_function("retention_keep_count")
        for free_kib, expected in ((9 * 1024 * 1024, 8), (7 * 1024 * 1024, 4)):
            with self.subTest(free_kib=free_kib):
                script = "\n".join(
                    [
                        "set -euo pipefail",
                        "BACKUP_ROOT=/not-read-by-probe",
                        "RETENTION_KEEP_BACKUPS=8",
                        "RETENTION_PRESSURE_SECONDARY_KEEP=4",
                        f"RETENTION_PRESSURE_FREE_KIB={8 * 1024 * 1024}",
                        f"df() {{ printf 'header\\nroot 1 1 {free_kib} 1 /\\n'; }}",
                        policy,
                        "retention_keep_count",
                    ]
                )
                result = subprocess.run(
                    self._bash_command(bash),
                    input=script.encode(),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout.decode() + result.stderr.decode(),
                )
                self.assertEqual(result.stdout.decode().strip(), str(expected))

        function = extract_function("prune_manual_backup_families")
        self.assertIn("keep_count=$(retention_keep_count)", function)
        self.assertIn('[ "$total" -gt "$keep_count" ]', function)
        self.assertIn('-v keep="$keep_count"', function)
        self.assertIn("RETENTION_MIN_AGE_HOURS", function)

    def test_build_source_removal_requires_verified_archives_older_than_one_week(
        self,
    ) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        function = extract_function("prune_verified_build_source_archives")
        now = 1_797_000_000
        old = 1_500_000_000

        def write_checksum(path: Path, target: Path) -> None:
            path.write_text(
                hashlib.sha256(target.read_bytes()).hexdigest()
                + "  "
                + target.name
                + "\n",
                encoding="utf-8",
                newline="\n",
            )

        def create_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
            source = root / "cpa-v8061-gemini-raw-build"
            cache = root / "wb-0987-go-cache"
            archive = root / "source-archives"
            for path in (source, cache, archive):
                path.mkdir()
            (source / "main.go").write_text("package main\n", encoding="utf-8")
            source_tar = subprocess.run(
                ["tar", "-C", root, "-cf", "-", source.name],
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(source_tar.returncode, 0, source_tar.stderr.decode())
            source_archive = archive / f"cpa-source.{old}.tar"
            source_archive.write_bytes(source_tar.stdout)
            write_checksum(archive / "cpa-source.sha256", source_archive)
            (archive / "cpa-source.created").write_text(
                f"created_at={old}\n",
                encoding="utf-8",
                newline="\n",
            )
            os.utime(source_archive, (old, old))
            os.utime(source, (old, old))
            os.utime(cache, (old, old))
            return source, cache, archive, source_archive

        def add_toolchain(archive: Path) -> Path:
            target = archive / f"cpa-go-toolchain.{old}.oci"
            target.write_bytes(b"toolchain\n")
            write_checksum(archive / "cpa-go-toolchain.sha256", target)
            (archive / "cpa-go-toolchain.created").write_text(
                f"created_at={old}\n",
                encoding="utf-8",
                newline="\n",
            )
            os.utime(target, (old, old))
            return target

        def run(
            root: Path,
            source: Path,
            cache: Path,
            archive: Path,
            *,
            save: str = 'printf "toolchain\\n" > "$4"',
            remove: str = "return 0",
            advance_clock: bool = False,
        ) -> str:
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    f"DIR={self._bash_path(bash, root)}",
                    f"SOURCE_TREE={self._bash_path(bash, source)}",
                    f"GO_CACHE={self._bash_path(bash, cache)}",
                    f"SOURCE_ARCHIVE_ROOT={self._bash_path(bash, archive)}",
                    "SOURCE_ARCHIVE_ALLOW_NONPRODUCTION=1",
                    "RETENTION_SOURCE_ARCHIVE_MIN_AGE_HOURS=168",
                    f"LOG={self._bash_path(bash, root / 'cleanup.log')}",
                    "REAL_STAT=$(command -v stat)",
                    "stat() {",
                    '  if [[ "$1" == "-c" && "$2" == "%a" ]]; then',
                    "    printf '700\\n'",
                    "  else",
                    '    "$REAL_STAT" "$@"',
                    "  fi",
                    "}",
                    'log() { printf "LOG %s\\n" "$*"; }',
                    "date() {",
                    '  if [[ "$1" == -u && "$2" == +%s ]]; then',
                    f"    printf '{now + (200000 if advance_clock else 0)}\\n'",
                    "  else",
                    '    command date "$@"',
                    "  fi",
                    "}",
                    "docker() {",
                    '  if [[ "$1 $2" == "image inspect" && "$4" == "{{.Id}}" ]]; then',
                    '    printf "sha256:%064d\\n" 0 | tr 0 a',
                    '  elif [[ "$1 $2" == "image save" ]]; then',
                    f"    {save}",
                    '  elif [[ "$1 $2" == "image rm" ]]; then',
                    f"    {remove}",
                    "  else return 1; fi",
                    "}",
                    function,
                    "prune_verified_build_source_archives",
                ]
            )
            result = subprocess.run(
                [*self._bash_command(bash), "-s"],
                input=harness.encode(),
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                result.stderr.decode() + result.stdout.decode(),
            )
            return result.stdout.decode() + result.stderr.decode()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cache, archive, _ = create_fixture(root)
            first = run(root, source, cache, archive)
            self.assertIn("result=toolchain_snapshot_created", first)
            self.assertNotIn("result=toolchain_image_archive_retained", first)
            self.assertNotIn("result=source_removed", first)
            self.assertTrue(source.is_dir(), first)
            self.assertTrue(cache.is_dir(), first)
            created = archive / "cpa-go-toolchain.created"
            self.assertEqual(created.read_text(encoding="utf-8"), f"created_at={now}\n")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cache, archive, _ = create_fixture(root)
            add_toolchain(archive)
            (archive / "cpa-go-toolchain.created").write_text(
                f"created_at={now}\n", encoding="utf-8", newline="\n"
            )
            # A recorded hold remains a lower bound when the system clock moves
            # backwards, so the rebuilt inputs stay protected.
            held = run(root, source, cache, archive)
            self.assertIn("retained=recent_rebuild_window", held)
            self.assertTrue(source.is_dir(), held)
            self.assertTrue(cache.is_dir(), held)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cache, archive, _ = create_fixture(root)
            add_toolchain(archive)
            removed = run(root, source, cache, archive, advance_clock=True)
            self.assertIn("result=source_removed", removed)
            self.assertIn("result=toolchain_image_archive_retained", removed)
            self.assertFalse(source.exists())
            self.assertFalse(cache.exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cache, archive, _ = create_fixture(root)
            add_toolchain(archive)
            (archive / "cpa-source.created").unlink()
            missing_age = run(
                root,
                source,
                cache,
                archive,
                advance_clock=True,
            )
            self.assertIn(
                "retained=recent_rebuild_window",
                missing_age,
            )
            self.assertTrue(source.is_dir(), missing_age)
            self.assertTrue(cache.is_dir(), missing_age)

        for damage in ("missing", "empty", "digest", "symlink"):
            with (
                self.subTest(damage=damage),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                source, cache, archive, source_archive = create_fixture(root)
                toolchain = add_toolchain(archive)
                if damage == "missing":
                    toolchain.unlink()
                elif damage == "empty":
                    toolchain.write_bytes(b"")
                    write_checksum(archive / "cpa-go-toolchain.sha256", toolchain)
                elif damage == "digest":
                    toolchain.write_bytes(b"changed after checksum\n")
                else:
                    toolchain.unlink()
                    try:
                        toolchain.symlink_to(source_archive)
                    except OSError:
                        self.skipTest("symlink permission unavailable")
                    write_checksum(archive / "cpa-go-toolchain.sha256", toolchain)
                before = sorted(path.name for path in archive.iterdir())
                output = run(
                    root,
                    source,
                    cache,
                    archive,
                    save="return 1",
                    remove='printf "FORBIDDEN_REMOVE\\n"',
                    advance_clock=True,
                )
                self.assertIn("scope=source_archive", output)
                self.assertNotIn("FORBIDDEN_REMOVE", output)
                self.assertTrue(source.is_dir(), output)
                self.assertTrue(cache.is_dir(), output)
                self.assertEqual(before, [path.name for path in archive.iterdir()])

    def test_build_source_archive_has_no_broad_deletion_scope(self) -> None:
        function = extract_function("prune_verified_build_source_archives")
        self.assertIn(
            'local source_root="${SOURCE_ARCHIVE_ROOT:-$DIR/source-archives}"',
            function,
        )
        self.assertIn('"$source_root" != /opt/cliproxyapi/source-archives', function)
        self.assertIn(
            'local source_tree="${SOURCE_TREE:-/root/cpa-v8061-gemini-raw-build}"',
            function,
        )
        self.assertIn('"$source_tree" != /root/cpa-v8061-gemini-raw-build', function)
        self.assertIn('local go_cache="${GO_CACHE:-/root/wb-0987-go-cache}"', function)
        self.assertIn('"$go_cache" != /root/wb-0987-go-cache', function)
        self.assertIn(
            'local allow_nonproduction="${SOURCE_ARCHIVE_ALLOW_NONPRODUCTION:-0}"',
            function,
        )
        self.assertIn("sha256sum --check --strict --status", function)
        self.assertIn("docker image save", function)
        self.assertIn('docker image rm "$go_id"', function)
        self.assertNotIn("docker image prune", function)
        self.assertNotIn("docker builder prune", function)
        self.assertNotIn("docker system prune", function)
        self.assertNotIn("docker volume prune", function)
        self.assertEqual(function.count("rm -rf --"), 1)
        self.assertIsNone(re.search(r"\brm\s+-rf\s+(?:/|\"|'|\$)", function))

    def test_backup_deletion_rejects_a_path_outside_the_real_root(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        function = extract_function("prune_backups")
        root_guard = 'backup_root_real=$(realpath -e -- "$BACKUP_ROOT" 2>/dev/null)'
        leaf_guard = "leaf=${entry##*/}"
        parent_guard = 'parent=$(realpath -e -- "$canonical_parent" 2>/dev/null)'
        leaf_target = '"$(realpath -e -- "$canonical_parent/$leaf" 2>/dev/null)"'
        self.assertIn(root_guard, function)
        self.assertIn(leaf_guard, function)
        self.assertIn(parent_guard, function)
        self.assertIn(leaf_target, function)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "backups"
            root.mkdir()
            os.utime(root, (1_600_000_000,) * 2)
            for day in range(1, 10):
                path = root / f"202609{day:02d}T000000Z-from-v0.0.{day}"
                path.mkdir()
                if day == 1:
                    (path / "file.txt").write_text("keep", encoding="utf-8")
                (path / "compose.yml").write_text("backup", encoding="utf-8")
                os.utime(path, (1_700_000_000 + day,) * 2)
            escape = root / "20990101T000000Z-from-v9.9.9"
            escape.mkdir()
            os.utime(escape, (1_700_000_100,) * 2)
            other = root / "20990102T000000Z-from-v9.9.9"
            other.mkdir()
            os.utime(other, (1_700_000_050,) * 2)
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    f"BACKUP_ROOT='{self._bash_path(bash, root)}'",
                    "RETENTION_KEEP_BACKUPS=8",
                    "log() { printf '+%s\\n' \"$*\"; }",
                    "realpath() {",
                    '  if [[ "$*" == *20990101* ]]; then',
                    '    printf "/tmp/outside\\n"',
                    '  elif [[ "$*" == *20990102* ]]; then',
                    '    printf "/tmp/also_outside\\n"',
                    "  else",
                    '    printf "/mnt/backups\\n"',
                    "  fi",
                    "}",
                    "cp(){ printf 'OUTSIDE\\n'; log OUTSIDE; }",
                    extract_function("prune_backups"),
                    "prune_backups",
                ]
            )
            result = subprocess.run(
                self._bash_command(bash),
                input=harness.encode(),
                capture_output=True,
                timeout=30,
                check=False,
            )
            output = result.stdout.decode()
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue(escape.is_dir(), output)
            self.assertTrue(other.is_dir(), output)
            self.assertIn("stage=path_escape", output)
            self.assertNotIn("OUTSIDE", output)

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

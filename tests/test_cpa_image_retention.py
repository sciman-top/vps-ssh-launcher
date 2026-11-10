"""Read-only image-retention candidates for the CPA daily updater."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

UPDATER = Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"


def function(name: str) -> str:
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


class ImageRetentionCandidateTests(ScriptValidationMixin, unittest.TestCase):
    def test_newest_rollback_name_without_trailing_newline_is_protected(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        current = "sha256:" + "e" * 64
        rollback = "sha256:" + "a" * 64
        removable = "sha256:" + "c" * 64
        script = "\n".join(
            [
                "set -euo pipefail",
                'log() { printf "%s\\n" "$*"; }',
                function("image_id_file"),
                function("backup_image_id"),
                function("resolve_backup_image_id"),
                function("retained_backup_paths"),
                function("image_candidates"),
                function("prune_images"),
                f"current='{current}'",
                f"rollback='{rollback}'",
                f"removable='{removable}'",
                "docker() {",
                '  if [[ "$1 $2" == "inspect --format" ]]; then',
                '    [[ "$3" == \'{{.Image}}\' && "$4" == cli-proxy-api ]] || return 1',
                '    printf "%s\\n" "$current"',
                '  elif [[ "$1 $2" == "ps --all" ]]; then',
                "    printf 'container-id\\n'",
                '  elif [[ "$1 $2" == "ps --format" ]]; then',
                "    printf 'container-id\\n'",
                "    return 0",
                '  elif [[ "$1 $2" == "image ls" ]]; then',
                '    [[ "$7" == eceasy/cli-proxy-api || "$7" == cli-proxy-api-fork ]] || return 1',
                '    if [[ "$7" == eceasy/cli-proxy-api ]]; then',
                '      printf "%s|eceasy/cli-proxy-api:<none>\\n" "$current"',
                '      printf "%s|eceasy/cli-proxy-api:<none>\\n" "$rollback"',
                '      printf "%s|eceasy/cli-proxy-api:<none>\\n" "$removable"',
                "    fi",
                '  elif [[ "$1 $2" == "image inspect" ]]; then',
                '    case "$4" in',
                '      "{{.Repository}}")',
                '        [[ "$5" == "$current" || "$5" == "$rollback" || "$5" == "$removable" ]] || return 1',
                "        printf 'eceasy/cli-proxy-api\\n' ;;",
                '      "{{.Id}}") printf "%s\\n" "$5" ;;',
                '      "{{.RootFS.Layers}}") printf "[%064d]\\n" 0 | tr "0" "f" ;;',
                '      "{{.Created.Unix}}") printf "1\\n" ;;',
                '      "{{.Size}}") printf "1\\n" ;;',
                "      *) return 1 ;;",
                "    esac",
                '  elif [[ "$1 $2" == "system df" ]]; then',
                "    printf 'Images 1GB\\n'",
                '  elif [[ "$1" == rmi ]]; then',
                '    [[ "$2" == "$removable" ]]',
                "  else return 1; fi",
                "}",
                "prune_images",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / ("backup-" + "b" * 40)
            backup.mkdir()
            (backup / "image-id").write_text(rollback + "\n", encoding="utf-8")
            (backup / "compose.yml").write_text(
                "services:\n  cli-proxy-api:\n    image: mutable-tag\n",
                encoding="utf-8",
            )
            script = script.replace(
                "docker() {",
                f"BACKUP_ROOT='{self._bash_path(bash, root)}'\n"
                "RETENTION_KEEP_BACKUPS=8\n"
                "CPA_IMAGE_REPO=eceasy/cli-proxy-api\n"
                "FORK_IMAGE_REPO=cli-proxy-api-fork\n"
                "LOG=/dev/null\n"
                "docker() {",
                1,
            )
            result = subprocess.run(
                self._bash_command(bash),
                input=script.encode(),
                capture_output=True,
                timeout=30,
                check=False,
            )
            output = result.stdout.decode() + result.stderr.decode()
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn("stage=backup_identity_unreadable", output)
            self.assertIn(rollback, output)

    def test_retained_paths_use_real_newest_directory_set(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        script = "\n".join(
            [
                "set -euo pipefail",
                function("retained_backup_paths"),
                'BACKUP_ROOT="$1"',
                "RETENTION_KEEP_BACKUPS=8",
                'retained_backup_paths ""',
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"backup-{index}" for index in range(10)]
            for index, path in enumerate(paths):
                path.mkdir()
                os.utime(path, (1_700_000_000 + index,) * 2)
            result = subprocess.run(
                self._bash_command(bash),
                input=script.replace(
                    'BACKUP_ROOT="$1"',
                    f"BACKUP_ROOT='{self._bash_path(bash, root)}'",
                ).encode(),
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            actual = [line for line in result.stdout.decode().split("\0") if line]
            expected = [self._bash_path(bash, path) for path in paths[2:]][::-1]
            self.assertEqual(actual, expected)

    def test_retained_paths_are_a_fail_closed_directory_inventory(self) -> None:
        section = function("retained_backup_paths")
        self.assertIn("-type d -print0", section)
        self.assertIn("stat -c %Y", section)
        self.assertIn("sort -zrn", section)
        self.assertIn('head -zn "$RETENTION_KEEP_BACKUPS"', section)
        self.assertNotIn('stat -c %Y -- "$entry" 2>/dev/null || true', section)

    def test_image_inventory_rejects_unverified_targets_before_rmi(self) -> None:
        section = function("image_candidates")
        candidate = function("prune_images")
        self.assertIn("--all --no-trunc", section)
        self.assertIn('"$repo"', section)
        self.assertIn("{{.Repository}}", section)
        self.assertIn("{{.RootFS.Layers}}", section)
        self.assertIn("{{.Created.Unix}}", section)
        self.assertIn("^sha256:[0-9a-f]{64}$", section)
        self.assertIn('docker rmi "$id"', candidate)
        self.assertIn("running_image", candidate)
        self.assertNotIn('docker rmi "$repo', candidate)
        self.assertNotIn('docker rmi "$image_ref', candidate)

    def test_only_complete_verified_official_targets_become_candidates(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        current = "sha256:" + "b" * 64
        unreferenced = "sha256:" + "c" * 64
        script = "\n".join(
            [
                "set -euo pipefail",
                'log() { printf "%s\\n" "$*"; }',
                function("image_candidates"),
                f"current='{current}'",
                f"unreferenced='{unreferenced}'",
                "docker() {",
                '  if [[ "$1 $2" == "image ls" ]]; then',
                '    [[ "$3" == --all && "$4" == --no-trunc && "$5" == --format ]] || return 1',
                '    [[ "$7" == eceasy/cli-proxy-api ]] || return 1',
                '    printf "%s|eceasy/cli-proxy-api:<none>\\n" "$current"',
                '    printf "%s|eceasy/cli-proxy-api:<none>\\n" "$unreferenced"',
                '  elif [[ "$1 $2" == "image inspect" ]]; then',
                '    [[ "$3" == --format ]] || return 1',
                '    [[ "$5" == "$current" || "$5" == "$unreferenced" ]] || return 1',
                '    case "$4" in',
                '      "{{.Repository}}") repo=eceasy/cli-proxy-api ;;',
                '      "{{.RootFS.Layers}}") repo="[$(printf "%064d" 0)]" ;;',
                '      "{{.Created.Unix}}") repo=1 ;;',
                "      *) return 1 ;;",
                "    esac",
                '    printf "%s\\n" "$repo"',
                "    return 0",
                "  else return 1; fi",
                "}",
                "image_candidates eceasy/cli-proxy-api",
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
        candidates = {
            line.split("\t", 1)[0] for line in result.stdout.decode().splitlines()
        }
        self.assertEqual(candidates, {current, unreferenced})

    def test_recorded_backup_id_is_used_before_mutable_compose_tag(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        recorded = "sha256:" + "a" * 64
        script = "\n".join(
            [
                "set -euo pipefail",
                function("image_id_file"),
                function("backup_image_id"),
                function("resolve_backup_image_id"),
                f"recorded='{recorded}'",
                'compose_image_ref() { printf "mutable-fork-tag\\n"; }',
                'docker() { [[ "$1 $2 $3" == "image inspect --format" && "$4" == "{{.Id}}" && "$5" == "$recorded" ]] || return 1; printf "%s\\n" "$5"; }',
                'resolve_backup_image_id "$1"',
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "image-id").write_text(recorded + "\n", encoding="utf-8")
            (root / "compose.yml").write_text(
                "services:\n  cli-proxy-api:\n    image: mutable-fork-tag\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                self._bash_command(
                    bash, "-c", script, "harness", self._bash_path(bash, root)
                ),
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout.decode().strip(), recorded)


if __name__ == "__main__":
    unittest.main()

"""Rollback sets must name the image they captured, not a mutable tag.

Background (2026-11-09): backup compose files record
"cli-proxy-api-fork:v8.0.61-gemini-raw" -- a mutable tag. Two retained
backups resolved to the CURRENT image while the pre-switch image the backup
was supposed to preserve was gone (8c6ab375 no longer on disk). prune_images()
therefore protected the wrong image, and "restore this backup" would have
restored different code than the backup captured.

The fix records the resolved image id inside each backup directory and makes
prune consult it before falling back to the (mutable) compose tag.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

UPDATER = Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"


def extract_function(name: str) -> str:
    """Return one `name() { ... }` body via a brace-depth walk.

    A naive split on the first closing brace breaks on any nested function or
    heredoc. image_id_file() is one line and compose_image_ref() contains a
    heredoc, so getting the boundary wrong hands bash a truncated script and
    every symbol reports "command not found".
    """
    source = UPDATER.read_text(encoding="utf-8")
    start = source.index(name + "() {")
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("unterminated function: " + name)


class RollbackImageIdentityTests(ScriptValidationMixin, unittest.TestCase):
    def test_backup_records_the_resolved_image_id(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        script = "\n".join(
            [
                "set -eu",
                "docker() { echo sha256:deadbeefcafe; }",
                extract_function("compose_image_ref"),
                extract_function("image_id_file"),
                'id=$(docker image inspect --format x "$(compose_image_ref '
                '"$1/compose.yml")")',
                'printf "%s\\n" "$id" > "$(image_id_file "$1")"',
                'cat "$(image_id_file "$1")"',
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "compose.yml").write_text(
                "services:\n  cli-proxy-api:\n"
                "    image: cli-proxy-api-fork:v8.0.61-gemini-raw\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                self._bash_command(
                    bash, "-c", script, "harness", self._bash_path(bash, root)
                ),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("sha256:deadbeefcafe", result.stdout)
            self.assertTrue((root / "image-id").exists())

    def test_prune_prefers_the_recorded_id_over_the_tag(self) -> None:
        section = extract_function("prune_images")
        self.assertIn("image_id_file", section)
        # The recorded id is consulted before any tag resolution.
        self.assertLess(
            section.index("image_id_file"),
            section.index("compose_image_ref"),
        )
        self.assertIn("PRUNE_WARN scope=images stage=recorded_id_missing", section)

    def test_updater_stamps_the_id_before_mutating(self) -> None:
        source = UPDATER.read_text(encoding="utf-8")
        self.assertIn("ROLLBACK_IMAGE_ID=$(record_image_ids", source)
        self.assertIn("BACKUP_IMAGE_ID=", source)
        # Stamped while building the backup, i.e. before MUTATED=1.
        self.assertLess(
            source.index("ROLLBACK_IMAGE_ID=$(record_image_ids"),
            source.index("MUTATED=1"),
        )


if __name__ == "__main__":
    unittest.main()

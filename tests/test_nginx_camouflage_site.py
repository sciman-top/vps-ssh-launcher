"""Contract and behavior tests for scripts/nginx_camouflage_site.ps1.

The decoy-site promotion is a real remote write, so the tests pin its safety
shape: read-only by default, backup before any change, an nginx config test,
and a verified rollback. A behavioral harness runs the embedded bash payload
against a temporary document root so the copy/rollback paths are exercised
without a real host.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nginx_camouflage_site.ps1"


def _payload(text: str) -> str:
    return (
        text.split("$remoteCommand = @'\n", 1)[1]
        .split("\n'@", 1)[0]
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


class NginxCamouflageScriptTests(ScriptValidationMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_apply_is_opt_in_and_backup_first(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn("__APPLY__", text)
        self.assertIn("if [ \"$APPLY\" != '1' ]; then", text)
        self.assertIn('cp -a "$NGINX_ROOT/." "$backup_dir/html/"', text)
        self.assertIn("backup_ready=1", text)
        # The backup is taken before the decoy is copied over the root.
        self.assertLess(
            text.index('cp -a "$NGINX_ROOT/." "$backup_dir/html/"'),
            text.index('cp -a "$DECOY_DIR/." "$NGINX_ROOT/"'),
        )

    def test_apply_tests_nginx_and_rolls_back_on_failure(self) -> None:
        text = self.text
        self.assertIn("NGINX_TEST_FAILED", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("ROLLBACK_FAILED", text)
        self.assertIn("CAMOUFLAGE_INDEX_MISMATCH", text)
        # Writes are serialized with the fleet-wide maintenance lock.
        self.assertIn("LOCK_FILE=", text)
        self.assertIn("flock -n 9", text)
        # No proxy-core, TLS, or subscription mutation is in scope.
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("xray.service", text)
        self.assertNotIn("sing-box", text)

    def _bash(self) -> str:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        return bash

    def _require_flock(self, bash: str) -> None:
        # Git Bash on Windows lacks flock; the apply path needs it on Linux.
        probe = subprocess.run(
            self._bash_command(bash, "-c", "command -v flock"),
            capture_output=True,
            timeout=10,
            check=False,
        )
        if probe.returncode != 0:
            self.skipTest("flock is not available in this Bash")

    def _run(
        self, bash: str, payload: str, env: dict[str, str]
    ) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            self._bash_command(bash, "-c", payload),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, **env},
        )

    def _paths(self, bash: str, tmp: str, root: Path) -> dict[str, str]:
        return {
            "VPS_CAMOUFLAGE_NGINX_ROOT": self._bash_path(bash, Path(root)),
            "VPS_CAMOUFLAGE_STATUS_DIR": self._bash_path(bash, Path(tmp) / "status"),
            "VPS_CAMOUFLAGE_BACKUP_DIR": self._bash_path(bash, Path(tmp)),
            "VPS_CAMOUFLAGE_LOCK_FILE": self._bash_path(bash, Path(tmp) / "lock"),
        }

    def _fixture(self, root: Path) -> None:
        (root / "en").mkdir(parents=True)
        (root / "en" / "index.html").write_text("<html>decoy</html>", encoding="utf-8")
        (root / "index.html").write_text(
            "<html>Welcome to nginx! If you see this page</html>", encoding="utf-8"
        )

    def test_read_only_leaves_root_untouched(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "0")
        bash = self._bash()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            before = (root / "index.html").read_text(encoding="utf-8")
            result = self._run(bash, payload, self._paths(bash, tmp, root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("camouflage=stock-default", result.stdout)
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), before)

    def test_apply_promotes_decoy(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = "true"
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CAMOUFLAGE_APPLIED", result.stdout)
            self.assertEqual(
                (root / "index.html").read_text(encoding="utf-8"),
                "<html>decoy</html>",
            )

    def test_apply_rolls_back_when_nginx_test_fails(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            original = (root / "index.html").read_text(encoding="utf-8")
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = "false"
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertEqual(
                (root / "index.html").read_text(encoding="utf-8"), original
            )
            self.assertIn(
                "result=failed",
                (Path(tmp) / "status" / "nginx-camouflage.status").read_text(
                    encoding="utf-8"
                ),
            )

    def test_apply_keeps_unrelated_root_file_after_rollback(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            (root / "other.html").write_text("keep\n", encoding="utf-8")
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = "false"
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertEqual(
                (root / "other.html").read_text(encoding="utf-8"), "keep\n"
            )

    def test_apply_accepts_missing_original_root_index(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            (root / "en").mkdir(parents=True)
            (root / "en" / "index.html").write_text(
                "<html>decoy</html>", encoding="utf-8"
            )
            env = self._paths(bash, tmp, root)
            # Force the post-copy verification to fail so the test exercises
            # rollback; the original root index is intentionally absent.
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = "false"
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertFalse((root / "index.html").exists())


if __name__ == "__main__":
    unittest.main()

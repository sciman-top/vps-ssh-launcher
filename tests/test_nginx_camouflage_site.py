"""Contract and behavior tests for scripts/nginx_camouflage_site.ps1.

The decoy-site promotion is a real remote write, so the tests pin its safety
shape: read-only by default, backup before any change, an nginx config test,
and a verified rollback. A behavioral harness runs the embedded bash payload
against a temporary document root so the copy/rollback paths are exercised
without a real host.
"""

from __future__ import annotations

import os
import shlex
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
            "VPS_CAMOUFLAGE_DPKG_CONF": self._bash_path(
                bash, Path(tmp) / "dpkg" / "vps-launcher-camouflage"
            ),
        }

    def _fixture(self, root: Path) -> None:
        (root / "en").mkdir(parents=True)
        (root / "en" / "index.html").write_text("<html>decoy</html>", encoding="utf-8")
        (root / "index.html").write_text(
            "<html>Welcome to nginx! If you see this page</html>", encoding="utf-8"
        )

    def _rollback_test_command(self, bash: str, root: Path, tmp: str) -> str:
        """Fail against the promoted decoy, then pass after restoration.

        Using a state-sensitive fixture keeps rollback verification honest: the
        production payload must observe a failing config test after Apply and
        a passing one after it restores the original bytes.  A constant
        ``false`` command would also fail during rollback and would tempt the
        implementation to add a test-only bypass.
        """
        command = Path(tmp) / "nginx-test.sh"
        decoy = shlex.quote(self._bash_path(bash, root / "en" / "index.html"))
        root_index = shlex.quote(self._bash_path(bash, root / "index.html"))
        command.write_text(
            "#!/usr/bin/env bash\n"
            f"if cmp -s {decoy} {root_index}; then\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        os.chmod(command, 0o755)
        return self._bash_path(bash, command)

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

    def test_apply_removes_stock_error_page(self) -> None:
        # 50x.html answers 200 on every vhost serving the root; the apply
        # must quarantine it alongside the decoy promotion.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            (root / "50x.html").write_text("<html>error</html>", encoding="utf-8")
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = "true"
            env["VPS_CAMOUFLAGE_NGINX_CONF_DIR"] = self._bash_path(
                bash, Path(tmp) / "nginx-conf"
            )
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CAMOUFLAGE_APPLIED", result.stdout)
            self.assertIn("STOCK_FILE_REMOVED file=50x.html", result.stdout)
            self.assertFalse((root / "50x.html").exists())

    def test_apply_writes_branded_404_and_error_page_dropin(self) -> None:
        # A stock "<center>nginx</center>" 404 is an active-probe tell. Apply must
        # write a site-consistent 404.html and an http-level error_page drop-in
        # so every vhost serving the root answers unknown paths with it.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            conf_dir = Path(tmp) / "nginx-conf"
            (conf_dir / "conf.d").mkdir(parents=True)
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = "true"
            env["VPS_CAMOUFLAGE_NGINX_CONF_DIR"] = self._bash_path(bash, conf_dir)
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CAMOUFLAGE_APPLIED", result.stdout)
            not_found = (root / "404.html").read_text(encoding="utf-8")
            self.assertIn("Page Not Found", not_found)
            self.assertNotIn("center", not_found.lower())
            dropin = conf_dir / "conf.d" / "vps-launcher-camouflage-errors.conf"
            text = dropin.read_text(encoding="utf-8")
            # http scope rejects `location`/`internal`, so the drop-in must only
            # carry the error_page directive and preserve the 404 status.
            self.assertIn("error_page 404 =404 /404.html;", text)
            self.assertNotIn("location ", text)

    def test_apply_rolls_back_new_error_page_dropin(self) -> None:
        # The drop-in lives outside the document root, so rollback must remove
        # a drop-in this run created even though the root backup does not
        # cover it.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            conf_dir = Path(tmp) / "nginx-conf"
            (conf_dir / "conf.d").mkdir(parents=True)
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = self._rollback_test_command(
                bash, root, tmp
            )
            env["VPS_CAMOUFLAGE_NGINX_CONF_DIR"] = self._bash_path(bash, conf_dir)
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertFalse(
                (conf_dir / "conf.d" / "vps-launcher-camouflage-errors.conf").exists()
            )

    def test_apply_restores_existing_error_page_dropin_on_rollback(self) -> None:
        # If a previous run left a drop-in, a failed apply must restore those
        # exact bytes rather than deleting the file.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            conf_dir = Path(tmp) / "nginx-conf"
            (conf_dir / "conf.d").mkdir(parents=True)
            prior = "# prior content\n"
            (conf_dir / "conf.d" / "vps-launcher-camouflage-errors.conf").write_text(
                prior, encoding="utf-8"
            )
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = self._rollback_test_command(
                bash, root, tmp
            )
            env["VPS_CAMOUFLAGE_NGINX_CONF_DIR"] = self._bash_path(bash, conf_dir)
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertEqual(
                (conf_dir / "conf.d" / "vps-launcher-camouflage-errors.conf").read_text(
                    encoding="utf-8"
                ),
                prior,
            )

    def test_read_only_reports_404_state(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "0")
        bash = self._bash()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_CONF_DIR"] = self._bash_path(
                bash, Path(tmp) / "nginx-conf"
            )
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("notfound_page=missing", result.stdout)
            self.assertIn("errorpage_wired=no", result.stdout)

    def test_apply_keeps_stock_error_page_when_referenced(self) -> None:
        # An error_page alias in the nginx config makes 50x.html load-bearing:
        # removing it would turn 5xx bodies into 404s, so the lane must keep
        # the file and say so.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            (root / "50x.html").write_text("<html>error</html>", encoding="utf-8")
            conf_dir = Path(tmp) / "nginx-conf"
            conf_dir.mkdir()
            (conf_dir / "site.conf").write_text(
                "error_page 500 502 503 504 /50x.html;\n", encoding="utf-8"
            )
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = "true"
            env["VPS_CAMOUFLAGE_NGINX_CONF_DIR"] = self._bash_path(bash, conf_dir)
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("STOCK_FILE_KEEP_REFERENCED file=50x.html", result.stdout)
            self.assertTrue((root / "50x.html").exists())

    def test_apply_rolls_back_stock_error_page_removal(self) -> None:
        # The full-root backup is taken before the quarantine, so a failed
        # apply restores the stock file with the rest of the original bytes.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            (root / "50x.html").write_text("<html>error</html>", encoding="utf-8")
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = self._rollback_test_command(
                bash, root, tmp
            )
            env["VPS_CAMOUFLAGE_NGINX_CONF_DIR"] = self._bash_path(
                bash, Path(tmp) / "nginx-conf"
            )
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertEqual(
                (root / "50x.html").read_text(encoding="utf-8"),
                "<html>error</html>",
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
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = self._rollback_test_command(
                bash, root, tmp
            )
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertEqual(
                (root / "index.html").read_text(encoding="utf-8"), original
            )
            self.assertFalse((root / ".copy-complete").exists())
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
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = self._rollback_test_command(
                bash, root, tmp
            )
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertEqual(
                (root / "other.html").read_text(encoding="utf-8"), "keep\n"
            )
            self.assertFalse((root / ".copy-complete").exists())

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
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = self._rollback_test_command(
                bash, root, tmp
            )
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertFalse((root / "index.html").exists())
            self.assertFalse((root / ".copy-complete").exists())

    def test_apply_projects_dpkg_exclude_conf(self) -> None:
        # nginx package upgrades re-unpack the stock pages and silently revert
        # the promotion (2026-11-01: monthly maintenance upgraded nginx
        # 1.31.x -> 1.31.8 on both hosts and the root flipped back to
        # "Welcome to nginx!"). Apply must project a dpkg path-exclude so
        # upgrades stop touching the lane-managed root.
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
            self.assertIn("DPKG_EXCLUDE_PROJECTED", result.stdout)
            conf = Path(tmp) / "dpkg" / "vps-launcher-camouflage"
            text = conf.read_text(encoding="utf-8")
            for line in (
                "path-exclude=/usr/share/nginx/html/index.html",
                "path-exclude=/usr/share/nginx/html/50x.html",
                "path-exclude=/usr/share/nginx/html/index.nginx-debian.html",
            ):
                self.assertIn(line, text)

    def test_apply_rolls_back_new_dpkg_exclude_conf(self) -> None:
        # The exclusion file lives outside the document root, so rollback must
        # remove a conf this run created even though the root backup does not
        # cover it.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = self._rollback_test_command(
                bash, root, tmp
            )
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertFalse((Path(tmp) / "dpkg" / "vps-launcher-camouflage").exists())

    def test_apply_restores_prior_dpkg_exclude_conf_on_rollback(self) -> None:
        # A previously projected exclusion must be restored byte-exact on a
        # failed apply, never silently rewritten or dropped.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            conf = Path(tmp) / "dpkg" / "vps-launcher-camouflage"
            conf.parent.mkdir(parents=True)
            prior = "# prior exclusion\npath-exclude=/usr/share/nginx/html/index.html\n"
            conf.write_text(prior, encoding="utf-8")
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = self._rollback_test_command(
                bash, root, tmp
            )
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertEqual(conf.read_text(encoding="utf-8"), prior)

    def test_read_only_reports_dpkg_exclude_state(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "0")
        bash = self._bash()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            env = self._paths(bash, tmp, root)
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("dpkg_exclude=missing", result.stdout)
            conf = Path(tmp) / "dpkg" / "vps-launcher-camouflage"
            conf.parent.mkdir(parents=True)
            conf.write_text(
                "path-exclude=/usr/share/nginx/html/index.html\n"
                "path-exclude=/usr/share/nginx/html/50x.html\n"
                "path-exclude=/usr/share/nginx/html/index.nginx-debian.html\n",
                encoding="utf-8",
            )
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("dpkg_exclude=present", result.stdout)
            # Read-only never writes the conf.
            self.assertEqual(conf.read_text(encoding="utf-8").count("path-exclude="), 3)

    def test_builder_check_marker_is_residue(self) -> None:
        # zz ships a 0-byte builder `check` marker in the root; the residue
        # documentation always claimed it but the find expression never
        # matched it. Both preview enumeration and apply quarantine must
        # cover it.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            (root / "check").write_text("", encoding="utf-8")
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = "true"
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CAMOUFLAGE_APPLIED", result.stdout)
            self.assertIn("RESIDUE_REMOVED file=check", result.stdout)
            self.assertFalse((root / "check").exists())

    def test_check_marker_not_protected_by_prose_mentions(self) -> None:
        # The reference filter greps served pages for the residue name, and
        # "check" is ordinary prose. A 0-byte builder marker is never served
        # content, so it is quarantined like the .psd/.DS_Store class even
        # when the decoy text contains the word.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            (root / "check").write_text("", encoding="utf-8")
            (root / "en" / "index.html").write_text(
                "<html>decoy</html><!-- check our catalogue -->",
                encoding="utf-8",
            )
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = "true"
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("RESIDUE_REMOVED file=check", result.stdout)
            self.assertNotIn("residue_kept_referenced= check", result.stdout)
            self.assertFalse((root / "check").exists())

    def test_embedded_payload_is_bash_syntax(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "0")
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        result = subprocess.run(
            self._bash_command(bash, "-n"),
            input=payload,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_read_only_reports_removable_residue(self) -> None:
        # Template-distribution debris (theme zips, PSD sources, __MACOSX,
        # builder Dockerfile/README) is a hand-assembled-decoy tell; the lane
        # must enumerate it as removable without touching the root in read-only
        # mode.
        payload = _payload(self.text).replace("__APPLY__", "0")
        bash = self._bash()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            (root / "html4.zip").write_text("PK", encoding="utf-8")
            (root / "README.md").write_text("# theme", encoding="utf-8")
            (root / "psd").mkdir()
            (root / "psd" / "index.psd").write_text("psd", encoding="utf-8")
            env = self._paths(bash, tmp, root)
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("residue_removable=", result.stdout)
            self.assertIn("html4.zip", result.stdout)
            self.assertIn("psd", result.stdout)
            # Read-only leaves everything in place.
            self.assertTrue((root / "html4.zip").exists())
            self.assertTrue((root / "psd" / "index.psd").exists())

    def test_apply_removes_residue_but_keeps_referenced_file(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            (root / "html4.zip").write_text("PK", encoding="utf-8")
            (root / "psd").mkdir()
            (root / "psd" / "index.psd").write_text("psd", encoding="utf-8")
            # A referenced asset must survive even though it looks like residue.
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            (root / "index.html").write_text(
                "<html>Welcome to nginx! <link rel=manifest href=manifest.json></html>",
                encoding="utf-8",
            )
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = "true"
            result = self._run(bash, payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CAMOUFLAGE_APPLIED", result.stdout)
            self.assertIn("RESIDUE_REMOVED file=html4.zip", result.stdout)
            self.assertFalse((root / "html4.zip").exists())
            self.assertFalse((root / "psd").exists())
            self.assertTrue((root / "manifest.json").exists())

    def test_apply_rolls_back_residue_removal(self) -> None:
        # A residue deletion must be undone by the full-root backup when the
        # subsequent nginx test fails.
        payload = _payload(self.text).replace("__APPLY__", "1")
        bash = self._bash()
        self._require_flock(bash)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "html"
            self._fixture(root)
            (root / "html4.zip").write_text("PK", encoding="utf-8")
            env = self._paths(bash, tmp, root)
            env["VPS_CAMOUFLAGE_NGINX_TEST"] = self._rollback_test_command(
                bash, root, tmp
            )
            env["VPS_CAMOUFLAGE_NGINX_CONF_DIR"] = self._bash_path(
                bash, Path(tmp) / "nginx-conf"
            )
            result = self._run(bash, payload, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ROLLBACK_VERIFIED", result.stdout)
            self.assertTrue((root / "html4.zip").exists())
            self.assertEqual((root / "html4.zip").read_text(encoding="utf-8"), "PK")


if __name__ == "__main__":
    unittest.main()

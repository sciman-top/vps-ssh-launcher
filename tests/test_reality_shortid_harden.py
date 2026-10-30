"""Contract and behavior tests for scripts/reality_shortid_harden.ps1.

The lane removes an empty REALITY shortId that vasma always regenerates. The
empty entry widens the active-probing surface and is never used by any vasma
subscription (they hard-code the non-empty id). Tests pin the safety shape and
exercise the strip/verify logic against temporary config trees.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reality_shortid_harden.ps1"


def _payload(text: str) -> str:
    return (
        text.split("$remoteCommand = @'\n", 1)[1]
        .split("\n'@", 1)[0]
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def _extract_function(payload: str, name: str) -> str:
    start = payload.index(f"{name}() {{")
    depth = 0
    for index in range(start, len(payload)):
        if payload[index] == "{":
            depth += 1
        elif payload[index] == "}":
            depth -= 1
            if depth == 0:
                return payload[start : index + 1]
    raise AssertionError(f"could not extract {name}")


class RealityShortIdHardenTests(ScriptValidationMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_apply_is_opt_in_and_idempotent(self) -> None:
        text = self.text
        self.assertIn("[switch]$Apply", text)
        self.assertIn("if [ \"$APPLY\" != '1' ]; then", text)
        self.assertIn("shortid_hardening=clean", text)
        self.assertIn("shortid_hardening=needs_strip", text)
        self.assertIn("write_status noop 0", text)
        # Never strip the last id (that would break the inbound).
        self.assertIn("if len(kept) == 0:", text)

    def test_apply_is_backup_first_and_verifies(self) -> None:
        text = self.text
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("ROLLBACK_FAILED", text)
        self.assertIn("XRAY_CONFIG_TEST_FAILED", text)
        self.assertIn("SINGBOX_CHECK_FAILED", text)
        self.assertIn("POST_STRIP_STILL_EMPTY", text)
        self.assertIn("flock -n 9", text)
        self.assertIn("singbox-config.json", text)
        self.assertIn("singbox-config.missing", text)
        self.assertIn("xray_touched=0", text)
        self.assertIn("singbox_touched=0", text)
        # Rollback must verify both daemons independently on BWG dual-core;
        # one active service must not mask the other service's failure.
        self.assertIn('if [ "$xray_touched" -eq 1 ]; then', text)
        self.assertIn('if [ "$singbox_touched" -eq 1 ]; then', text)
        # Rollback source-tree restores must not fail silently: a failed cp
        # has to yield ROLLBACK_FAILED instead of a false VERIFIED.
        self.assertIn(
            'cp -a "$backup_dir/xray/." "$XRAY_CONF/" || rollback_failed=1', text
        )
        self.assertIn(
            'cp -a "$backup_dir/singbox/." "$SB_SOURCE_DIR/" || rollback_failed=1',
            text,
        )
        # Post-strip recheck is fail-closed: only a clean scan (rc 3) passes;
        # a surviving empty id (rc 0) or invalid JSON (rc 2) must roll back.
        self.assertIn(
            'scan_files "${xray_files[@]}" >/dev/null || scan_rc=$?', text
        )
        self.assertIn("scan_singbox_source >/dev/null || scan_rc=$?", text)
        self.assertIn('if [ "$scan_rc" -ne 3 ]; then recheck=1; fi', text)
        # Upstream vasma stays untouched.
        self.assertNotIn("/etc/v2ray-agent/install.sh", text)

    def _bash(self) -> str:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        return bash

    def _run(
        self, bash: str, payload: str, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        # Git Bash on Windows has no `python3`; provide one backed by the
        # project interpreter so the embedded scanners can run.
        payload = f'python3() {{ {shlex.quote(sys.executable)} "$@"; }}\n' + payload
        return subprocess.run(
            self._bash_command(bash, "-c", payload),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, **env},
        )

    def _xray_tree(self, root: Path) -> Path:
        conf = root / "xrayconf"
        conf.mkdir(parents=True)
        (conf / "07.json").write_text(
            json.dumps(
                {
                    "inbounds": [
                        {
                            "port": 45987,
                            "streamSettings": {
                                "realitySettings": {
                                    "shortIds": ["", "6ba85179e30d4fc2"]
                                }
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return conf

    def test_read_only_reports_needs_strip_without_writing(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "0")
        bash = self._bash()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = self._xray_tree(root)
            before = (conf / "07.json").read_text(encoding="utf-8")
            result = self._run(
                bash,
                payload,
                {
                    "VPS_SIDH_XRAY_CONF": self._bash_path(bash, conf),
                    "VPS_SIDH_XRAY_BINARY": "false",
                    "VPS_SIDH_SB_CONF_DIR": self._bash_path(bash, root / "nosb"),
                    "VPS_SIDH_SB_BINARY": "false",
                    "VPS_SIDH_STATUS_DIR": self._bash_path(bash, root / "status"),
                    "VPS_SIDH_BACKUP_DIR": self._bash_path(bash, root),
                    "VPS_SIDH_LOCK_FILE": self._bash_path(bash, root / "lock"),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("xray_empty_short_id=yes", result.stdout)
            self.assertIn("shortid_hardening=needs_strip", result.stdout)
            self.assertEqual((conf / "07.json").read_text(encoding="utf-8"), before)

    def test_read_only_fails_closed_on_invalid_source_json(self) -> None:
        payload = _payload(self.text).replace("__APPLY__", "0")
        bash = self._bash()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "xrayconf"
            conf.mkdir()
            (conf / "broken.json").write_text("{not-json", encoding="utf-8")
            result = self._run(
                bash,
                payload,
                {
                    "VPS_SIDH_XRAY_CONF": self._bash_path(bash, conf),
                    "VPS_SIDH_XRAY_BINARY": "false",
                    "VPS_SIDH_SB_CONF_DIR": self._bash_path(bash, root / "nosb"),
                    "VPS_SIDH_SB_BINARY": "false",
                    "VPS_SIDH_STATUS_DIR": self._bash_path(bash, root / "status"),
                    "VPS_SIDH_BACKUP_DIR": self._bash_path(bash, root),
                    "VPS_SIDH_LOCK_FILE": self._bash_path(bash, root / "lock"),
                },
            )
            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertIn("shortid_hardening=invalid", result.stderr)

    def test_strip_removes_empty_but_keeps_nonempty(self) -> None:
        # Exercise strip_files directly through a tiny harness so the check does
        # not depend on a running xray binary.
        func = _extract_function(_payload(self.text), "strip_files")
        script = (
            "set -Eeuo pipefail\n"
            f'python3() {{ {shlex.quote(sys.executable)} "$@"; }}\n'
            + func
            + '\nstrip_files "$1" || true\n'
        )
        bash = self._bash()
        with tempfile.TemporaryDirectory() as tmp:
            conf = self._xray_tree(Path(tmp))
            target = conf / "07.json"
            completed = subprocess.run(
                self._bash_command(
                    bash, "-c", script, "_", self._bash_path(bash, target)
                ),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            data = json.loads(target.read_text(encoding="utf-8"))
            ids = data["inbounds"][0]["streamSettings"]["realitySettings"]["shortIds"]
            self.assertEqual(ids, ["6ba85179e30d4fc2"])


if __name__ == "__main__":
    unittest.main()

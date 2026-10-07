"""test_connect_entrypoints.py - split from test_scripts.py (domain: connect)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from script_validation_support import (
    ScriptValidationMixin,
)


class ConnectEntrypointTests(ScriptValidationMixin, unittest.TestCase):
    def test_ssh_tool_direct_execution_invokes_cli(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, str(repo_root / "ssh_tool.py"), "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout.lower())

    def test_connect_ps1_template_uses_password_env(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('"password_env": "VPS_EXAMPLE_PASSWORD"', text)
        self.assertNotIn('"password": "YOUR_PASSWORD"', text)

    def test_connect_cmd_requires_powershell_7(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "connect.cmd").read_text(
            encoding="utf-8"
        )

        self.assertIn("VPS_SSH_LAUNCHER_POWERSHELL", text)
        self.assertIn("pwsh.exe", text)
        self.assertNotIn('set "POWERSHELL_EXE=powershell.exe"', text)
        self.assertIn("PowerShell 7", text)

    def test_connect_ps1_requires_explicit_allow_global_bootstrap(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[switch]$AllowGlobalBootstrap", text)
        self.assertIn("AllowGlobalBootstrap", text)
        self.assertIn(
            "Refusing to install or upgrade dependencies in non-isolated Python",
            text,
        )
        self.assertIn('importlib.metadata.version("paramiko")', text)
        self.assertIn("major == 5", text)

    def test_connect_command_file_preserves_complex_remote_command(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")

        repo_root = Path(__file__).resolve().parents[1]
        connect_source = (repo_root / "connect.ps1").read_text(encoding="utf-8")
        command = r"""printf '%s\n' "quoted value" | grep quoted && echo 'done'
"""
        helper = r"""
function Initialize-WindowsProcessEnvironment {}
function Resolve-LauncherConfigPath {
  param([string]$ProjectRoot, [string]$Config)
  return $Config
}
function Resolve-LauncherExplicitPath {
  param([string]$Path)
  return [System.IO.Path]::GetFullPath($Path)
}
function Resolve-ProjectPython {
  param([string]$ProjectRoot, [switch]$AllowPyLauncher)
  return @{ Exe = "Invoke-LauncherProbe"; Args = @(); IsIsolated = $true }
}
function Invoke-LauncherProbe {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
  [Console]::Out.WriteLine('C:\fixture\site-packages')
  [Console]::Out.WriteLine('5.5.0')
  $global:LASTEXITCODE = 0
}
function Invoke-LauncherPython {
  param(
    [hashtable]$Python,
    [string]$ProjectRoot,
    [string[]]$LauncherArgs
  )
  $payload = ConvertTo-Json -InputObject @($LauncherArgs) -Compress
  [System.IO.File]::WriteAllText($env:VPS_SSH_LAUNCHER_TEST_CAPTURE, $payload)
  $global:LASTEXITCODE = 0
  return 0
}
"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "scripts" / "lib"
            library.mkdir(parents=True)
            (root / "connect.ps1").write_text(connect_source, encoding="utf-8")
            (library / "project_environment.ps1").write_text(helper, encoding="utf-8")
            config = root / "target.json"
            config.write_text("{}\n", encoding="utf-8")
            command_file = root / "remote command.sh"
            command_file.write_bytes(command.encode("utf-8"))
            capture = root / "launcher-args.json"
            env = os.environ.copy()
            env["VPS_SSH_LAUNCHER_TEST_CAPTURE"] = str(capture)

            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-File",
                    str(root / "connect.ps1"),
                    "-Config",
                    str(config),
                    "-Profile",
                    "fixture",
                    "-CommandFile",
                    str(command_file),
                ],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            launcher_args = json.loads(capture.read_text(encoding="utf-8"))
            command_index = launcher_args.index("--command")
            self.assertEqual(launcher_args[command_index + 1], command)

            capture.unlink()
            conflicting = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-File",
                    str(root / "connect.ps1"),
                    "-Config",
                    str(config),
                    "-Command",
                    "echo inline",
                    "-CommandFile",
                    str(command_file),
                ],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertNotEqual(conflicting.returncode, 0)
            self.assertIn(
                "mutually exclusive",
                conflicting.stdout + conflicting.stderr,
            )
            self.assertFalse(capture.exists())

        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        self.assertIn("-CommandFile", readme)
        self.assertIn("run.cmd -Profile example -CommandFile", readme)

    def test_connect_ps1_caches_paramiko_probe_result(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "connect.ps1").read_text(
            encoding="utf-8"
        )

        # A passing probe result is cached next to the config so the common
        # invocation does not pay a full Python process start every time.
        self.assertIn("paramiko-probe.cache", text)
        self.assertIn("Test-ParamikoProbeCache", text)
        self.assertIn('sysconfig.get_paths()["purelib"]', text)
        # Cache invalidation anchors: interpreter identity, repo requirement
        # drift and an actually-present paramiko distribution.
        self.assertIn("requirements_stamp", text)
        self.assertIn("paramiko-$($marker.version).dist-info", text)
        # The cache only skips the probe; install/upgrade stays reachable and
        # a post-install probe must still gate what gets cached.
        self.assertIn('"-m", "pip", "install"', text)
        self.assertIn("Paramiko probe still failing after dependency install.", text)


if __name__ == "__main__":
    unittest.main()

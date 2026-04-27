import os
import shutil
import subprocess
import unittest
from pathlib import Path


class ScriptValidationTests(unittest.TestCase):
    def test_powershell_scripts_parse(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        repo_root = Path(__file__).resolve().parent
        script_paths = [
            repo_root / "connect.ps1",
            *sorted((repo_root / "scripts").glob("*.ps1")),
        ]

        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                self._assert_powershell_script_parses(powershell, script_path)

    def _assert_powershell_script_parses(
        self, powershell: str, script_path: Path
    ) -> None:
        command = r"""
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path -LiteralPath $env:VPS_SSH_LAUNCHER_SCRIPT_UNDER_TEST),
  [ref]$tokens,
  [ref]$errors
) | Out-Null

if ($errors.Count -gt 0) {
  $errors | ForEach-Object { Write-Error $_.Message }
  exit 1
}
"""
        args = [powershell, "-NoProfile"]
        if Path(powershell).name.lower() == "powershell.exe":
            args += ["-ExecutionPolicy", "Bypass"]
        args += ["-Command", command]
        env = os.environ.copy()
        env["VPS_SSH_LAUNCHER_SCRIPT_UNDER_TEST"] = str(script_path)

        completed = subprocess.run(
            args,
            cwd=script_path.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_google_ipv4_routing_script_is_opt_in_for_apply(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "google_ipv4_routing.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("[switch]$Apply", text)
        self.assertIn("reapply-google-ipv4-routing.sh", text)
        self.assertIn("google_ipv4_out", text)
        self.assertIn("ForceIPv4", text)
        self.assertIn("xray-missing", text)
        self.assertIn("Assert-SafeRemoteApplyScript", text)

    def test_google_ipv4_routing_reuses_project_python_resolution(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "google_ipv4_routing.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("Resolve-ProjectPython", text)
        self.assertIn("VPS_SSH_LAUNCHER_PYTHON", text)
        self.assertIn(".venv\\Scripts\\python.exe", text)
        self.assertIn("$py.Exe @($py.Args + @($sshTool", text)
        self.assertNotIn("& python $sshTool", text)

    def test_connect_ps1_template_uses_password_env(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('"password_env": "VPS_EXAMPLE_PASSWORD"', text)
        self.assertNotIn('"password": "YOUR_PASSWORD"', text)

    def test_connect_ps1_prefers_project_python_over_path(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("VPS_SSH_LAUNCHER_PYTHON", text)
        self.assertIn(".venv\\Scripts\\python.exe", text)

    def test_connect_cmd_prefers_powershell_7(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.cmd").read_text(
            encoding="utf-8"
        )

        self.assertIn("VPS_SSH_LAUNCHER_POWERSHELL", text)
        self.assertIn("pwsh.exe", text)
        self.assertIn("powershell.exe", text)

    def test_connect_ps1_initializes_windows_process_environment(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("Initialize-WindowsProcessEnvironment", text)
        self.assertIn("SYSTEMROOT", text)
        self.assertIn("COMSPEC", text)
        self.assertIn("APPDATA", text)
        self.assertIn("LOCALAPPDATA", text)
        self.assertIn("PROGRAMDATA", text)

    def test_run_gates_uses_same_python_for_all_tools(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "run_gates.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("VPS_SSH_LAUNCHER_PYTHON", text)
        self.assertIn(".venv\\Scripts\\python.exe", text)
        self.assertIn('-m", "ruff"', text)
        self.assertIn('-m", "bandit"', text)
        self.assertIn('-m", "pyright"', text)

    def test_run_gates_requires_isolated_python_for_environment_gates(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "run_gates.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("AllowGlobalPython", text)
        self.assertIn("Assert-IsolatedPythonForEnvironmentGate", text)
        self.assertIn("Assert-PythonAsyncioAvailable", text)
        self.assertIn("Assert-NodeCryptoAvailable", text)
        self.assertIn("Assert-IntegrationProfileIsNonInteractive", text)
        self.assertIn("Initialize-WindowsProcessEnvironment", text)
        self.assertIn("SYSTEMROOT", text)
        self.assertIn("COMSPEC", text)
        self.assertIn("APPDATA", text)
        self.assertIn("RequiresIsolatedPython = $true", text)
        self.assertIn("RequiresPythonAsyncio = $true", text)
        self.assertIn("RequiresNodeCrypto = $true", text)
        self.assertIn("python -m venv .venv", text)

    def test_powershell_entrypoints_fail_fast_on_invalid_python_env(self) -> None:
        repo_root = Path(__file__).resolve().parent
        script_paths = [
            repo_root / "connect.ps1",
            repo_root / "scripts" / "run_gates.ps1",
            repo_root / "scripts" / "google_ipv4_routing.ps1",
        ]

        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                text = script_path.read_text(encoding="utf-8")
                self.assertIn(
                    "VPS_SSH_LAUNCHER_PYTHON is set but the file does not exist",
                    text,
                )


if __name__ == "__main__":
    unittest.main()

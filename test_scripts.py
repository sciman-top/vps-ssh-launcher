import shutil
import subprocess
import unittest
from pathlib import Path


class ScriptValidationTests(unittest.TestCase):
    def test_connect_ps1_parses(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        command = r"""
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path 'connect.ps1'),
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

        completed = subprocess.run(
            args,
            cwd=Path(__file__).resolve().parent,
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

    def test_run_gates_uses_same_python_for_all_tools(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "run_gates.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("VPS_SSH_LAUNCHER_PYTHON", text)
        self.assertIn(".venv\\Scripts\\python.exe", text)
        self.assertIn('-m", "ruff"', text)
        self.assertIn('-m", "bandit"', text)
        self.assertIn('-m", "pyright"', text)


if __name__ == "__main__":
    unittest.main()

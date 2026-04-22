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


if __name__ == "__main__":
    unittest.main()

import json
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
            *sorted((repo_root / "scripts").rglob("*.ps1")),
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
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "google_ipv4_routing.ps1").read_text(
            encoding="utf-8"
        )
        helper = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("project_environment.ps1", text)
        self.assertIn("Resolve-ProjectPython", helper)
        self.assertIn("VPS_SSH_LAUNCHER_PYTHON", helper)
        self.assertIn(".venv\\Scripts\\python.exe", helper)
        self.assertIn("Invoke-LauncherPython", helper)
        self.assertIn("Invoke-LauncherPython -Python $py", text)
        self.assertNotIn("& python $sshTool", text)

    def test_vasma_kernel_cron_uses_vasma_menu_not_direct_downloads(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("16.core管理 -> 1.Xray-core -> 1.升级Xray-core", text)
        self.assertIn("16.core管理 -> 2.sing-box -> 1.升级 sing-box", text)
        self.assertIn("printf '16\\n1\\n1\\ny\\n' | /usr/bin/vasma", text)
        self.assertIn("printf '16\\n2\\n1\\ny\\n' | /usr/bin/vasma", text)
        self.assertIn("vasma_visible_stable_xray_version", text)
        self.assertIn("vasma_visible_stable_singbox_version", text)
        self.assertIn("XTLS/Xray-core/releases/latest", text)
        self.assertIn("SagerNet/sing-box/releases/latest", text)
        self.assertIn("skip update to avoid empty download URL", text)
        self.assertIn("skip reinstall", text)
        self.assertIn("auto_update_xray.sh", text)
        self.assertIn("auto_update_singbox.sh", text)
        self.assertIn("grep -v -E '/etc/v2ray-agent/auto_update_", text)
        self.assertNotIn("releases?per_page", text)
        self.assertNotIn("github.com/XTLS/Xray-core/releases/download", text)
        self.assertNotIn("github.com/SagerNet/sing-box/releases/download", text)
        self.assertNotIn('REPO="XTLS/Xray-core"', text)
        self.assertNotIn('REPO="SagerNet/sing-box"', text)

    def test_weekly_kernel_cron_avoids_monthly_maintenance_collision(self) -> None:
        repo_root = Path(__file__).resolve().parent
        script = (repo_root / "scripts" / "vasma_kernel_update_cron.ps1").read_text(
            encoding="utf-8"
        )
        readme = (repo_root / "README.md").read_text(encoding="utf-8")

        self.assertIn('[string]$Schedule = "20 14 * * 5"', script)
        self.assertIn("20 14 * * 5", readme)
        self.assertIn("每月 1 日 22:00", readme)
        self.assertIn("维护锁", readme)

    def test_high_risk_vps_updates_are_documented_as_sequential(self) -> None:
        repo_root = Path(__file__).resolve().parent
        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        agents = (repo_root / "AGENTS.md").read_text(encoding="utf-8")
        script = (repo_root / "scripts" / "vasma_kernel_update_cron.ps1").read_text(
            encoding="utf-8"
        )

        for text in (readme, agents):
            self.assertIn("逐台执行", text)
            self.assertIn("等待用户确认", text)
            self.assertIn("禁止并行触发两台 VPS", text)

        self.assertIn("never trigger multiple VPS kernel updates in parallel", script)
        self.assertIn("verify in a", script)
        self.assertIn("second SSH command", script)

    def test_connect_ps1_template_uses_password_env(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('"password_env": "VPS_EXAMPLE_PASSWORD"', text)
        self.assertNotIn('"password": "YOUR_PASSWORD"', text)

    def test_connect_ps1_prefers_project_python_over_path(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "connect.ps1").read_text(encoding="utf-8")
        helper = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("project_environment.ps1", text)
        self.assertIn("VPS_SSH_LAUNCHER_PYTHON", helper)
        self.assertIn(".venv\\Scripts\\python.exe", helper)

    def test_connect_cmd_prefers_powershell_7(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.cmd").read_text(
            encoding="utf-8"
        )

        self.assertIn("VPS_SSH_LAUNCHER_POWERSHELL", text)
        self.assertIn("pwsh.exe", text)
        self.assertIn("powershell.exe", text)

    def test_connect_ps1_initializes_windows_process_environment(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "connect.ps1").read_text(encoding="utf-8")
        helper = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("project_environment.ps1", text)
        self.assertIn("Initialize-WindowsProcessEnvironment", text)
        self.assertIn("SYSTEMROOT", helper)
        self.assertIn("COMSPEC", helper)
        self.assertIn("APPDATA", helper)
        self.assertIn("LOCALAPPDATA", helper)
        self.assertIn("PROGRAMDATA", helper)

    def test_connect_ps1_passes_command_timeout_to_python_cli(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[int]$CommandTimeout = 60", text)
        self.assertIn("--command-timeout", text)
        self.assertIn("$CommandTimeout", text)

    def test_connect_ps1_passes_command_hard_timeout_to_python_cli(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[int]$CommandHardTimeout = 0", text)
        self.assertIn("--command-hard-timeout", text)
        self.assertIn("$CommandHardTimeout", text)

    def test_connect_ps1_passes_max_workers_to_python_cli(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[ValidateRange(1, 128)]", text)
        self.assertIn("[int]$MaxWorkers", text)
        self.assertIn('PSBoundParameters.ContainsKey("MaxWorkers")', text)
        self.assertIn("--max-workers", text)

    def test_connect_ps1_requires_explicit_allow_global_bootstrap(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[switch]$AllowGlobalBootstrap", text)
        self.assertIn("AllowGlobalBootstrap", text)
        self.assertIn("Refusing to install dependencies into non-isolated Python", text)

    def test_run_gates_uses_same_python_for_all_tools(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "run_gates.ps1").read_text(encoding="utf-8")
        helper = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("project_environment.ps1", text)
        self.assertIn("VPS_SSH_LAUNCHER_PYTHON", helper)
        self.assertIn(".venv\\Scripts\\python.exe", helper)
        self.assertIn("$pythonGateFiles", text)
        self.assertIn("$powershellPolicyVerifier", text)
        self.assertIn(".governed-ai\\verify-powershell-policy.py", text)
        self.assertIn("contract:powershell-policy", text)
        self.assertIn('-m", "ruff"', text)
        self.assertIn('-m", "bandit"', text)
        self.assertIn('-m", "pyright"', text)
        self.assertNotIn('"ruff", "check", "."', text)
        self.assertNotIn('"ruff", "format", "--check", "."', text)

    def test_repo_profile_matches_targeted_python_gate_scope(self) -> None:
        text = (
            Path(__file__).resolve().parent / ".governed-ai" / "repo-profile.json"
        ).read_text(encoding="utf-8")

        self.assertIn("python .governed-ai/verify-powershell-policy.py", text)
        self.assertIn(
            "python -m ruff check ssh_tool.py auto_install.py test_ssh_tool.py",
            text,
        )
        self.assertIn(
            "python -m ruff format --check ssh_tool.py auto_install.py test_ssh_tool.py",
            text,
        )
        self.assertNotIn("python -m ruff check .", text)
        self.assertNotIn("python -m ruff format --check .", text)

    def test_repo_profile_gate_ids_and_commands_match_run_gates_contract(self) -> None:
        repo_root = Path(__file__).resolve().parent
        run_gates_text = (repo_root / "scripts" / "run_gates.ps1").read_text(
            encoding="utf-8"
        )
        profile = json.loads(
            (repo_root / ".governed-ai" / "repo-profile.json").read_text(
                encoding="utf-8"
            )
        )

        contract_commands = {
            item["id"]: item["command"] for item in profile["contract_commands"]
        }
        quick_gate_commands = {
            item["id"]: item["command"] for item in profile["quick_gate_commands"]
        }
        full_gate_commands = {
            item["id"]: item["command"] for item in profile["full_gate_commands"]
        }

        self.assertIn("contract:powershell-policy", run_gates_text)
        self.assertIn(
            "python .governed-ai/verify-powershell-policy.py",
            contract_commands["contract:powershell-policy"],
        )
        self.assertIn(
            "python .governed-ai/verify-powershell-policy.py",
            quick_gate_commands["contract:powershell-policy"],
        )
        self.assertIn(
            "python .governed-ai/verify-powershell-policy.py",
            full_gate_commands["contract:powershell-policy"],
        )

    def test_run_gates_requires_isolated_python_for_environment_gates(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "run_gates.ps1").read_text(encoding="utf-8")
        helper = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("AllowGlobalPython", text)
        self.assertIn("Assert-IsolatedPythonForEnvironmentGate", text)
        self.assertIn("Assert-PythonAsyncioAvailable", text)
        self.assertIn("Assert-NodeCryptoAvailable", text)
        self.assertIn("Assert-IntegrationProfileIsNonInteractive", text)
        self.assertIn("Initialize-WindowsProcessEnvironment", text)
        self.assertIn("SYSTEMROOT", helper)
        self.assertIn("COMSPEC", helper)
        self.assertIn("APPDATA", helper)
        self.assertIn("RequiresIsolatedPython = $true", text)
        self.assertIn("RequiresPythonAsyncio = $true", text)
        self.assertIn("RequiresNodeCrypto = $true", text)
        self.assertIn("python -m venv .venv", text)

    def test_run_gates_resolves_effective_integration_config_for_guard(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "run_gates.ps1").read_text(encoding="utf-8")

        self.assertIn("function Resolve-IntegrationConfigPath", text)
        self.assertIn("vps-ssh-launcher\\target.json", text)
        self.assertIn("$effectiveIntegrationConfig", text)
        self.assertIn("-ConfigPath $effectiveIntegrationConfig", text)
        self.assertIn(
            "$env:VPS_SSH_LAUNCHER_INTEGRATION_CONFIG = $effectiveIntegrationConfig",
            text,
        )

    def test_powershell_entrypoints_fail_fast_on_invalid_python_env(self) -> None:
        repo_root = Path(__file__).resolve().parent
        script_paths = [
            repo_root / "scripts" / "lib" / "project_environment.ps1",
        ]

        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                text = script_path.read_text(encoding="utf-8")
                self.assertIn(
                    "VPS_SSH_LAUNCHER_PYTHON is set but the file does not exist",
                    text,
                )

    def test_powershell_entrypoints_reuse_shared_environment_helper(self) -> None:
        repo_root = Path(__file__).resolve().parent
        script_paths = [
            repo_root / "connect.ps1",
            repo_root / "scripts" / "run_gates.ps1",
            repo_root / "scripts" / "google_ipv4_routing.ps1",
            repo_root / "scripts" / "vasma_kernel_update_cron.ps1",
        ]

        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                text = script_path.read_text(encoding="utf-8")
                self.assertIn("project_environment.ps1", text)
                self.assertNotIn("function Resolve-ProjectPython", text)

    def test_shared_environment_helper_is_the_only_inline_environment_definition(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parent
        all_scripts = [
            repo_root / "connect.ps1",
            *sorted((repo_root / "scripts").rglob("*.ps1")),
        ]

        for script_path in all_scripts:
            text = script_path.read_text(encoding="utf-8")
            is_helper = script_path.name == "project_environment.ps1"
            with self.subTest(script=str(script_path.relative_to(repo_root))):
                if is_helper:
                    self.assertIn("function Initialize-WindowsProcessEnvironment", text)
                    self.assertIn("function Resolve-ProjectPython", text)
                else:
                    self.assertNotIn(
                        "function Initialize-WindowsProcessEnvironment",
                        text,
                    )
                    self.assertNotIn("function Resolve-ProjectPython", text)


if __name__ == "__main__":
    unittest.main()

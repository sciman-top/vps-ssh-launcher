"""test_ci_meta.py - split from test_scripts.py (domain: ci)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import tomllib
from pathlib import Path
from typing import Any

from script_validation_support import (
    ScriptValidationMixin,
)


def load_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        raise unittest.SkipTest("PyYAML is not installed on this host") from None
    loaded: Any = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise AssertionError("workflow YAML must be a mapping")
    # PyYAML's YAML 1.1 resolver turns the workflow key ``on`` into True.
    if True in loaded and "on" not in loaded:
        loaded["on"] = loaded.pop(True)
    return loaded


class CiMetaTests(ScriptValidationMixin, unittest.TestCase):
    def test_gate_integration_opt_in_is_scoped_and_restored(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")
        repo_root = Path(__file__).resolve().parents[1]
        # Exercise the real runner with a disposable executor. No Python test
        # or remote command is run; each gate records only the effective flag.
        helper = r"""
function Initialize-WindowsProcessEnvironment {}
function Resolve-ProjectPython {
  return @{ Exe = "Invoke-GateProbe"; IsIsolated = $true; Source = "fixture" }
}
function Invoke-GateProbe {
  $global:observedFlags += $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION
  $global:LASTEXITCODE = if ($env:VPS_GATE_FAIL -eq "1") { 7 } else { 0 }
}
"""
        command = r"""
$global:observedFlags = @()
$failed = $false
try {
  if ($env:VPS_GATE_EXPLICIT -eq "1") {
    & $env:VPS_GATE_RUNNER -Profile Integration -RunIntegration `
      -IntegrationConfig $env:VPS_GATE_CONFIG -IntegrationProfile fixture
  } else {
    & $env:VPS_GATE_RUNNER -Profile Full
  }
} catch { $failed = $true }
@{
  flags = @($global:observedFlags)
  failed = $failed
  restored = @(
    $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION,
    $env:VPS_SSH_LAUNCHER_INTEGRATION_CONFIG,
    $env:VPS_SSH_LAUNCHER_INTEGRATION_PROFILE
  )
} | ConvertTo-Json -Compress
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            (scripts / "lib").mkdir(parents=True)
            # The gate globs <repoRoot>/tests for its test-file discovery.
            (root / "tests").mkdir()
            runner = scripts / "run_gates.ps1"
            runner.write_bytes((repo_root / "scripts/run_gates.ps1").read_bytes())
            (scripts / "lib/project_environment.ps1").write_text(
                helper, encoding="utf-8"
            )
            config_path = root / "fixture.json"
            config_path.write_text("{}", encoding="utf-8")
            for explicit, fail in ((False, False), (True, False), (True, True)):
                with self.subTest(explicit=explicit, fail=fail):
                    env = os.environ.copy()
                    env.update(
                        {
                            "VPS_GATE_RUNNER": str(runner),
                            "VPS_GATE_CONFIG": str(config_path),
                            "VPS_GATE_EXPLICIT": "1" if explicit else "0",
                            "VPS_GATE_FAIL": "1" if fail else "0",
                            "VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1",
                            "VPS_SSH_LAUNCHER_INTEGRATION_CONFIG": "original-config",
                            "VPS_SSH_LAUNCHER_INTEGRATION_PROFILE": "original-profile",
                        }
                    )
                    result = subprocess.run(
                        [powershell, "-NoProfile", "-Command", command],
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(result.stdout.splitlines()[-1])
                    self.assertTrue(report["flags"], result.stdout)
                    self.assertEqual(set(report["flags"]), {"1" if explicit else "0"})
                    self.assertEqual(report["failed"], fail)
                    self.assertEqual(
                        report["restored"],
                        ["1", "original-config", "original-profile"],
                    )

    def test_powershell_scripts_parse(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")

        repo_root = Path(__file__).resolve().parents[1]
        script_paths = [
            repo_root / "connect.ps1",
            *sorted((repo_root / "scripts").rglob("*.ps1")),
        ]

        command = r"""
$failed = $false
foreach ($path in ($env:VPS_SSH_LAUNCHER_SCRIPTS_UNDER_TEST | ConvertFrom-Json)) {
  $tokens = $null
  $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile(
    $path, [ref]$tokens, [ref]$errors
  ) | Out-Null
  foreach ($error in $errors) {
    Write-Output ($path + ': ' + $error.Message)
    $failed = $true
  }
}
if ($failed) { exit 1 }
"""
        env = os.environ.copy()
        env["VPS_SSH_LAUNCHER_SCRIPTS_UNDER_TEST"] = json.dumps(
            [str(path) for path in script_paths]
        )
        completed = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_operational_scripts_require_powershell_7(self) -> None:
        # PS 5.1 reads UTF-8 (no BOM) scripts as ANSI and would silently
        # corrupt the vasma menu anchors before they are deployed; the family
        # also relies on pwsh-only behavior (utf8NoBOM). The #requires line is
        # ASCII, so 5.1 refuses cleanly instead of running corrupted.
        # Enumerate the directory instead of a hand-kept list: the 2026-10-04
        # audit found five scripts (bwg_full_maintenance, cockpit_sidecar_
        # guardrails, cpa_recovery_workflow, v2ray_agent_renewtls_cron,
        # v2ray_agent_script_update_cron) were outside the old list.
        repo_root = Path(__file__).resolve().parents[1]
        scripts = sorted((repo_root / "scripts").glob("*.ps1"))
        self.assertGreater(len(scripts), 0)
        for script in scripts:
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                self.assertTrue(
                    text.startswith("#requires -Version 7"),
                    f"{script.name} must refuse pre-7 PowerShell hosts",
                )

    def test_shared_remote_command_transport_contract(self) -> None:
        # Behavioral contract of Invoke-LauncherRemoteCommand with a stubbed
        # Invoke-LauncherPython: no SSH and no python process are touched.
        # Pins arg order (globals before "run", timeout flags after it),
        # base64 single-shot, CRLF normalization and the chunked mode-600
        # temp-file path used by every maintenance wrapper.
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")
        repo_root = Path(__file__).resolve().parents[1]
        probe = r"""
param([Parameter(Mandatory = $true)][string]$RepoRoot)
. (Join-Path $RepoRoot "scripts\lib\project_environment.ps1")
$global:captured = @()
function Invoke-LauncherPython {
  param($Python, $ProjectRoot, $LauncherArgs)
  $global:captured += , @($LauncherArgs)
  return 0
}
$py = @{ Exe = "fake"; Args = @(); IsIsolated = $true; Source = "fixture" }

$global:captured = @()
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command "echo hello"
$c1 = $global:captured
if ($c1.Count -ne 1) { throw "small: expected 1 invocation, got $($c1.Count)" }
$a1 = $c1[0]
$head = ($a1[0..4] -join " ")
if ($head -ne "--config C:\cfg.json --profile bwg --strict-host-key-checking") { throw "head: $head" }
if ($a1[5] -ne "run" -or $a1[6] -ne "--command") { throw "mid: $($a1[5]) $($a1[6])" }
$b64 = $a1[7] -replace "^printf %s ", "" -replace " \| base64 -d \| bash$", ""
$decoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
if ($decoded -ne "echo hello") { throw "decoded: $decoded" }

$global:captured = @()
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command "true" -IdleTimeoutSeconds 300 -HardTimeoutSeconds 360
$a2 = $global:captured[0]
$i = [array]::IndexOf($a2, "run")
if ($i -lt 0) { throw "no run token" }
$tail = ($a2[$i..($a2.Count - 1)] -join " ")
if ($tail -notmatch "^run --command-timeout 300 --command-hard-timeout 360 --command printf") { throw "tail: $tail" }

$global:captured = @()
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command ("set -e`r`nif [ 1 ]; then`r`n  echo ok`r`nfi")
$p3 = $global:captured[0][-1]
$b64 = [regex]::Match($p3, "printf %s (\S+) \|").Groups[1].Value
$dec3 = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
if ($dec3.Contains("`r")) { throw "CR survived normalization" }
if ($dec3 -ne "set -e`nif [ 1 ]; then`n  echo ok`nfi") { throw "decoded: $($dec3 -replace "`n", "<NL>")" }

$global:captured = @()
$big = ("`$" + "x" * 20000)
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command $big -RemoteTempPrefix "vasma-kernel"
$c4 = $global:captured
if ($c4[0][-1] -notmatch "umask 077; : > '/tmp/vasma-kernel-[0-9a-f]{32}\.b64'; chmod 600") {
  throw "temp setup: $($c4[0][-1])"
}
$chunks = @($c4 | Where-Object { $_[-1] -match "^printf %s '.{1,12000}' >> '/tmp/vasma-kernel-" })
$final = @($c4 | Where-Object { $_[-1] -match "set -o pipefail; base64 -d" })
if ($chunks.Count -lt 2) { throw "expected >=2 chunk appends, got $($chunks.Count)" }
if ($final.Count -ne 1) { throw "expected 1 final exec, got $($final.Count)" }
Write-Output "TRANSPORT_CONTRACT_OK"
"""
        with tempfile.TemporaryDirectory() as directory:
            probe_path = Path(directory) / "transport_probe.ps1"
            probe_path.write_text(probe, encoding="utf-8", newline="\n")
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(probe_path),
                    "-RepoRoot",
                    str(repo_root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            (completed.stdout or "") + (completed.stderr or ""),
        )
        self.assertIn("TRANSPORT_CONTRACT_OK", completed.stdout)

    def test_focused_gate_discovers_related_tests_for_source_only_changes(
        self,
    ) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")
        repo_root = Path(__file__).resolve().parents[1]
        helper = r"""
function Initialize-WindowsProcessEnvironment {}
function Resolve-ProjectPython {
  return @{ Exe = "Invoke-GateProbe"; IsIsolated = $true; Source = "fixture" }
}
function Invoke-GateProbe {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
  $global:observedCommands += , @($Arguments)
  $global:LASTEXITCODE = 0
}
"""
        command = r"""
$global:observedCommands = @()
& $env:VPS_GATE_RUNNER -Profile Focused -FocusPath vps_ssh_launcher\config.py
@{ commands = $global:observedCommands } | ConvertTo-Json -Compress -Depth 4
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            (scripts / "lib").mkdir(parents=True)
            tests = root / "tests"
            tests.mkdir()
            (root / "vps_ssh_launcher").mkdir()
            (root / "vps_ssh_launcher/config.py").write_text("pass\n", encoding="utf-8")
            (tests / "test_config.py").write_text("", encoding="utf-8")
            (tests / "test_unrelated.py").write_text("", encoding="utf-8")
            runner = scripts / "run_gates.ps1"
            runner.write_bytes((repo_root / "scripts/run_gates.ps1").read_bytes())
            (scripts / "lib/project_environment.ps1").write_text(
                helper, encoding="utf-8"
            )
            env = os.environ.copy()
            env.update(
                {
                    "VPS_GATE_RUNNER": str(runner),
                    "PSModulePath": os.environ.get("PSModulePath", ""),
                }
            )
            completed = subprocess.run(
                [powershell, "-NoProfile", "-Command", command],
                env=env,
                cwd=root,
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
            (completed.stdout or "") + (completed.stderr or ""),
        )
        report = json.loads(completed.stdout.splitlines()[-1])
        commands = report["commands"]
        self.assertEqual(len(commands), 4, commands)
        self.assertEqual(commands[0][:3], ["-m", "pytest", "-q"])
        self.assertEqual(
            [str(Path(path).resolve()) for path in commands[0][3:]],
            [str((root / "tests/test_config.py").resolve())],
        )

    def test_gate_test_files_match_pytest_testpaths(self) -> None:
        # pytest collects the tests/ tree only: the repo root also holds the
        # live sciman-v2ray-agent upstream checkout whose test tree must not
        # be collected. The gate globs the same directory for static checks,
        # so no test file may regress to the old flat root layout.
        repo_root = Path(__file__).resolve().parents[1]
        with (repo_root / "pyproject.toml").open("rb") as handle:
            testpaths = tomllib.load(handle)["tool"]["pytest"]["ini_options"][
                "testpaths"
            ]
        self.assertEqual(testpaths, ["tests"])
        self.assertTrue(
            list((repo_root / "tests").glob("test_*.py")),
            "the tests/ directory must hold the collected test files",
        )
        self.assertEqual(
            list(repo_root.glob("test_*.py")),
            [],
            "test files must live under tests/, not the repository root",
        )

    def test_package_layering_matches_documented_dependency_direction(self) -> None:
        # README「模块边界与功能维护」：依赖方向为 CLI/维护入口 -> 能力模块 ->
        # contracts；能力模块不导入 CLI 或维护领域。用 AST 固化该结构契约，
        # 新增功能不得让下层模块反向依赖入口层。
        import ast

        repo_root = Path(__file__).resolve().parents[1]
        package_dir = repo_root / "vps_ssh_launcher"
        entry_files = {"cli.py", "maintenance_cli.py"}
        # __init__ 只在 main() 内延迟加载 cli（README 记载的设计），不参与断言。
        exempt_files = {"__init__.py", "__main__.py"}
        capability_allowed = {
            "contracts.py": set(),
            "config.py": {"contracts"},
            "execution.py": {"contracts"},
            "connection.py": {"contracts", "config", "socks5"},
            "socks5.py": set(),
            "batch.py": {"contracts", "config", "connection", "execution"},
        }

        def package_modules_imported(tree: ast.Module, *, level: int) -> set[str]:
            names: set[str] = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level != level:
                    continue
                if node.module:
                    names.add(node.module.split(".")[0])
                else:
                    names.update(alias.name for alias in node.names)
            return names

        for path in sorted(package_dir.glob("*.py")):
            if path.name in exempt_files or path.name in entry_files:
                continue
            with self.subTest(module=path.name):
                self.assertIn(
                    path.name,
                    capability_allowed,
                    "classify new package modules in the layering contract",
                )
                tree = ast.parse(path.read_text(encoding="utf-8"))
                names = package_modules_imported(tree, level=1)
                self.assertLessEqual(names, capability_allowed[path.name])

        for path in sorted((package_dir / "maintenance").glob("*.py")):
            if path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # 同层 sibling import（level 1）不受限；父包方向不得依赖入口层。
            names = package_modules_imported(tree, level=2)
            with self.subTest(module=f"maintenance/{path.name}"):
                self.assertEqual(names & {"cli", "maintenance_cli"}, set())
                # Maintenance implementation modules must never import an
                # entrypoint back. Cover absolute imports too:
                # the level-2 check above intentionally sees only relative
                # syntax and would otherwise allow
                # ``from vps_ssh_launcher.maintenance_cli import main``.
                absolute_names: set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        absolute_names.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        if node.module:
                            absolute_names.add(node.module)
                forbidden = {
                    "vps_ssh_launcher.cli",
                    "vps_ssh_launcher.maintenance_cli",
                }
                self.assertEqual(absolute_names & forbidden, set())

    def test_powershell_entrypoints_reuse_shared_environment_helper(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        required_helpers = {
            repo_root / "connect.ps1",
            repo_root / "scripts" / "run_gates.ps1",
            repo_root / "scripts" / "google_ipv4_routing.ps1",
            repo_root / "scripts" / "vasma_kernel_update_cron.ps1",
            repo_root / "scripts" / "v2ray_agent_script_update_cron.ps1",
            repo_root / "scripts" / "system_maintenance_cron.ps1",
            repo_root / "scripts" / "vps_maintenance.ps1",
            repo_root / "scripts" / "v2ray_agent_renewtls_cron.ps1",
            repo_root / "scripts" / "bwg_full_maintenance.ps1",
        }

        script_paths = [
            repo_root / "connect.ps1",
            *sorted((repo_root / "scripts").rglob("*.ps1")),
        ]
        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                text = script_path.read_text(encoding="utf-8")
                if script_path in required_helpers:
                    self.assertIn("project_environment.ps1", text)
                if script_path.name != "project_environment.ps1":
                    self.assertNotIn("function Resolve-ProjectPython", text)
                    self.assertNotIn(
                        "function Initialize-WindowsProcessEnvironment", text
                    )

    def test_explicit_python_environment_is_probed_for_isolation(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        repo_root = Path(__file__).resolve().parents[1]
        helper = repo_root / "scripts" / "lib" / "project_environment.ps1"
        command = r"""
. $env:VPS_SSH_LAUNCHER_HELPER_UNDER_TEST
$resolved = Resolve-ProjectPython -ProjectRoot $env:VPS_SSH_LAUNCHER_ROOT
$resolved.IsIsolated.ToString().ToLowerInvariant()
"""
        env = os.environ.copy()
        env["VPS_SSH_LAUNCHER_HELPER_UNDER_TEST"] = str(helper)
        env["VPS_SSH_LAUNCHER_ROOT"] = str(repo_root)
        env["VPS_SSH_LAUNCHER_PYTHON"] = sys.executable
        completed = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        expected = str(sys.prefix != sys.base_prefix).lower()
        self.assertEqual(completed.stdout.strip().splitlines()[-1], expected)

    def test_explicit_config_path_is_anchored_before_launcher_changes_directory(
        self,
    ) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")

        repo_root = Path(__file__).resolve().parents[1]
        helper = repo_root / "scripts" / "lib" / "project_environment.ps1"
        command = r"""
. $env:VPS_SSH_LAUNCHER_HELPER_UNDER_TEST
$config = Resolve-LauncherConfigPath `
  -ProjectRoot $env:VPS_SSH_LAUNCHER_ROOT `
  -Config '.\fixture-target.json'
$key = Resolve-LauncherExplicitPath -Path '.\fixture-key'
Push-Location $env:VPS_SSH_LAUNCHER_ROOT
try {
  [System.IO.Path]::GetFullPath($config)
  [System.IO.Path]::GetFullPath($key)
} finally {
  Pop-Location
}
"""
        with tempfile.TemporaryDirectory() as directory:
            # Windows runners may expose TEMP with an 8.3 parent alias while
            # PowerShell reports its long name; compare canonical roots.
            config_path = Path(directory).resolve() / "fixture-target.json"
            config_path.write_text("{}", encoding="utf-8")
            env = os.environ.copy()
            env["VPS_SSH_LAUNCHER_HELPER_UNDER_TEST"] = str(helper)
            env["VPS_SSH_LAUNCHER_ROOT"] = str(repo_root)
            completed = subprocess.run(
                [powershell, "-NoProfile", "-Command", command],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        output_paths = [Path(line) for line in completed.stdout.strip().splitlines()]
        self.assertEqual(
            output_paths, [config_path, config_path.with_name("fixture-key")]
        )

    def test_integration_workflow_is_fixed_strict_and_environment_protected(
        self,
    ) -> None:
        workflow_path = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "integration-real-ssh.yml"
        )
        workflow = load_yaml(workflow_path.read_text(encoding="utf-8"))
        integration = workflow["jobs"]["integration"]
        steps = {step["name"]: step for step in integration["steps"]}
        run_step = steps["Run dedicated SSH integration test"]
        run_text = run_step["run"]
        self.assertEqual(workflow["permissions"]["contents"], "read")
        self.assertEqual(workflow["concurrency"]["group"], "vps-real-ssh-integration")
        self.assertEqual(integration["environment"], "vps-production")
        self.assertIn(
            "VPS_SSH_LAUNCHER_INTEGRATION_KNOWN_HOSTS",
            steps["Prepare integration config"]["env"],
        )
        self.assertEqual(
            run_step["env"]["VPS_SSH_LAUNCHER_INTEGRATION_STRICT_HOST_KEY_CHECKING"],
            "1",
        )
        self.assertIn('"-Profile", "Integration"', run_text)
        self.assertIn("./scripts/run_gates.ps1", run_text)
        self.assertNotIn("-IntegrationCommand", run_text)
        self.assertNotIn("-IntegrationExpected", run_text)
        self.assertIn(
            "python -m pip install -e . pytest", steps["Install dependencies"]["run"]
        )
        self.assertEqual(
            set(workflow["on"]["workflow_dispatch"]["inputs"]), {"integration_profile"}
        )

    def test_ci_workflow_filters_receipts_and_cancels_stale_non_main_runs(
        self,
    ) -> None:
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows/ci.yml"
        data = load_yaml(workflow.read_text(encoding="utf-8"))
        static = [
            step
            for step in data["jobs"]["static"]["steps"]
            if "FullScope Static" in step.get("run", "")
        ]
        runtime = [
            step
            for step in data["jobs"]["tests"]["steps"]
            if "FullScope Runtime" in step.get("run", "")
        ]
        self.assertEqual(
            {
                "static": len(static),
                "runtime": len(runtime),
                "staticAudit": "RunDependencyAudit" in static[0]["run"],
                "runtimeMatrix": len(
                    data["jobs"]["tests"]["strategy"]["matrix"]["include"]
                ),
                "ignoresOutputs": "outputs/**"
                in data["on"]["pull_request"]["paths-ignore"],
                "compiles": any(
                    "compileall" in step.get("run", "")
                    for job in data["jobs"].values()
                    for step in job["steps"]
                ),
            },
            {
                "static": 1,
                "runtime": 1,
                "staticAudit": True,
                "runtimeMatrix": 2,
                "ignoresOutputs": True,
                "compiles": False,
            },
        )

        docs_workflow = load_yaml(
            (
                Path(__file__).resolve().parents[1]
                / ".github"
                / "workflows"
                / "docs.yml"
            ).read_text(encoding="utf-8")
        )
        docs_steps = docs_workflow["jobs"]["diff-check"]["steps"]
        docs_runs = [step.get("run", "") for step in docs_steps]
        self.assertEqual(docs_workflow["name"], "Documentation checks")
        self.assertIn("README.md", docs_workflow["on"]["pull_request"]["paths"])
        self.assertIn("docs/runbooks/**", docs_workflow["on"]["pull_request"]["paths"])
        self.assertTrue(any("git diff --check" in run for run in docs_runs))
        self.assertIn("python scripts/check_docs.py", docs_runs)


if __name__ == "__main__":
    unittest.main()

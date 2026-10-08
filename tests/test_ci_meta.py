"""test_ci_meta.py - split from test_scripts.py (domain: ci)."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from script_validation_support import (
    ScriptValidationMixin,
)


class CiMetaTests(ScriptValidationMixin, unittest.TestCase):
    def test_ci_gate_passes_dependency_audit_as_a_named_switch(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")
        workflow = (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        # Run the workflow's actual PowerShell step against a parameter probe.
        step = workflow.split("- name: Run repository gates", 1)[1].split("run: |", 1)[
            1
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts/run_gates.ps1").write_text(
                "param([string]$Profile, [string[]]$FocusPath = @(), "
                "[switch]$RunDependencyAudit)\n"
                "@{profile=$Profile; focusCount=@($FocusPath).Count; "
                "audit=[bool]$RunDependencyAudit} | ConvertTo-Json -Compress",
                encoding="utf-8",
            )
            for audit in ("true", "false"):
                with self.subTest(audit=audit):
                    result = subprocess.run(
                        [
                            powershell,
                            "-NoProfile",
                            "-Command",
                            step.replace("${{ matrix.dependency-audit }}", audit),
                        ],
                        cwd=root,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        json.loads(result.stdout),
                        {"profile": "Full", "focusCount": 0, "audit": audit == "true"},
                    )

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

    def test_run_gates_covers_profiles_without_duplicate_tools(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "scripts" / "run_gates.ps1").read_text(encoding="utf-8")

        # The gate id set is the reviewed contract: adding or removing a gate
        # must land here explicitly, which is the review point. The concrete
        # command line behind each id is executed for real by the Full profile
        # on every run, so freezing those strings would only add a sync tax.
        declared_ids = set(re.findall(r'Id\s*=\s*"([^"]+)"', text))
        self.assertEqual(
            declared_ids,
            {
                "build",
                "test",
                "invariant:pip-check",
                "invariant:dependency-audit",
                "hotspot:bandit",
                "hotspot:bandit-scripts",
                "lint:ruff",
                "lint:format",
                "type:mypy",
                "focused:test",
                "focused:ruff",
                "focused:format",
                "focused:mypy",
                "integration:test",
            },
        )
        # Scope anchors the id set cannot prove: the test discovery stays
        # glob-derived (a hand-kept list would silently skip new files), and
        # the scripts tree — including projected remote code — stays inside
        # static checks while mypy/format stay scoped to top-level scripts.
        self.assertIn('-Filter "test_*.py" -File', text)
        self.assertIn('$scriptTargets = @("scripts")', text)
        self.assertIn("$scriptTopLevelTargets", text)
        # The integration surface stays switch-scoped: Focused is a
        # local-only profile, and the retired per-command tools must not
        # return as extra parameters.
        self.assertIn('[ValidateSet("Focused", "Full", "Integration")]', text)
        self.assertIn("[string[]]$FocusPath = @()", text)
        self.assertIn('-split ","', text)
        self.assertNotIn("[string]$IntegrationCommand", text)
        self.assertNotIn("[string]$IntegrationExpected", text)
        self.assertNotIn("VPS_SSH_LAUNCHER_INTEGRATION_COMMAND", text)
        self.assertNotIn("VPS_SSH_LAUNCHER_INTEGRATION_EXPECTED", text)
        self.assertNotIn('"unittest"', text)
        self.assertNotIn('"pyright"', text)
        self.assertNotIn('"vulture"', text)

    def test_gate_test_files_match_pytest_testpaths(self) -> None:
        # pytest collects the tests/ tree only: the repo root also holds the
        # live sciman-v2ray-agent upstream checkout whose test tree must not
        # be collected. The gate globs the same directory for static checks,
        # so no test file may regress to the old flat root layout.
        repo_root = Path(__file__).resolve().parents[1]
        import tomllib

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
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "integration-real-ssh.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("integration_command:", workflow)
        self.assertNotIn("integration_expected:", workflow)
        self.assertNotIn("-IntegrationCommand", workflow)
        self.assertNotIn("-IntegrationExpected", workflow)
        self.assertIn("environment: vps-production", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("group: vps-real-ssh-integration", workflow)
        self.assertIn("VPS_SSH_LAUNCHER_INTEGRATION_KNOWN_HOSTS", workflow)
        self.assertIn(
            'VPS_SSH_LAUNCHER_INTEGRATION_STRICT_HOST_KEY_CHECKING: "1"',
            workflow,
        )
        self.assertIn('"-Profile", "Integration"', workflow)
        self.assertIn("python -m pip install -e . pytest", workflow)
        self.assertNotIn('"${{ inputs.integration_profile }}"', workflow)

    def test_ci_workflow_filters_receipts_and_cancels_stale_non_main_runs(
        self,
    ) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("branches:\n      - main", workflow)
        self.assertIn('"docs/change-evidence/**"', workflow)
        self.assertIn('"README.md"', workflow)
        self.assertIn('"docs/runbooks/**"', workflow)
        self.assertIn('".workbuddy-ai/**"', workflow)
        self.assertIn('"outputs/**"', workflow)
        self.assertIn("group: ci-${{ github.workflow }}-${{ github.ref }}", workflow)
        self.assertIn(
            "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}", workflow
        )
        self.assertIn("-Profile Full @gateArgs", workflow)

        docs_workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "docs.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("name: Documentation checks", docs_workflow)
        self.assertIn('"README.md"', docs_workflow)
        self.assertIn('"docs/runbooks/**"', docs_workflow)
        self.assertIn("git diff --check", docs_workflow)


if __name__ == "__main__":
    unittest.main()

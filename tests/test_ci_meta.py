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
from typing import Any

from script_validation_support import (
    ScriptValidationMixin,
    _lane_script,
    repo_root,
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


# The reviewed gate catalog, one entry per logical leg. Ids are shared across
# profile tables on purpose (focused/full reuse "test" and the invariant pair),
# so this set is smaller than the number of `Id = "..."` sites in the runner.
REVIEWED_GATE_IDS = frozenset(
    {
        "contract:catalog",
        "build",
        "test",
        "test-lane",
        "test:serial-recheck",
        "test:filesystem-baseline",
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
    }
)


class CiMetaTests(ScriptValidationMixin, unittest.TestCase):
    def test_powershell_checks_do_not_reject_crlf_checkout(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        attributes = (repo_root / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.ps1 text eol=crlf whitespace=-trailing-space", attributes)

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

    def test_gate_leg_applies_per_command_environment_overrides(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")
        repo_root = Path(__file__).resolve().parents[1]
        helper = r"""
$global:observedGateEnvironment = @()
function Initialize-WindowsProcessEnvironment {}
function Resolve-ProjectPython {
  return @{ Exe = "Invoke-GateProbe"; IsIsolated = $true; Source = "fixture" }
}
function Invoke-GateProbe {
  $commandText = ($args | ForEach-Object { [string]$_ }) -join " "
  if ($args[0] -like "*gate_content_hash.py") {
    '{"tree":"fixture-tree","environment":"fixture-environment"}'
  } else {
    $null = $global:observedGateEnvironment += [PSCustomObject]@{
      command = $commandText
      private = $env:VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP
      workerRoot = $env:VPS_SSH_LAUNCHER_GATE_TEMP_ROOT
      pythonPath = $env:PYTHONPATH
    }
    if ($commandText -match "pytest") {
      "1 passed in 0.1s"
    }
  }
  $global:LASTEXITCODE = 0
}
"""
        command = r"""
$failed = $false
try {
  & $env:VPS_GATE_RUNNER -Profile Full -FullScope Runtime -RunParallel -ProveParallel -RunCoverage -MaxWorkers 2
} catch {
  $failed = $true
  Write-Error $_
}
@{
  failed = $failed
  observed = @($global:observedGateEnvironment)
} | ConvertTo-Json -Compress
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            (scripts / "lib").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "tests/test_probe.py").write_text(
                "def test_probe():\n    assert True\n", encoding="utf-8"
            )
            runner = scripts / "run_gates.ps1"
            runner.write_bytes((repo_root / "scripts/run_gates.ps1").read_bytes())
            (scripts / "lib/project_environment.ps1").write_text(
                helper, encoding="utf-8"
            )
            receipts = root / "outputs" / "gates" / "receipts"
            receipts.mkdir(parents=True)
            (receipts / "test.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "profile": "Full",
                        "tree": "fixture-tree",
                        "environment": "fixture-environment",
                        "workers": 0,
                    }
                ),
                encoding="utf-8",
            )
            (receipts / "parallel-proof.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "profile": "Full",
                        "tree": "fixture-tree",
                        "environment": "fixture-environment",
                        "workers": 2,
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "VPS_GATE_RUNNER": str(runner),
                    "VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP": "original",
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
            self.assertFalse(report["failed"], result.stdout + result.stderr)
            observed = report["observed"]
            self.assertTrue(observed, result.stdout)
            test_runs = [item for item in observed if item["private"] == "1"]
            self.assertEqual(len(test_runs), 2, result.stdout)
            for item in test_runs:
                self.assertTrue(item["workerRoot"], result.stdout)
                self.assertIn("scripts", item["pythonPath"], result.stdout)
            self.assertTrue(
                any("--cov=vps_ssh_launcher" in item["command"] for item in test_runs),
                result.stdout,
            )
            # The receipts this run rewrote must carry the whole-test count
            # parsed from the stub summary line ("1 passed in 0.1s") next to
            # the lane bookkeeping, so suite size stays an observable.
            recorded = json.loads((receipts / "test.json").read_text(encoding="utf-8"))
            self.assertEqual(recorded["workers"], 2, result.stdout)
            self.assertEqual(recorded["tests"], 1, result.stdout)
            proof = json.loads(
                (receipts / "parallel-proof.json").read_text(encoding="utf-8")
            )
            self.assertEqual(proof["tests"], 1, result.stdout)

    def test_auto_profile_fails_closed_when_git_is_unavailable(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")
        repo_root = Path(__file__).resolve().parents[1]
        helper = r"""
function Initialize-WindowsProcessEnvironment {}
function Resolve-ProjectPython {
  return @{ Exe = "Invoke-GateProbe"; IsIsolated = $true; Source = "fixture" }
}
"""
        command = r"""
$failed = $false
$message = ""
try {
  & $env:VPS_GATE_RUNNER -Profile Auto
} catch {
  $failed = $true
  $message = $_.Exception.Message
}
@{
  failed = $failed
  message = $message
} | ConvertTo-Json -Compress
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            (scripts / "lib").mkdir(parents=True)
            (root / "tests").mkdir()
            runner = scripts / "run_gates.ps1"
            runner.write_bytes((repo_root / "scripts/run_gates.ps1").read_bytes())
            (scripts / "lib/project_environment.ps1").write_text(
                helper, encoding="utf-8"
            )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            # Platform-native stub: a .cmd file is only resolvable on Windows,
            # where it won this test's CI run; on Linux the runner must find
            # an executable `git` shim or the real git answers and the probe
            # reports a clean tree instead of the failure this test plants.
            if os.name == "nt":
                (fake_bin / "git.cmd").write_text(
                    "@echo off\r\nexit /b 17\r\n",
                    encoding="ascii",
                )
            else:
                stub = fake_bin / "git"
                stub.write_text("#!/bin/sh\nexit 17\n", encoding="utf-8")
                stub.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "VPS_GATE_RUNNER": str(runner),
                    "PATH": str(fake_bin) + os.pathsep + env["PATH"],
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
            self.assertTrue(report["failed"], result.stdout)
            self.assertIn("could not inspect", report["message"])

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

    def test_capture_has_a_bounded_dual_stream_wait_and_preserves_newlines(
        self,
    ) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "capture.py"
            entry.write_text(
                "import sys\n"
                "sys.stdout.write('out-1\\r\\nout-2\\n')\n"
                "sys.stderr.write('err-1\\r\\nerr-2\\n')\n",
                encoding="utf-8",
            )
            library = repo_root / "scripts/lib/project_environment.ps1"
            python = shutil.which("python")
            if python is None:
                self.skipTest("python executable is not available")
            command = (
                f". '{library}'; "
                f"$r = Invoke-LauncherPython -Python @{{ Exe = '{python}'; Args = @() }} "
                f"-ProjectRoot '{root}' -EntryPointPath '{entry}' -CaptureOutput "
                "-LauncherArgs @(); $r | ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                [powershell, "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(report["Lines"], ["out-1", "out-2"])
            self.assertEqual(report["StandardErrorLines"], ["err-1", "err-2"])

    def test_apply_is_opt_in_for_every_projected_shell_lane(self) -> None:
        # The registry is DERIVED from scripts/*.ps1, never hand-listed: the
        # hand-written table named 14 lanes while 16 scripts rendered the
        # __APPLY__ marker, so two lanes were silently outside every lane-wide
        # write-safety contract. A new lane is now covered the moment it is
        # added, and a lane that drops the marker fails here.
        lanes = self.projected_lane_scripts()
        self.assertGreaterEqual(len(lanes), 15)
        for lane in sorted(lanes):
            with self.subTest(lane=lane):
                self.assert_apply_is_opt_in(
                    _lane_script(repo_root(), lane).read_text(encoding="utf-8"),
                    lane=lane,
                )

    def test_projected_lane_registry_matches_the_tree(self) -> None:
        # The derivation is the contract: every scripts/*.ps1 that renders an
        # -Apply switch into a remote payload must appear in it, and nothing
        # else may pretend to be one.
        scripts = repo_root() / "scripts"
        expected = {
            path.stem
            for path in scripts.glob("*.ps1")
            if 'APPLY="__APPLY__"' in path.read_text(encoding="utf-8")
        }
        self.assertEqual(self.projected_lane_scripts(), expected)
        self.assertTrue(expected, "no projected -Apply lane was found at all")

    def test_local_apply_gate_still_guards_the_frp_install(self) -> None:
        # frp_relay -Setup runs a real install with no remote payload marker, so
        # its -Apply gate lives on the host side and must stay reachable only
        # under the explicit switch.
        source = (repo_root() / "scripts" / "frp_relay.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$Apply", source)
        # The dispatch is the gate: the install branch is only reachable from
        # inside the -Apply arm of the Setup parameter set.
        dispatch = source[source.index("if ($Setup) {") :]
        self.assertLess(
            dispatch.index("if ($Apply) {"),
            dispatch.index("Install-FrpSetup"),
            "the install must stay behind the -Apply gate",
        )

    def test_every_apply_lane_serializes_writes_and_verifies_rollback(self) -> None:
        for lane in sorted(self.projected_lane_scripts()):
            if lane in self.LANE_WRITE_SAFETY_EXEMPT:
                continue
            with self.subTest(lane=lane):
                self.assert_lane_write_safety(
                    _lane_script(repo_root(), lane).read_text(encoding="utf-8")
                )

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
$global:stubExitCode = 0
function Invoke-LauncherPython {
  param($Python, $ProjectRoot, $LauncherArgs, [switch]$CaptureOutput)
  $global:captured += , @($LauncherArgs)
  if ($CaptureOutput) {
    if ($global:stubExitCode -eq 0) {
      return @{
        ExitCode = 0
        Lines = "captured-out"
        StandardErrorLines = @()
      }
    }
    return @{
      ExitCode = $global:stubExitCode
      Lines = "captured-out"
      StandardErrorLines = @()
    }
  }
  return $global:stubExitCode
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
$bigPayload = 4 * [int][Math]::Ceiling($big.Length / 3)
$bigOuter = [int][Math]::Ceiling(($bigPayload + 900) * 4 / 3) + 80
if ($bigOuter -le 24000) { throw "oversized fixture unexpectedly fits source budget" }
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command $big -RemoteTempPrefix "vasma-kernel"
$c4 = $global:captured
if ($c4.Count -ne 5) { throw "oversized transfer should use staged invocations, got $($c4.Count)" }
$createCommand = $c4[0][-1]
if ($createCommand -notlike "umask 077; : > '/tmp/vasma-kernel-*.b64'; chmod 600 '/tmp/vasma-kernel-*.b64'") {
  throw "staged create missing: $createCommand"
}
$stagedPath = [regex]::Match($createCommand, "'(/tmp/vasma-kernel-[A-Za-z0-9]+\.b64)'").Groups[1].Value
if (-not $stagedPath) { throw "staged path missing: $createCommand" }
$stagedB64 = ""
for ($chunkIndex = 1; $chunkIndex -le 3; $chunkIndex++) {
  $chunkCommand = $c4[$chunkIndex][-1]
  if ($chunkCommand -notmatch "^printf %s '([A-Za-z0-9+/=]+)' >> '$([regex]::Escape($stagedPath))'$") {
    throw "staged chunk $chunkIndex missing: $chunkCommand"
  }
  $stagedB64 += $Matches[1]
}
$finalCommand = $c4[4][-1]
if ($finalCommand -notlike "test `"`$(wc -c < '$stagedPath')`" -eq $($stagedB64.Length) || { rm -f -- '$stagedPath'; exit 21; }; set -o pipefail; base64 -d -- '$stagedPath' | bash; rc=`$?; rm -f -- '$stagedPath'; exit `$rc") {
  throw "staged commit missing: $finalCommand"
}
$stagedDecoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($stagedB64))
if ($stagedDecoded -ne $big) { throw "staged payload bytes changed" }

$global:captured = @()
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command "echo secret" -RemoteTempPrefix "sensitive" -ViaTempFile
$c5 = $global:captured
if ($c5.Count -ne 3) { throw "via-temp: expected staged create/chunk/commit, got $($c5.Count)" }
if ($c5[0][-1] -notlike "umask 077; : > '/tmp/sensitive-*.b64'; chmod 600 '/tmp/sensitive-*.b64'") {
  throw "via-temp create missing: $($c5[0][-1])"
}
$sensitivePath = [regex]::Match($c5[0][-1], "'(/tmp/sensitive-[A-Za-z0-9]+\.b64)'").Groups[1].Value
if (-not $sensitivePath) { throw "via-temp path missing" }
if ($c5[1][-1] -notmatch "^printf %s '([A-Za-z0-9+/=]+)' >> '$([regex]::Escape($sensitivePath))'$") {
  throw "via-temp chunk missing: $($c5[1][-1])"
}
$sensitiveB64 = $Matches[1]
if ($c5[2][-1] -notlike "test `"`$(wc -c < '$sensitivePath')`" -eq $($sensitiveB64.Length) || { rm -f -- '$sensitivePath'; exit 21; }; set -o pipefail; base64 -d -- '$sensitivePath' | bash; rc=`$?; rm -f -- '$sensitivePath'; exit `$rc") {
  throw "via-temp commit missing: $($c5[2][-1])"
}
$sensitiveDecoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($sensitiveB64))
if ($sensitiveDecoded -ne "echo secret") { throw "via-temp payload bytes changed" }
$argvFast = @($c5 | Where-Object { $_[-1] -match "\| base64 -d \| bash$" })
if ($argvFast.Count -ne 0) { throw "via-temp must not use the argv fast path" }

$global:captured = @()
$global:stubExitCode = 0
$cap = Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command "echo probe" -CaptureOutput
if ($cap.ExitCode -ne 0) { throw "capture exit: $($cap.ExitCode)" }
if (@($cap.Lines).Count -ne 1 -or @($cap.Lines)[0] -ne "captured-out") { throw "capture lines: $($cap.Lines -join ',')" }
$headCap = ($global:captured[0][0..4] -join " ")
if ($headCap -ne "--config C:\cfg.json --profile bwg --strict-host-key-checking") { throw "capture head: $headCap" }
# Capture mode must return a non-zero remote exit instead of throwing, so
# wrappers keep lane-specific codes (e.g. 75 busy) intact.
$global:stubExitCode = 7
$cap7 = Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command "echo probe7" -CaptureOutput
if ($cap7.ExitCode -ne 7) { throw "capture exit fidelity: $($cap7.ExitCode)" }

$global:captured = @()
$global:stubExitCode = 0
$capTemp = Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command $big -RemoteTempPrefix "capture-temp" -CaptureOutput
if ($capTemp.ExitCode -ne 0) { throw "capture temp exit: $($capTemp.ExitCode)" }
if ($global:captured.Count -ne 5) { throw "capture temp invocations: $($global:captured.Count)" }
if ($global:captured[0][-1] -notlike "umask 077; : > '/tmp/capture-temp-*.b64'; chmod 600 '/tmp/capture-temp-*.b64'") {
  throw "capture create missing"
}
for ($chunkIndex = 1; $chunkIndex -le 3; $chunkIndex++) {
  if ($global:captured[$chunkIndex][-1] -notlike "printf %s * >> '/tmp/capture-temp-*.b64'") {
    throw "capture chunk $chunkIndex missing"
  }
}
if ($global:captured[4][-1] -notlike "test `"`$(wc -c < '/tmp/capture-temp-*.b64')`" -eq 26668 || { rm -f -- '/tmp/capture-temp-*.b64'; exit 21; }; set -o pipefail; base64 -d -- '/tmp/capture-temp-*.b64' | bash; rc=`$?; rm -f -- '/tmp/capture-temp-*.b64'; exit `$rc") {
  throw "capture commit missing"
}
$global:captured = @()
$global:stubExitCode = 0
$framedScript = "echo framed-ok" + [Environment]::NewLine + "# " + ("f" * 500) + [Environment]::NewLine + "exit 0"
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command $framedScript -RemoteTempPrefix "framed-check"
$framedPayload = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($framedScript)
).Length
$framedOuter = [int](($framedPayload + 900) * 4 / 3) + 80
if ($framedOuter -gt 24000) {
  if ($global:captured.Count -ne 5) { throw "oversized framed transfer should stage" }
} else {
  if ($global:captured.Count -ne 1) { throw "bounded transfer should use one invocation" }
  $framedInvocation = $global:captured[0][-1]
  if (-not $framedInvocation.StartsWith("printf %s ")) {
    throw "bounded transfer lost the framed payload"
  }
}

# Framed-band regression case: a payload whose outer base64 exceeds the
# 12000-char argv fast path once rode a single-invocation "framed" receiver
# that never executed it (bash block-buffers a piped-in script stream) and
# silently fell through to the staged path. The framing is gone; the band
# must now go straight to the staged mode-600 transfer, exactly once.
$global:captured = @()
$windowScript = "echo framed-window`n# " + ("f" * 9000)
$windowPayload = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($windowScript)
).Length
$windowOuter = [int](($windowPayload + 900) * 4 / 3) + 80
if ($windowPayload -le 12000 -or $windowOuter -gt 24000) {
  throw "window fixture missed the framed band: len=$windowPayload outer=$windowOuter"
}
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command $windowScript -RemoteTempPrefix "framed-window"
if ($global:captured.Count -ne 4) { throw "window band must stage 4 invocations, got $($global:captured.Count)" }
$windowCreate = $global:captured[0][-1]
if ($windowCreate -notlike "umask 077; : > '/tmp/framed-window-*.b64'; chmod 600 '/tmp/framed-window-*.b64'") {
  throw "window staged create missing: $windowCreate"
}
for ($chunkIndex = 1; $chunkIndex -le 2; $chunkIndex++) {
  if ($global:captured[$chunkIndex][-1] -notlike "printf %s * >> '/tmp/framed-window-*.b64'") {
    throw "window staged chunk $chunkIndex missing"
  }
}
if ($global:captured[3][-1] -notlike "test `"`$(wc -c < '/tmp/framed-window-*.b64')`" -eq $($windowPayload) || { rm -f -- '/tmp/framed-window-*.b64'; exit 21; }; set -o pipefail; base64 -d -- '/tmp/framed-window-*.b64' | bash; rc=`$?; rm -f -- '/tmp/framed-window-*.b64'; exit `$rc") {
  throw "window staged commit missing"
}

$global:captured = @()
$global:stdinSnapshot = $null
function Invoke-LauncherPython {
  param(
    [hashtable]$Python,
    [string]$ProjectRoot,
    [string]$EntryPointPath = "",
    [string[]]$LauncherArgs,
    [switch]$CaptureOutput
  )
  $global:captured += ,@($LauncherArgs)
  $idx = [Array]::IndexOf($LauncherArgs, "--stdin-file")
  if ($idx -ge 0 -and ($idx + 1) -lt $LauncherArgs.Count) {
    $global:stdinSnapshot = [IO.File]::ReadAllBytes($LauncherArgs[$idx + 1])
  }
  if ($CaptureOutput) {
    return @{ ExitCode = $global:stubExitCode; Lines = @("captured-out") }
  }
  return $global:stubExitCode
}
$streamScript = ("echo stream`r`n" * 1500)
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $RepoRoot -Config C:\cfg.json -Profile bwg -Command $streamScript -StreamStdin
if ($global:captured.Count -ne 1) { throw "StreamStdin must invoke once, got $($global:captured.Count)" }
if ($global:captured[0] -notcontains "--stdin-file") { throw "StreamStdin missing --stdin-file" }
$stdinIdx = [Array]::IndexOf($global:captured[0], "--stdin-file")
$tempGzPath = $global:captured[0][$stdinIdx + 1]
if (Test-Path -LiteralPath $tempGzPath) { throw "StreamStdin leaked temp file: $tempGzPath" }
$ms = [IO.MemoryStream]::new($global:stdinSnapshot)
$gz = [IO.Compression.GzipStream]::new($ms, [IO.Compression.CompressionMode]::Decompress)
$sr = [IO.StreamReader]::new($gz, [Text.Encoding]::UTF8)
$roundTrip = $sr.ReadToEnd()
$sr.Dispose(); $gz.Dispose(); $ms.Dispose()
if ($roundTrip -ne $streamScript.Replace("`r`n", "`n")) { throw "StreamStdin gzip round-trip mismatch" }
$global:stubExitCode = 0
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

    def test_capture_mode_keeps_stderr_out_of_stdout_fields(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is not available")
        if os.name != "nt":
            self.skipTest("Windows PowerShell capture-path fixture")
        repo_root = Path(__file__).resolve().parents[1]
        probe = r"""
param([Parameter(Mandatory = $true)][string]$RepoRoot)
. (Join-Path $RepoRoot "scripts\lib\project_environment.ps1")
$windowsPs = [IO.Path]::Combine(
  [Environment]::GetFolderPath([Environment+SpecialFolder]::System),
  "WindowsPowerShell",
  "v1.0",
  "powershell.exe"
)
if (-not (Test-Path -LiteralPath $windowsPs -PathType Leaf)) {
  throw "Windows PowerShell fixture runtime missing"
}
$captureRoot = $env:PYTEST_CAPTURE_ROOT
if ([string]::IsNullOrWhiteSpace($captureRoot)) { throw "capture fixture root missing" }
$fixture = @'
param()
[Console]::Out.WriteLine("ok")
[Console]::Error.WriteLine("warn")
'@
[IO.File]::WriteAllText(
  (Join-Path $captureRoot "capture_fixture.ps1"),
  $fixture,
  [Text.UTF8Encoding]::new($false)
)
$fixturePath = Join-Path $captureRoot "capture_fixture.ps1"
$py = @{ Exe = $windowsPs; Args = @(); IsIsolated = $true; Source = "fixture" }
$capture = Invoke-LauncherPython -Python $py -ProjectRoot $RepoRoot `
  -EntryPointPath $fixturePath -CaptureOutput -LauncherArgs @("capture")
if ($capture -isnot [hashtable]) {
  throw "capture result type: $($capture.GetType().FullName)"
}
$direct = & $windowsPs -NoProfile -ExecutionPolicy Bypass -File $fixturePath
if ($capture.ExitCode -ne 0) {
  throw "capture exit $($capture.ExitCode): $($capture.StandardErrorLines -join '|')"
}
$captureLines = [string[]]@($capture.Lines)
$captureErrorLines = [string[]]@($capture.StandardErrorLines)
if (($direct -join "`n") -ne "ok") { throw "fixture direct stdout: $($direct -join '|')" }
if (($captureLines -join "`n") -ne "ok") { throw "stdout merged: $($captureLines -join '|')" }
if (($captureErrorLines -join "`n") -ne "warn") { throw "stderr missing: $($captureErrorLines -join '|')" }
Write-Output "CAPTURE_STREAMS_OK"
"""
        with tempfile.TemporaryDirectory() as directory:
            probe_path = Path(directory) / "capture_probe.ps1"
            probe_path.write_text(probe, encoding="utf-8", newline="\n")
            env = dict(os.environ)
            env["PYTEST_CAPTURE_ROOT"] = directory
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
                env=env,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            (completed.stdout or "") + (completed.stderr or ""),
        )
        self.assertIn("CAPTURE_STREAMS_OK", completed.stdout)

    def test_focused_gate_runs_only_explicit_python_targets(self) -> None:
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
& $env:VPS_GATE_RUNNER -Profile Focused `
  -FocusPath tests\test_config.py,tests\script_validation_support.py,vps_ssh_launcher\config.py
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
            (tests / "script_validation_support.py").write_text("", encoding="utf-8")
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
        # A support module widens the pytest leg to the tests tree; source
        # files still stay on the three static legs (pytest would otherwise
        # collect the source file, find no tests and exit 5).
        self.assertIn("tests", [path.replace("\\", "/") for path in commands[0]])
        self.assertNotIn("tests/test_config.py", commands[0])
        for command in commands[1:]:
            self.assertNotEqual(command[:2], ["-m", "pytest"])
            self.assertEqual(
                sorted(
                    path.replace("\\", "/") for path in command if path.endswith(".py")
                ),
                [
                    "tests/script_validation_support.py",
                    "tests/test_config.py",
                    "vps_ssh_launcher/config.py",
                ],
            )

    def test_focused_gate_skips_pytest_leg_for_source_only_targets(self) -> None:
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
& $env:VPS_GATE_RUNNER -Profile Focused `
  -FocusPath vps_ssh_launcher\config.py
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
        # Source-only focus: no pytest leg at all, three static legs.
        self.assertEqual(len(commands), 3, commands)
        for command in commands:
            self.assertNotEqual(command[:2], ["-m", "pytest"])
            self.assertEqual(
                sorted(
                    path.replace("\\", "/") for path in command if path.endswith(".py")
                ),
                ["vps_ssh_launcher/config.py"],
            )

    def test_referenced_outputs_paths_are_tracked(self) -> None:
        # outputs/ is gitignored wholesale while tracked transaction scripts
        # stay tracked, so a reference to an untracked outputs file passes
        # locally and breaks fresh checkouts with FileNotFoundError (CI
        # 2026-11-13: deploy-cpa-request-guard-20261113.ps1). Anything a
        # current document or test references must be tracked.
        repo_root = Path(__file__).resolve().parents[1]
        pattern = re.compile(r"outputs/[A-Za-z0-9._/-]+")
        sources = [
            (repo_root / "README.md").read_text(encoding="utf-8"),
            (repo_root / "README.en.md").read_text(encoding="utf-8"),
        ]
        sources.extend(
            path.read_text(encoding="utf-8")
            for path in sorted((repo_root / "docs" / "runbooks").glob("*.md"))
        )
        sources.extend(
            path.read_text(encoding="utf-8")
            for path in sorted((repo_root / "tests").glob("*.py"))
        )
        tracked = set(
            subprocess.run(
                ["git", "-C", str(repo_root), "ls-files", "outputs/"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            ).stdout.splitlines()
        )
        referenced = {
            match.group(0).rstrip(".")
            for source in sources
            for match in pattern.finditer(source)
        }
        # Glob stems (`outputs/deploy-cpa-*.sh`) and template mentions
        # (`outputs/doctor-<date>.txt`) end mid-name; the same rule lets
        # check_docs.py skip them in fenced blocks. doctor-latest.txt is a
        # runtime-generated artifact (check_docs.py OPTIONAL_PATHS). Gate
        # receipts and worker scratch space are also machine-local by contract;
        # they are deliberately ignored by the content hash and never need a
        # tracked checkout path.
        referenced = {
            path
            for path in referenced
            if (
                not path.endswith("-")
                and path != "outputs/doctor-latest.txt"
                and not path.startswith("outputs/gates/")
            )
        }
        missing = sorted(referenced - tracked)
        self.assertEqual(
            missing,
            [],
            "current docs/tests reference outputs files that git does not track",
        )
        self.assertTrue(tracked, "the tracked outputs contract set must not be empty")

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
            "config.py": {"contracts", "windows_acl"},
            "execution.py": {"contracts"},
            "connection.py": {"contracts", "config", "socks5", "windows_acl"},
            "socks5.py": set(),
            "batch.py": {"contracts", "config", "connection", "execution"},
            "safe_files.py": set(),
            "windows_acl.py": {"windows_ctypes"},
            "windows_ctypes.py": set(),
            "platform_utils.py": set(),
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

        # Bidirectional closure: the table may not keep ghost entries for
        # deleted modules, otherwise the layering contract silently drifts
        # from the actual module set when a feature is removed.
        governed = {
            path.name
            for path in package_dir.glob("*.py")
            if path.name not in exempt_files and path.name not in entry_files
        }
        self.assertEqual(
            set(capability_allowed),
            governed,
            "layering contract lists a module that no longer exists; drop "
            "ghost entries when deleting a package module",
        )

        # Subpackage governance, discovered from the tree instead of hardcoded
        # to maintenance/ so a future second subpackage cannot grow outside the
        # contract. Same rule as today: sibling imports are free, the parent
        # direction must not reach an entrypoint, in relative or absolute form.
        subpackages = sorted(
            path
            for path in {p.parent for p in package_dir.rglob("__init__.py")}
            if path != package_dir
        )
        self.assertTrue(
            subpackages,
            "no subpackage discovered; the layering contract expects at least "
            "one, otherwise this loop is silently vacuous",
        )
        forbidden_absolute = {f"vps_ssh_launcher.{name[:-3]}" for name in entry_files}
        for subpackage in subpackages:
            for path in sorted(subpackage.glob("*.py")):
                if path.name == "__init__.py":
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                label = "/".join(path.relative_to(package_dir).parts)
                # 同层 sibling import（level 1）不受限；父包方向不得依赖入口层。
                names = package_modules_imported(
                    tree, level=len(path.relative_to(package_dir).parts)
                )
                with self.subTest(module=label):
                    self.assertEqual(names & {"cli", "maintenance_cli"}, set())
                    # Subpackage implementation modules must never import an
                    # entrypoint back. Cover absolute imports too: the
                    # relative check above intentionally sees only relative
                    # syntax and would otherwise allow
                    # ``from vps_ssh_launcher.maintenance_cli import main``.
                    absolute_names: set[str] = set()
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            absolute_names.update(alias.name for alias in node.names)
                        elif isinstance(node, ast.ImportFrom) and node.level == 0:
                            if node.module:
                                absolute_names.add(node.module)
                    self.assertEqual(absolute_names & forbidden_absolute, set())

    def test_auto_profile_maps_a_diff_to_full_focused_skip_or_no_changes(
        self,
    ) -> None:
        # -Profile Auto picks the gate from the working tree and the unpushed
        # commits, so a mistake in that decision either skips a runtime change
        # or runs a needless full gate. There was no coverage for it when its
        # doc-only branch was written in the wrong scope and crashed on every
        # documentation-only diff; this pins every outcome, including the
        # cheap clean-tree result and the committed-but-unpushed trap.
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
        cases: tuple[tuple[dict[str, str], str, str, dict[str, str]], ...] = (
            # (worktree files, expected legs, description, files to commit)
            ({}, "no_changes", "clean tree", {}),
            ({"vps_ssh_launcher/config.py": "pass\n"}, "full", "runtime surface", {}),
            ({"docs/runbook.md": "# doc\n"}, "skip", "documentation only", {}),
            ({"tests/test_config.py": ""}, "focused", "test file only", {}),
            ({"notes.md": "x\n"}, "full", "unclassified path", {}),
            # A clean tree that still carries an unpushed commit is not the
            # cheap no-op: the 2026-11-25 closeout watched Auto report PASS
            # on exactly that state without running anything.
            (
                {},
                "full",
                "runtime change hidden in an unpushed commit",
                {"vps_ssh_launcher/config.py": "pass\n"},
            ),
            (
                {},
                "skip",
                "doc-only change hidden in an unpushed commit",
                {"docs/runbook.md": "# doc\n"},
            ),
        )
        for files, kind, description, committed in cases:
            with self.subTest(case=description):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    scripts = root / "scripts"
                    (scripts / "lib").mkdir(parents=True)
                    (root / "tests").mkdir()
                    (root / "vps_ssh_launcher").mkdir()
                    # Auto decides from the diff, so the fixture is committed
                    # first and the change is made afterwards. An empty diff
                    # must remain the cheap no-changes path.
                    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
                    (root / "vps_ssh_launcher").mkdir(exist_ok=True)
                    runner = scripts / "run_gates.ps1"
                    runner.write_text(
                        (repo_root / "scripts/run_gates.ps1").read_text(
                            encoding="utf-8"
                        ),
                        encoding="utf-8",
                    )
                    (scripts / "lib/project_environment.ps1").write_text(
                        helper, encoding="utf-8"
                    )
                    (scripts / "check_docs.py").write_text(
                        "print('DOCS_CHECK=PASS')\n", encoding="utf-8"
                    )
                    env = os.environ.copy()
                    env.update(
                        {
                            "VPS_GATE_RUNNER": str(runner),
                            "PSModulePath": os.environ.get("PSModulePath", ""),
                        }
                    )
                    # A git repository is required: Auto reads the diff.
                    # The branch name is pinned because the upstream setup
                    # below names it.
                    subprocess.run(
                        ["git", "init", "-q", "-b", "main"],
                        cwd=root,
                        check=True,
                        timeout=30,
                    )
                    subprocess.run(
                        ["git", "add", "-A"], cwd=root, check=True, timeout=30
                    )
                    subprocess.run(
                        [
                            "git",
                            "-c",
                            "user.email=gate@test",
                            "-c",
                            "user.name=gate",
                            "commit",
                            "-qm",
                            "base",
                        ],
                        cwd=root,
                        check=True,
                        timeout=30,
                    )
                    # Auto also classifies unpushed commits, so give the
                    # fixture a remote-tracking upstream at the base commit.
                    # The repo is its own remote: fetching copies refs/heads
                    # into refs/remotes/origin under the standard refspec,
                    # which is what makes --set-upstream-to resolvable.
                    # Without an upstream (bare init, detached HEAD) the
                    # runner must keep the working-tree-only decision.
                    subprocess.run(
                        ["git", "remote", "add", "origin", str(root)],
                        cwd=root,
                        check=True,
                        timeout=30,
                    )
                    subprocess.run(
                        ["git", "fetch", "-q", "origin", "main"],
                        cwd=root,
                        check=True,
                        timeout=30,
                    )
                    subprocess.run(
                        ["git", "branch", "--set-upstream-to", "origin/main"],
                        cwd=root,
                        check=True,
                        capture_output=True,
                        timeout=30,
                    )
                    # The actual change Auto must classify.
                    for name, body in files.items():
                        target = root / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(body, encoding="utf-8")
                    for name, body in committed.items():
                        target = root / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(body, encoding="utf-8")
                    if committed:
                        subprocess.run(
                            ["git", "add", "-A"], cwd=root, check=True, timeout=30
                        )
                        subprocess.run(
                            [
                                "git",
                                "-c",
                                "user.email=gate@test",
                                "-c",
                                "user.name=gate",
                                "commit",
                                "-qm",
                                "case",
                            ],
                            cwd=root,
                            check=True,
                            timeout=30,
                        )
                    command = (
                        "$global:observedCommands = @(); "
                        "& $env:VPS_GATE_RUNNER -Profile Auto; "
                        "@{ commands = $global:observedCommands } | "
                        "ConvertTo-Json -Compress -Depth 4"
                    )
                    completed = subprocess.run(
                        [powershell, "-NoProfile", "-Command", command],
                        env=env,
                        cwd=root,
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
                    report = json.loads(completed.stdout.splitlines()[-1])
                    commands = report["commands"]
                    if kind == "no_changes":
                        self.assertEqual(commands, [], commands)
                        self.assertIn("no working-tree change", completed.stdout)
                    elif kind == "skip":
                        # Only the document checks run.
                        self.assertEqual(len(commands), 1, commands)
                        self.assertIn("check_docs.py", " ".join(commands[0]))
                    elif kind == "focused":
                        self.assertTrue(
                            any("pytest" in " ".join(step) for step in commands),
                            commands,
                        )
                    else:
                        self.assertTrue(
                            any("pytest" in " ".join(step) for step in commands),
                            commands,
                        )
                        self.assertTrue(
                            any("bandit" in " ".join(step) for step in commands),
                            commands,
                        )

    def test_powershell_entrypoints_reuse_shared_environment_helper(self) -> None:
        # Derive the requirement from the script body instead of a hand-kept
        # list: a new wrapper that calls into the shared helpers is covered the
        # moment it is added, and a deleted wrapper leaves no ghost entry.
        # Only the shared library may define these functions.
        repo_root = Path(__file__).resolve().parents[1]
        shared_helpers = (
            "Resolve-ProjectPython",
            "Initialize-WindowsProcessEnvironment",
            "Resolve-LauncherConfigPath",
            "Resolve-LauncherUserDataPath",
            "Get-LauncherUserDataRoot",
            "Invoke-LauncherRemoteCommand",
        )

        script_paths = [
            repo_root / "connect.ps1",
            *sorted((repo_root / "scripts").rglob("*.ps1")),
        ]
        consumers = 0
        for script_path in script_paths:
            text = script_path.read_text(encoding="utf-8")
            if script_path.name == "project_environment.ps1":
                continue
            with self.subTest(script=script_path.name):
                for helper in shared_helpers:
                    self.assertNotIn(f"function {helper}", text)
                if any(f"{helper} " in text for helper in shared_helpers):
                    consumers += 1
                    self.assertIn(
                        "project_environment.ps1",
                        text,
                        f"{script_path.name} calls a shared helper without "
                        "dot-sourcing scripts/lib/project_environment.ps1",
                    )
        # Guard the derivation itself: if the call pattern stopped matching,
        # every script would trivially pass and this contract would be vacuous.
        self.assertGreaterEqual(consumers, 9)

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
        install_text = steps["Install dependencies"]["run"]
        self.assertIn(
            "python -m pip install --require-hashes --requirement requirements-dev.lock",
            install_text,
        )
        self.assertIn(
            "python -m pip install --no-deps --no-build-isolation -e .",
            install_text,
        )
        self.assertEqual(
            set(workflow["on"]["workflow_dispatch"]["inputs"]), {"integration_profile"}
        )

    def test_ci_workflow_runs_both_gate_scopes_and_the_dependency_audit(
        self,
    ) -> None:
        # Behaviour contract only: CI must keep exercising both gate scopes and
        # the dependency audit. The previous version also pinned the matrix
        # length, the paths-ignore list and the absence of compileall, so any
        # cosmetic workflow edit failed here without any behaviour changing.
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows/ci.yml"
        data = load_yaml(workflow.read_text(encoding="utf-8"))
        runs = "\n".join(
            step.get("run", "")
            for job in data["jobs"].values()
            for step in job["steps"]
        )
        self.assertIn("-FullScope Static", runs)
        self.assertIn("-FullScope Runtime", runs)
        self.assertIn("-RunDependencyAudit", runs)
        self.assertIn("-RunParallel", runs)
        self.assertGreaterEqual(runs.count("requirements-dev.lock"), 2)
        self.assertNotIn('pip install -e ".[dev]"', runs)
        runtimes = [
            job
            for job in data["jobs"].values()
            if any("-FullScope Runtime" in step.get("run", "") for step in job["steps"])
        ]
        # The runtime gate keeps one full suite per OS and a focused
        # interpreter-compatibility smoke for the newer Python.
        self.assertGreaterEqual(
            len(runtimes[0]["strategy"]["matrix"]["include"]), 2, runtimes[0]
        )
        includes = runtimes[0]["strategy"]["matrix"]["include"]
        self.assertEqual(
            {(item["python-version"], item["gate"]) for item in includes},
            {("3.11", "full"), ("3.13", "smoke")},
        )
        full_runs = [
            step.get("run", "")
            for step in runtimes[0]["steps"]
            if step.get("if") == "matrix.gate == 'full'"
        ]
        smoke_runs = [
            step.get("run", "")
            for step in runtimes[0]["steps"]
            if step.get("if") == "matrix.gate == 'smoke'"
        ]
        self.assertTrue(any("-FullScope Runtime" in run for run in full_runs))
        self.assertTrue(any("-RunParallel" in run for run in full_runs))
        self.assertTrue(any("-Profile Focused" in run for run in smoke_runs))
        self.assertTrue(
            any(
                "tests/test_config.py" in run and "tests/test_ssh_tool.py" in run
                for run in smoke_runs
            )
        )

    def test_parallel_lane_contract_exposes_explicit_proof_switch(self) -> None:
        # Execution behavior is covered by the disposable runner fixtures
        # above. Keep only the public switch contract here so equivalent
        # implementation refactors do not require a long list of source
        # fragments to remain byte-for-byte stable.
        runner = (repo_root() / "scripts/run_gates.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$ProveParallel", runner)
        self.assertIn("if (-not $ProveParallel)", runner)

    def test_gate_ids_stay_on_the_reviewed_catalog(self) -> None:
        # d863abf removed the old covers-profiles contract, and the Id set grew
        # from 14 to 18 while nothing anchored it. This restores only the
        # set-level review point: command lines stay free (the profiles really
        # execute them on every Full run), but adding, renaming, or retiring a
        # leg must update REVIEWED_GATE_IDS on purpose.
        runner = (repo_root() / "scripts/run_gates.ps1").read_text(encoding="utf-8")
        found = set(re.findall(r'\bId\s*=\s*"([^"]+)"', runner))
        self.assertEqual(found, set(REVIEWED_GATE_IDS))
        for retired in ("unittest", "pyright", "vulture"):
            self.assertFalse(
                any(retired in gate_id for gate_id in found),
                f"retired tool {retired!r} must not come back as a gate Id",
            )

    def test_receipts_are_keyed_on_content_not_on_host_state(self) -> None:
        # A receipt is only evidence for the tree it was produced from, so the
        # key is a content hash and the gate has to compute it from inputs.
        hasher = (repo_root() / "scripts/gate_content_hash.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def tree_hash(", hasher)
        self.assertIn("def environment_hash(", hasher)
        self.assertIn("sys.version_info", hasher)
        self.assertIn("sys.platform", hasher)
        self.assertIn("cannot inspect gate input directory", hasher)
        # The receipt lives under outputs/, which the hash must ignore:
        # otherwise writing the receipt invalidates the evidence it records.
        self.assertIn('"outputs"', hasher)
        self.assertIn("IGNORED_PARTS", hasher)

    def test_parallel_workers_keep_the_private_fixture_bridge(self) -> None:
        # The CI runner emulation is not a host fact: ci.yml runs the suite in
        # one worktree, so a worker that skipped the bridge would fail the very
        # tests that check the private target contract -- and it did, on Linux,
        # before this was pinned. Per-worker TEMP is what makes running there
        # safe, not skipping it.
        conftest = (repo_root() / "tests/conftest.py").read_text(encoding="utf-8")
        self.assertIn('VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP") == "1"', conftest)
        self.assertNotIn(
            "PYTEST_XDIST_WORKER",
            conftest.split("def _private_temp_root")[1].split(
                "def _secure_fixture_tree"
            )[0],
        )

    def test_gate_helper_scripts_are_importable(self) -> None:
        # Both helpers are run by the gate itself. A syntax error in them would
        # only surface on the next Full run, after the suite already spent
        # minutes proving everything else.
        for name in ("gate_content_hash.py", "dsh_gate_xdist_worker_prelude.py"):
            with self.subTest(script=name):
                path = repo_root() / "scripts" / name
                source = path.read_text(encoding="utf-8")
                compile(source, str(path), "exec")
                self.assertIn("from __future__ import annotations", source)

    def test_both_readmes_document_the_same_modules(self) -> None:
        # README.md and README.en.md carry parallel module matrices. check_docs.py
        # only proves links resolve, so a row added to one and not the other used
        # to stay invisible. Compare the module tokens in table rows only: prose
        # is allowed to differ.
        repo_root = Path(__file__).resolve().parents[1]
        token = re.compile(r"`((?:vps_ssh_launcher|scripts)/[A-Za-z0-9._/*-]+)`")

        def modules(name: str) -> set[str]:
            text = (repo_root / name).read_text(encoding="utf-8")
            return {
                found
                for line in text.splitlines()
                if line.startswith("|")
                for found in token.findall(line)
            }

        chinese = modules("README.md")
        english = modules("README.en.md")
        self.assertTrue(chinese)
        self.assertEqual(sorted(chinese - english), [])
        self.assertEqual(sorted(english - chinese), [])

    def test_maintenance_control_plane_modules_stay_separated(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli_path = repo_root / "vps_ssh_launcher" / "maintenance_cli.py"
        commands_path = repo_root / "vps_ssh_launcher" / "maintenance" / "commands.py"
        text = cli_path.read_text(encoding="utf-8")
        commands = commands_path.read_text(encoding="utf-8")
        for forbidden in (
            "vps_ssh_launcher.maintenance_cli",
            "from .. import cli",
            "from ..cli import",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, commands)
        self.assertIn("from .maintenance.commands import", text)
        self.assertIn("def policy_from_args(", commands)
        self.assertIn("def target_config_from_args(", commands)
        self.assertIn("def require_integration_opt_in(", commands)

    def test_local_audit_entrypoints_run_as_files_and_modules(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        commands = (
            ["scripts/cockpit_provider_health.py", "--help"],
            ["scripts/cockpit_provider_prune.py", "--help"],
            ["scripts/cockpit_model_catalog_prune.py", "--help"],
            ["scripts/cockpit_model_catalog_context.py", "--help"],
            ["scripts/cpa_natural_traffic_audit.py", "--help"],
            ["-m", "scripts.cockpit_provider_health", "--help"],
            ["-m", "scripts.cockpit_provider_prune", "--help"],
            ["-m", "scripts.cockpit_model_catalog_prune", "--help"],
            ["-m", "scripts.cockpit_model_catalog_context", "--help"],
            ["-m", "scripts.cpa_natural_traffic_audit", "--help"],
        )
        for arguments in commands:
            with self.subTest(arguments=arguments):
                # File mode needs an explicit repository bootstrap because a
                # generic interpreter only adds scripts/ to sys.path on POSIX.
                result = subprocess.run(
                    [sys.executable, *arguments],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())

    def test_windows_acl_module_imports_without_windows_ctypes_names(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        # Linux CPython does not expose either name. Removing them in a
        # subprocess models that import contract without changing this Windows
        # test process or invoking the native DACL entrypoint.
        code = (
            "import ctypes; "
            "ctypes.WinDLL = None; ctypes.wintypes = None; "
            "from vps_ssh_launcher import cli, config, maintenance_cli; "
            "assert ctypes.WinDLL is None; "
            "print('CROSS_PLATFORM_IMPORT_OK')"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root)
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
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
        self.assertIn("CROSS_PLATFORM_IMPORT_OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()

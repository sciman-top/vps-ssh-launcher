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
        # A new lane otherwise silently inherits the copy-pasted -Apply shape
        # only by convention. Check the actual renderer and shell boundary once.
        repo_root = Path(__file__).resolve().parents[1]
        lanes = (
            "cdn_fallback_lane",
            "cdn_origin_cert_sync",
            "zz_subscription_republish",
            "merged_subscription_lane",
            "reality_alt_sni_lane",
            "xhttp_nginx_front",
            "nginx_tls_stealth",
            "hysteria2_masquerade_lane",
            "hysteria2_obfs_lane",
            "reality_shortid_harden",
        )
        for lane in lanes:
            with self.subTest(lane=lane):
                self.assert_apply_is_opt_in(
                    (repo_root / "scripts" / f"{lane}.ps1").read_text(encoding="utf-8")
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
  -FocusPath tests\test_config.py,vps_ssh_launcher\config.py
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
        # The pytest leg carries only explicit test files; source files stay
        # on the three static legs (pytest would otherwise collect the source
        # file, find no tests and exit 5).
        self.assertEqual(
            [Path(path).as_posix() for path in commands[0][3:]],
            ["tests/test_config.py"],
        )
        for command in commands[1:]:
            self.assertNotEqual(command[:2], ["-m", "pytest"])
            self.assertEqual(
                sorted(
                    Path(path).as_posix() for path in command if path.endswith(".py")
                ),
                ["tests/test_config.py", "vps_ssh_launcher/config.py"],
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
                [Path(path).as_posix() for path in command if path.endswith(".py")],
                ["vps_ssh_launcher/config.py"],
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
            list((repo_root / "tests").glob("*_test.py")),
            [],
            "pytest collects test_*.py; name test modules accordingly",
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
            "config.py": {"contracts", "windows_acl"},
            "execution.py": {"contracts"},
            "connection.py": {"contracts", "config", "socks5"},
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
        for event in ("push", "pull_request"):
            paths = docs_workflow["on"][event]["paths"]
            self.assertIn("scripts/check_docs.py", paths)
            self.assertIn("tests/test_ci_meta.py", paths)
        self.assertTrue(any("git diff --check" in run for run in docs_runs))
        self.assertIn("python scripts/check_docs.py", docs_runs)

    def test_full_format_scope_covers_remote_projection_sources(self) -> None:
        text = (
            Path(__file__).resolve().parents[1] / "scripts/run_gates.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('"Static", "Format"', text)
        self.assertIn('$FullScope -eq "Format"', text)
        self.assertIn(
            'ruff", "format", "--check") + $pythonTargets + $scriptTargets',
            text,
        )
        self.assertIn("Resolve-IntegrationProfile", text)

    def test_full_static_scope_checks_platform_and_import_boundaries(self) -> None:
        text = (
            Path(__file__).resolve().parents[1] / "scripts/run_gates.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('$platform = if ($IsLinux) { "linux" } else { "win32" }', text)
        self.assertIn('"MYPY_PLATFORM"', text)
        self.assertIn("$env:MYPY_PLATFORM = if (", text)
        self.assertIn('"test-" + $platform', text)
        self.assertIn(
            '$env:APPDATA,\n        "vps-ssh-launcher",\n        $platform,', text
        )
        self.assertIn('".config",\n      "vps-ssh-launcher",\n      $platform,', text)
        self.assertIn(
            '$scriptTopLevelTargets = @(\n    Get-ChildItem -LiteralPath (Join-Path $repoRoot "scripts") -Filter "*.py" -File',
            text,
        )
        self.assertIn(
            '@{ Id = "type:mypy"; Command = @($pythonExe, "-m", "mypy", "--explicit-package-bases") + $pythonTargets + $scriptTopLevelTargets',
            text,
        )

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

    def test_local_audit_adapter_does_not_enter_remote_runtime(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        scripts = repo_root / "scripts"
        for entrypoint in (
            "cockpit_provider_health.py",
            "cpa_natural_traffic_audit.py",
        ):
            with self.subTest(entrypoint=entrypoint):
                text = (scripts / entrypoint).read_text(encoding="utf-8")
                self.assertIn("from scripts.audit_cli import", text)
        for remote in (scripts / "remote").glob("*.py"):
            with self.subTest(remote=remote.name):
                text = remote.read_text(encoding="utf-8")
                self.assertNotIn("scripts.audit_cli", text)
                self.assertNotIn("vps_ssh_launcher.platform_utils", text)

    def test_private_test_temp_bridge_is_explicit_and_production_neutral(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        conftest = (repo_root / "tests/conftest.py").read_text(encoding="utf-8")
        self.assertIn("VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP", conftest)
        self.assertIn("_apply_private_fixture_bridge()", conftest)
        self.assertIn("stat.S_IRWXU", conftest)
        runner = (repo_root / "scripts/run_gates.ps1").read_text(encoding="utf-8")
        self.assertIn('"VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP"', runner)
        self.assertIn(
            '$path -replace "/", [IO.Path]::DirectorySeparatorChar',
            runner,
        )

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

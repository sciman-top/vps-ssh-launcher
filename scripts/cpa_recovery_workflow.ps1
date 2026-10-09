#requires -Version 7
<##
.SYNOPSIS
  Unified read-only/explicit-apply workflow for CPA capacity, 429, and slow paths.

.DESCRIPTION
  Keeps diagnosis, projection, reload-required state, and controlled replay as
  separate evidence layers.  Default Audit is read-only.  Remote projection is
  only allowed with -ApplyRemote; this script never stops Cockpit or API-bearing
  processes. OAuth recovery requires an explicit external recovery confirmation.
##>
param(
  [ValidateSet("Triage", "Audit", "Project", "Verify", "ControlledReplay", "WaitCapSimulation", "RecoverAfterReset")]
  [string]$Mode = "Audit",
  [string]$SidecarCandidatePath = "",
  [switch]$ApplyRemote,
  [switch]$SkipRemote,
  [string]$DoctorOutput = "",
  [switch]$QuotaResetConfirmed,
  [switch]$CooldownResetConfirmed,
  [ValidateSet("gpt-6.1-sol", "gpt-6-luna", "gpt-5.6-luna")]
  [string]$RecoveryModel = "gpt-6.1-sol",
  [ValidateRange(0.01, 720)]
  [double]$Hours = 4
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  $python = (Get-Command python).Source
}
$sidecar = Join-Path $PSScriptRoot "cockpit_sidecar_guardrails.ps1"
$providerHealth = Join-Path $PSScriptRoot "cockpit_provider_health.py"
$triage = Join-Path $PSScriptRoot "cpa_failure_triage.py"
$replay = Join-Path $PSScriptRoot "cockpit_non_oauth_replay.py"
$waitCap = Join-Path $PSScriptRoot "cockpit_gate_wait_cap_check.py"
$cpa = Join-Path $PSScriptRoot "cpa_bwg_guardrails.ps1"

function Invoke-Checked {
  param([string]$File, [string[]]$Arguments)
  & $File @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $File"
  }
}

function Invoke-Doctor {
  param([switch]$Apply)
  $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $cpa, "-Profile", "bwg")
  if ($Apply) { $args += "-Apply" }
  if ([string]::IsNullOrWhiteSpace($DoctorOutput)) {
    Invoke-Checked "pwsh" $args
    return
  }
  $parent = Split-Path -Parent (Resolve-Path -LiteralPath $DoctorOutput -ErrorAction SilentlyContinue)
  if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  & pwsh @args 2>&1 | Tee-Object -FilePath $DoctorOutput
  if ($LASTEXITCODE -ne 0) { throw "CPA doctor/apply failed with exit code $LASTEXITCODE" }
}

function Invoke-Triage {
  $args = @($triage, "--hours", "$Hours")
  if (-not [string]::IsNullOrWhiteSpace($DoctorOutput) -and
      (Test-Path -LiteralPath $DoctorOutput -PathType Leaf)) {
    $args += @("--doctor", $DoctorOutput)
  }
  Invoke-Checked $python $args
}

function Get-ProviderHealth {
  # The sidecar state gate only applies when Desktop actually targets the
  # local 10909 provider gateway. In public_gateway mode the selected target
  # is fq.sciman.top and 10909/14185 being absent is the expected state. Keep
  # the mode decision tied to the same read-only provider health tool that
  # validates the selected catalog and key/endpoint relationship; never infer
  # it from a listener alone.
  # The health tool deliberately returns exit 1 for findings.  Keep its JSON
  # report instead of treating that diagnostic result as a transport failure:
  # Audit must still reach the remote doctor and triage so one stale desktop
  # target cannot hide the BWG admission state.  Stderr is redirected to a
  # short-lived file so it cannot contaminate the machine-readable JSON and is
  # never printed (provider diagnostics are redaction-first).
  $stderrPath = Join-Path ([IO.Path]::GetTempPath()) (
    "cpa-provider-health-$PID-$([guid]::NewGuid().ToString('N')).err"
  )
  try {
    $raw = & $python $providerHealth "--json" 2> $stderrPath
    $exitCode = $LASTEXITCODE
    $text = ($raw -join "`n").Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
      throw "Cockpit provider health returned no JSON (exit code $exitCode)"
    }
    try {
      $report = $text | ConvertFrom-Json
    } catch {
      throw "Cockpit provider health returned invalid JSON"
    }
    return [pscustomobject]@{
      report = $report
      exitCode = $exitCode
    }
  } finally {
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
  }
}

Write-Output "WORKFLOW_MODE=$Mode"
Write-Output "EVIDENCE_ORDER=repo_verified,filesystem_projected,host_loaded,controlled_live_replay,natural_live_accepted"

if ($Mode -eq "RecoverAfterReset") {
  $confirmationCount = [int]$QuotaResetConfirmed.IsPresent + [int]$CooldownResetConfirmed.IsPresent
  if ($confirmationCount -ne 1) {
    throw "RecoverAfterReset requires exactly one external recovery confirmation: -QuotaResetConfirmed or -CooldownResetConfirmed."
  }
  if ($SkipRemote -or $ApplyRemote) {
    throw "RecoverAfterReset requires BWG access and does not accept -ApplyRemote."
  }
  $remote = @'
python3 - '__RECOVERY_MODEL__' <<'PY'
import datetime
import importlib.util
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location(
    "admission_recovery", "/opt/cliproxyapi/cpa-admission.py"
)
admission = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = admission
spec.loader.exec_module(admission)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def state():
    with opener.open("http://127.0.0.1:8318/healthz", timeout=5) as response:
        return json.load(response)["lanes"]["chatgpt-oauth"]["state"]

try:
    before = state()
    print("RECOVERY_UTC=" + datetime.datetime.now(datetime.timezone.utc).isoformat())
    print("COOLDOWN_BEFORE=" + str(before["cooldown_remaining"]))
    if before["inflight"] or before["pending"] or before["half_open_probe"]:
        print("RECOVERY_RESULT=LANE_BUSY")
        raise SystemExit(14)
    if not before.get("cooldown_active", before["cooldown_remaining"]):
        print("RECOVERY_RESULT=NOT_REQUIRED")
        raise SystemExit(0)
    generation = before.get("failure_generation")
    if type(generation) is not int:
        print("RECOVERY_RESULT=RUNTIME_UPGRADE_REQUIRED")
        raise SystemExit(20)
    config = yaml.safe_load(Path("/opt/cliproxyapi/config.yaml").read_text())
    payload = {
        "model": sys.argv[1],
        "reason": "__RECOVERY_REASON__",
        "expected_generation": generation,
    }
    request = urllib.request.Request(
        "http://127.0.0.1:8318/admin/recover-after-reset",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + config["api-keys"][0],
            "Content-Type": "application/json",
        },
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=120) as response:
            status, body = response.status, response.read(262145)
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read(262145)
    print("RECOVERY_HTTP_STATUS=" + str(status))
    print("RECOVERY_SECONDS=" + str(round(time.monotonic() - started, 3)))
    completed = status == 200 and admission.completed_reset_probe(body)
    deadline = time.monotonic() + 2
    after = state()
    while after["half_open_probe"] and time.monotonic() < deadline:
        time.sleep(0.05)
        after = state()
    print("COMPLETED_WITH_EXPECTED_OUTPUT=" + str(completed).lower())
    print("COOLDOWN_AFTER=" + str(after["cooldown_remaining"]))
    recovered = (
        completed
        and not after["cooldown_remaining"]
        and not after["failure_streak"]
        and not after["half_open_probe"]
    )
    print("RECOVERY_RESULT=" + ("RECOVERED" if recovered else "NOT_RECOVERED"))
    raise SystemExit(0 if recovered else 10)
except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
    print("RECOVERY_RESULT=UNVERIFIED class=" + type(error).__name__)
    raise SystemExit(10)
PY
'@
  $recoveryReason = if ($CooldownResetConfirmed) { "cooldown_reset" } else { "quota_reset" }
  $remote = $remote.Replace("__RECOVERY_MODEL__", $RecoveryModel)
  $remote = $remote.Replace("__RECOVERY_REASON__", $recoveryReason)
  & (Join-Path $repoRoot "connect.ps1") -Profile bwg -StrictHostKeyChecking `
    -Command $remote -CommandTimeout 140 -CommandHardTimeout 160
  exit $LASTEXITCODE
}

if ($Mode -eq "Triage") {
  Invoke-Triage
  exit 0
}

if ($Mode -in @("Audit", "Project", "Verify")) {
  $providerCheckFailed = $false
  if ($Mode -eq "Project") {
    if ([string]::IsNullOrWhiteSpace($SidecarCandidatePath) -and -not $ApplyRemote) {
      throw "Project requires -SidecarCandidatePath and/or -ApplyRemote."
    }
    if (-not [string]::IsNullOrWhiteSpace($SidecarCandidatePath)) {
      Invoke-Checked "pwsh" @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $sidecar,
        "-Mode", "Project", "-CandidatePath", $SidecarCandidatePath
      )
    }
  } else {
    $providerHealthResult = Get-ProviderHealth
    $provider = $providerHealthResult.report
    $providerHealthExitCode = [int]$providerHealthResult.exitCode
    $providerCheckFailed = $providerHealthExitCode -ne 0
    $targetUri = $null
    Write-Output "COCKPIT_PROVIDER_HEALTH_EXIT=$providerHealthExitCode"
    if ($null -eq $provider -or [string]::IsNullOrWhiteSpace([string]$provider.configTarget)) {
      # A missing top-level model_provider/base_url is a real desktop
      # acceptance gap, but it must not prevent the remote admission doctor
      # from running.  Keep this state explicitly unresolved and fail the
      # workflow only after all read-only evidence has been collected.
      Write-Output "COCKPIT_GATEWAY_MODE=unknown"
      Write-Output "COCKPIT_PROVIDER_VERIFY=UNRESOLVED"
      Write-Output "COCKPIT_PROVIDER_RULE=Set and reload the intended Desktop provider target before Verify"
      $providerCheckFailed = $true
    } else {
      try {
        $targetUri = [Uri]$provider.configTarget
      } catch {
        Write-Output "COCKPIT_GATEWAY_MODE=unknown"
        Write-Output "COCKPIT_PROVIDER_VERIFY=UNRESOLVED"
        Write-Output "COCKPIT_PROVIDER_RULE=configTarget is not a valid URI"
        $providerCheckFailed = $true
      }
    }
    if ($null -ne $targetUri) {
      if ($targetUri.Host -eq "fq.sciman.top") {
        Write-Output "COCKPIT_GATEWAY_MODE=public_gateway"
        if ($providerHealthExitCode -eq 0) {
          Write-Output "COCKPIT_PROVIDER_VERIFY=PASS"
        } else {
          Write-Output "COCKPIT_PROVIDER_VERIFY=FINDINGS"
        }
        Write-Output "COCKPIT_SIDECAR_VERIFY=SKIPPED_PUBLIC_GATEWAY"
        Write-Output "COCKPIT_SIDECAR_RULE=10909/14185 are optional in public_gateway mode"
      } elseif ($targetUri.Host -in @("127.0.0.1", "localhost", "::1")) {
        Write-Output "COCKPIT_GATEWAY_MODE=local_gateway"
        Invoke-Checked "pwsh" @(
          "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $sidecar,
          "-Mode", $Mode
        )
      } else {
        Write-Output "COCKPIT_GATEWAY_MODE=other"
        Write-Output "COCKPIT_PROVIDER_VERIFY=UNRESOLVED"
        Write-Output "COCKPIT_PROVIDER_RULE=Only fq.sciman.top or loopback gateway targets have a verified contract"
        $providerCheckFailed = $true
      }
    }
  }

  if (-not $SkipRemote) {
    Invoke-Doctor -Apply:$ApplyRemote
  }
  Invoke-Triage
  if ($Mode -eq "Project") {
    Write-Output "RELOAD_REQUIRED=1"
    Write-Output "RELOAD_RULE=Use Cockpit formal reload/start path; do not taskkill or stop API-bearing processes."
  }
  if ($Mode -ne "Project" -and $providerCheckFailed) {
    Write-Output "WORKFLOW_RESULT=FINDINGS"
    exit 1
  }
  Write-Output "WORKFLOW_RESULT=PASS"
  exit 0
}

if ($Mode -eq "ControlledReplay") {
  Invoke-Checked $python @($replay)
  exit 0
}

if ($Mode -eq "WaitCapSimulation") {
  # This uses a loopback stub and scratch sidecar only.  It never reaches CPA
  # or an OAuth provider and proves the local 45 s rejection cap behaviour.
  Invoke-Checked $python @($waitCap, "--expect-cap-s", "45")
  exit 0
}

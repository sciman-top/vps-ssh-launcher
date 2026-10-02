#requires -Version 7
<##
.SYNOPSIS
  Unified read-only/explicit-apply workflow for CPA capacity, 429, and slow paths.

.DESCRIPTION
  Keeps diagnosis, projection, reload-required state, and controlled replay as
  separate evidence layers.  Default Audit is read-only.  Remote projection is
  only allowed with -ApplyRemote; this script never stops Cockpit or API-bearing
  processes and never sends OAuth replay requests.
##>
param(
  [ValidateSet("Triage", "Audit", "Project", "Verify", "ControlledReplay", "WaitCapSimulation")]
  [string]$Mode = "Audit",
  [string]$SidecarCandidatePath = "",
  [switch]$ApplyRemote,
  [switch]$SkipRemote,
  [string]$DoctorOutput = "",
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

Write-Output "WORKFLOW_MODE=$Mode"
Write-Output "EVIDENCE_ORDER=repo_verified,filesystem_projected,host_loaded,controlled_live_replay,natural_live_accepted"

if ($Mode -eq "Triage") {
  Invoke-Triage
  exit 0
}

if ($Mode -in @("Audit", "Project", "Verify")) {
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
    Invoke-Checked "pwsh" @(
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $sidecar,
      "-Mode", $Mode
    )
  }

  if (-not $SkipRemote) {
    Invoke-Doctor -Apply:$ApplyRemote
  }
  Invoke-Triage
  if ($Mode -eq "Project") {
    Write-Output "RELOAD_REQUIRED=1"
    Write-Output "RELOAD_RULE=Use Cockpit formal reload/start path; do not taskkill or stop API-bearing processes."
  }
  if ($Mode -eq "Verify") {
    Invoke-Checked "pwsh" @(
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $sidecar,
      "-Mode", "Verify"
    )
  }
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

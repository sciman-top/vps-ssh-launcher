#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [string]$TargetConfig,
  [ValidatePattern('^[A-Za-z0-9_.-]+$')]
  [string]$Profile = "bwg",
  [string]$OutputDirectory,
  [switch]$RunIntegration,
  [switch]$Apply,
  [switch]$RemoteWrite,
  [switch]$AutoApply,
  [switch]$VerifyHealth,
  [switch]$LaneAcceptance
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

function Resolve-MaintenancePolicyPath {
  param([string]$Value)

  if ($Value) {
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Value)
  }
  if (-not $env:APPDATA) {
    throw "APPDATA is unavailable; pass -Config explicitly."
  }
  return Join-Path $env:APPDATA "vps-ssh-launcher\maintenance.toml"
}

function Resolve-MaintenanceTargetPath {
  param([string]$Value)

  if ($Value) {
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Value)
  }
  if (-not $env:APPDATA) {
    throw "APPDATA is unavailable; pass -TargetConfig explicitly."
  }
  return Join-Path $env:APPDATA "vps-ssh-launcher\target.json"
}

function Invoke-VpsMaintenanceCli {
  param(
    [Parameter(Mandatory = $true)]
    [hashtable]$Python,
    [Parameter(Mandatory = $true)]
    [string[]]$Arguments,
    [Parameter(Mandatory = $true)]
    [string]$LogPath
  )

  $output = @(& $Python.Exe @($Python.Args + @(
        "-m",
        "vps_ssh_launcher.maintenance_cli"
      ) + $Arguments) 2>&1)
  $exitCode = $LASTEXITCODE
  $output | ForEach-Object {
    $line = [string]$_
    Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8NoBOM
    [Console]::WriteLine($line)
  }
  return $exitCode
}

if ($Apply -and $AutoApply) {
  throw "-Apply and -AutoApply are mutually exclusive."
}
if ($AutoApply -and $RemoteWrite) {
  throw "-AutoApply cannot be combined with -RemoteWrite; it supplies the policy-gated remote-write boundary."
}
if ($Apply -and -not $RemoteWrite) {
  throw "-Apply requires -RemoteWrite; default execution is dry-run only."
}
if ($RemoteWrite -and -not $Apply) {
  throw "-RemoteWrite requires -Apply."
}
if (-not $RunIntegration) {
  throw "Fresh maintenance runs require -RunIntegration."
}
if ($LaneAcceptance -and $Profile -ne "bwg") {
  throw "-LaneAcceptance is restricted to bwg."
}

# A vasma reinstall or a menu reconfigure silently restores the stock REALITY
# shortIds, decoy page, nginx stealth, XHTTP front and Hysteria2 masquerade
# while every service stays active and every subscription keeps working. The
# read-only drift gate is the only thing that notices, so every maintenance
# pass runs it instead of waiting for the next manual audit. Both checks are
# read-only: the drift gate takes no lock and restarts nothing, and the
# acceptance lane only performs client handshakes from the host itself.
function Invoke-ProxyLaneChecks {
  param(
    [Parameter(Mandatory = $true)][string]$Profile,
    [Parameter(Mandatory = $true)][string]$TargetPath,
    [Parameter(Mandatory = $true)][string]$LogPath,
    [Parameter(Mandatory = $true)][string]$ScriptRoot,
    [switch]$WithAcceptance
  )

  $writeLog = {
    param($Lines)
    $Lines | ForEach-Object {
      Add-Content -LiteralPath $LogPath -Value ([string]$_) -Encoding utf8NoBOM
      [Console]::WriteLine([string]$_)
    }
  }

  $driftOutput = @(& pwsh -NoProfile -ExecutionPolicy Bypass `
      -File (Join-Path $ScriptRoot "proxy_hardening_drift.ps1") `
      -Profile $Profile -Config $TargetPath 2>&1)
  $driftExit = $LASTEXITCODE
  & $writeLog $driftOutput
  $driftVerdict = @($driftOutput | Where-Object { ([string]$_).StartsWith("HARDENING_DRIFT=") })
  if ($driftExit -ne 0) {
    $detail = if ($driftVerdict.Count -gt 0) { $driftVerdict[-1] } else { "HARDENING_DRIFT=unknown" }
    throw "Proxy hardening drift on ${Profile} ($detail); re-run the named lanes. See $LogPath"
  }

  if (-not $WithAcceptance) {
    return
  }

  $acceptArguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $ScriptRoot "proxy_lane_acceptance.ps1"),
    "-Profile", $Profile, "-Config", $TargetPath
  )
  # The obfs lane stays out of the vasma subscription, so its password lives
  # beside target.json instead of in the task arguments.
  $obfsPasswordFile = Join-Path (Split-Path -Parent $TargetPath) "$Profile-hysteria2-obfs-password.txt"
  if (Test-Path -LiteralPath $obfsPasswordFile -PathType Leaf) {
    $acceptArguments += @("-ObfsPasswordFile", $obfsPasswordFile)
  }
  $acceptOutput = @(& pwsh @acceptArguments 2>&1)
  $acceptExit = $LASTEXITCODE
  & $writeLog $acceptOutput
  $acceptVerdict = @($acceptOutput | Where-Object { ([string]$_).StartsWith("ACCEPTANCE_RESULT=") })
  $verdict = if ($acceptVerdict.Count -gt 0) { [string]$acceptVerdict[-1] } else { "ACCEPTANCE_RESULT=unknown" }
  if ($acceptExit -ne 0 -or $verdict -ne "ACCEPTANCE_RESULT=PASS") {
    throw "Proxy lane acceptance was not PASS on ${Profile} ($verdict). See $LogPath"
  }
}

Initialize-WindowsProcessEnvironment
$policyPath = Resolve-MaintenancePolicyPath -Value $Config
$targetPath = Resolve-MaintenanceTargetPath -Value $TargetConfig
if (-not (Test-Path -LiteralPath $policyPath -PathType Leaf)) {
  throw "Maintenance policy not found: $policyPath"
}
if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
  throw "Target config not found: $targetPath"
}

$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$runsRoot = if ($OutputDirectory) {
  $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
} elseif ($env:LOCALAPPDATA) {
  Join-Path $env:LOCALAPPDATA "vps-ssh-launcher\maintenance-runs"
} else {
  Join-Path $repoRoot ".maintenance-runs"
}
New-Item -ItemType Directory -Path $runsRoot -Force | Out-Null
$runId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$logPath = Join-Path $runsRoot "$runId-$Profile.log"
$planPath = Join-Path $runsRoot "$runId-$Profile-plan.json"

$oldIntegration = [Environment]::GetEnvironmentVariable(
  "VPS_SSH_LAUNCHER_RUN_INTEGRATION",
  "Process"
)
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
try {
  Push-Location $repoRoot
  try {
    $planArgs = @(
      "--config", $policyPath,
      "--json",
      "plan",
      "--live-inventory",
      "--run-integration",
      "--target-config", $targetPath,
      "--profile", $Profile,
      "--output", $planPath
    )
    $planExit = Invoke-VpsMaintenanceCli -Python $python -Arguments $planArgs -LogPath $logPath
    if ($planExit -ne 0) {
      throw "Maintenance plan was not admissible; exit code $planExit. See $logPath"
    }

    if ($Apply -or $AutoApply) {
      $reviewedPlan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
      if ([string]$reviewedPlan.plan_id -notmatch '^plan-[0-9a-f]{16}$') {
        throw "The generated maintenance plan has an invalid plan_id."
      }
      $applyArgs = @(
        "--config", $policyPath,
        "--json",
        "apply",
        "--plan-id", [string]$reviewedPlan.plan_id,
        "--yes",
        "--remote-write",
        "--run-integration",
        "--target-config", $targetPath,
        "--profile", $Profile
      )
      if ($AutoApply) {
        $applyArgs += "--unattended"
      }
      $applyExit = Invoke-VpsMaintenanceCli -Python $python -Arguments $applyArgs -LogPath $logPath
      if ($applyExit -ne 0) {
        throw "Maintenance apply was not verified; exit code $applyExit. See $logPath"
      }
    }
    # Existing S4U task definitions may require elevation to change. Keep the
    # daily observe path covered through its existing script entrypoint too.
    if ($VerifyHealth -or ($Profile -eq "bwg" -and -not ($Apply -or $AutoApply))) {
      if ($Profile -ne "bwg") { throw "-VerifyHealth is restricted to bwg." }
      $healthOutput = @(& pwsh -NoProfile -File (Join-Path $PSScriptRoot "cpa_bwg_guardrails.ps1") `
        -Profile bwg -Config $targetPath 2>&1)
      $healthExit = $LASTEXITCODE
      $healthOutput | ForEach-Object {
        Add-Content -LiteralPath $logPath -Value ([string]$_) -Encoding utf8NoBOM
        [Console]::WriteLine([string]$_)
      }
      if ($healthExit -ne 0) { throw "BWG strict health verification failed; exit code $healthExit. See $logPath" }
    }

    # Observe passes already carry the daily task's integration grant, so the
    # lane checks ride along; -Apply runs stay lean and verify through their
    # own apply/verify path.
    if ($LaneAcceptance -or -not ($Apply -or $AutoApply)) {
      Invoke-ProxyLaneChecks -Profile $Profile -TargetPath $targetPath -LogPath $logPath `
        -ScriptRoot $PSScriptRoot `
        -WithAcceptance:($LaneAcceptance -or ($Profile -eq "bwg" -and -not ($Apply -or $AutoApply)))
    }
  } finally {
    Pop-Location
  }
} finally {
  if ($null -eq $oldIntegration) {
    Remove-Item Env:VPS_SSH_LAUNCHER_RUN_INTEGRATION -ErrorAction SilentlyContinue
  } else {
    $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = $oldIntegration
  }
}

Write-Output "MAINTENANCE_RUN_OK profile=$Profile plan=$planPath log=$logPath apply=$($Apply.IsPresent) auto_apply=$($AutoApply.IsPresent)"

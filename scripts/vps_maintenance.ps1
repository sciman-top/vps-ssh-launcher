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
  [switch]$AutoApply
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
$runId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
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
      $applyArgs = @(
        "--config", $policyPath,
        "--json",
        "apply",
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

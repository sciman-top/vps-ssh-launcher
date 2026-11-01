#requires -Version 7
[CmdletBinding()]
param(
  [ValidateSet("bwg", "zz")]
  [string]$Profile = "zz",
  [string]$LogDirectory,
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if ($Profile -ne "zz") {
  throw "Proxy drift recovery is confined to zz; bwg remains read-only."
}

if (-not $LogDirectory) {
  $LogDirectory = if ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "vps-ssh-launcher\maintenance-runs"
  } else {
    Join-Path $repoRoot ".maintenance-runs"
  }
}
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$logPath = Join-Path $LogDirectory "drift-$Profile-$stamp.log"

# Task Scheduler discards stdout, so an unattended drift verdict would age
# silently. Preview remains read-only. -Apply first runs the same gate, then
# delegates only named drift lanes to their existing locked/backup/rollback
# entries and requires a fresh clean gate; no service is bounced on a healthy
# host. S4U registration is a separate elevated operation.
$arguments = @(
  "-NoProfile", "-ExecutionPolicy", "Bypass",
  "-File", (Join-Path $PSScriptRoot "proxy_hardening_lane_recover.ps1"),
  "-Profile", $Profile
)
if ($Apply) {
  $arguments += "-Apply"
}
$output = @()
$exitCode = 0
if (-not $Apply) {
  $driftOutput = @(& pwsh -NoProfile -ExecutionPolicy Bypass `
      -File (Join-Path $PSScriptRoot "proxy_hardening_drift.ps1") `
      -Profile $Profile 2>&1)
  $exitCode = $LASTEXITCODE
  $output = $driftOutput
} else {
  $output = @(& pwsh @arguments 2>&1)
  $exitCode = $LASTEXITCODE
}
$output | Set-Content -LiteralPath $logPath -Encoding utf8NoBOM
$output | ForEach-Object { [Console]::WriteLine([string]$_) }
Write-Output "DRIFT_LOG=$logPath"
exit $exitCode

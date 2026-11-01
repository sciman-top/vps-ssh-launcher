#requires -Version 7
[CmdletBinding()]
param(
  [ValidateSet("bwg", "zz")]
  [string]$Profile = "zz",
  [string]$LogDirectory
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

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
# silently. Capture the same read-only gate the operator runs by hand, keep
# its exit code (0 clean, 3 drift), and leave a greppable receipt.
$output = @(& pwsh -NoProfile -ExecutionPolicy Bypass `
    -File (Join-Path $PSScriptRoot "proxy_hardening_drift.ps1") `
    -Profile $Profile 2>&1)
$exitCode = $LASTEXITCODE
$output | Set-Content -LiteralPath $logPath -Encoding utf8NoBOM
$output | ForEach-Object { [Console]::WriteLine([string]$_) }
Write-Output "DRIFT_LOG=$logPath"
exit $exitCode

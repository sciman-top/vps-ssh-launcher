#requires -Version 7
# Replays the workbuddy 0.9.64-local + truncation-guard swap through the
# configured bwg profile. Backup + rollback live in the remote script.
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$sourcePath = Join-Path $PSScriptRoot "install-workbuddy-0964-trunc-20261022.sh"
$source = (Get-Content -LiteralPath $sourcePath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$command = "VPS_SSH_LAUNCHER_PROFILE='bwg'`n" + $source

Write-Host "Invoking the workbuddy 0.9.64 truncation-guard swap through the bwg profile."
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile "bwg" -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

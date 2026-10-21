#requires -Version 7
# Replays the workbuddy single-shot only through the configured bwg profile.
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$sourcePath = Join-Path $PSScriptRoot "workbuddy-single-shot-20261021.sh"
$source = (Get-Content -LiteralPath $sourcePath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$command = "VPS_SSH_LAUNCHER_PROFILE='bwg'`n" + $source

Write-Host "Invoking the workbuddy single-shot through the bwg profile."
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile "bwg" -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 420

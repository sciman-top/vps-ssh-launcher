#requires -Version 7
# Replays the v8.0.43 canary source only through the configured bwg profile.
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$sourcePath = Join-Path $PSScriptRoot "deploy-cpa-v8043-canary-20261026.sh"
$source = (Get-Content -LiteralPath $sourcePath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$command = "VPS_SSH_LAUNCHER_PROFILE='bwg'`n" + $source

Write-Host "Invoking the v8.0.43 canary through the bwg profile."
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile "bwg" -Command $command -IdleTimeoutSeconds 400 -HardTimeoutSeconds 900

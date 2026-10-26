#requires -Version 7
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$sourcePath = Join-Path $PSScriptRoot "install-workbuddy-0970-20261026.sh"
$source = (Get-Content -LiteralPath $sourcePath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$command = "VPS_SSH_LAUNCHER_PROFILE='bwg'`n" + $source

Write-Host "Invoking the WorkBuddy 0.9.70 install transaction through the bwg profile."
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile "bwg" -Command $command -IdleTimeoutSeconds 400 -HardTimeoutSeconds 900

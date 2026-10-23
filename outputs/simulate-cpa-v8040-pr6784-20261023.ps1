#requires -Version 7
# Runs the isolated v8.0.40 vs PR #6784 differential simulation through bwg.
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts\lib\project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$sourcePath = Join-Path $PSScriptRoot 'simulate-cpa-v8040-pr6784-20261023.sh'
$source = (Get-Content -LiteralPath $sourcePath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$command = "VPS_SSH_LAUNCHER_PROFILE='bwg'`n" + $source

Write-Host 'Invoking the isolated CPA PR #6784 differential simulation through the bwg profile.'
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile 'bwg' -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1800

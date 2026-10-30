#requires -Version 7
# Driver for the authorized bwg v8.0.48 host-only canary, 2026-10-30.
# The WorkBuddy 0.9.81 artifact is already the reviewed upstream v0.8.89
# build; this run stages/tests the new CPA binary, then performs the reversible
# single-host production swap with the unchanged plugin.
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('stage', 'accept', 'load', 'deploy')]
  [string]$Phase
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
switch ($Phase) {
  'stage' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'stage-v8048-20261030.sh') -Raw
    $idle = 900; $hard = 1800
  }
  'accept' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'accept-v8048-20261030.sh') -Raw
    $idle = 600; $hard = 1800
  }
  'load' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'plugin-load-v8048-20261030.sh') -Raw
    $idle = 120; $hard = 600
  }
  'deploy' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deploy-cpa-v8048-canary-20261030.sh') -Raw
    $idle = 900; $hard = 2700
  }
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds $idle -HardTimeoutSeconds $hard
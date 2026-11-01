#requires -Version 7
# Driver for the authorized bwg v8.0.49 joint canary (2026-11-01).
# Phases mirror the reviewed v8.0.48 driver. stage/accept/load are non-mutating
# w.r.t. production (isolated mount+net namespace, mock upstream). deploy is the
# reversible single-host swap and is gated on the v8.0.49 72h soak.
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
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'stage-v8049-20261101.sh') -Raw
    $idle = 900; $hard = 1800
  }
  'accept' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'accept-v8049-20261101.sh') -Raw
    $idle = 600; $hard = 1800
  }
  'load' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'plugin-load-v8049-20261101.sh') -Raw
    $idle = 120; $hard = 600
  }
  'deploy' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deploy-cpa-v8049-canary-20261101.sh') -Raw
    $idle = 900; $hard = 2700
  }
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds $idle -HardTimeoutSeconds $hard

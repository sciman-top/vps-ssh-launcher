#requires -Version 7
# Authorized bwg CPA v8.0.50 -> v8.0.51 joint canary, 2026-11-02.
# Plugin stays 0.9.84; the accept phase reuses the reviewed v8.0.48 fixture
# with the staging path pointed at the v8.0.51 tree.
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
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'stage-v8051-20261102.sh') -Raw
    $idle = 900; $hard = 1800
  }
  'accept' {
    $base = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'accept-v8048-20261030.sh') -Raw
    $command = $base.Replace(
      '/root/cpa-v8048-plugin-0981-20261030',
      '/root/cpa-v8051-plugin-0984-20261102'
    )
    $idle = 600; $hard = 1800
  }
  'load' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'plugin-load-v8051-20261102.sh') -Raw
    $idle = 120; $hard = 600
  }
  'deploy' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deploy-cpa-v8051-canary-20261102.sh') -Raw
    $idle = 600; $hard = 1800
  }
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds $idle -HardTimeoutSeconds $hard

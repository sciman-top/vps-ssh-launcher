#requires -Version 7
param(
  [string]$Profile = "bwg",
  [int]$IdleTimeoutSeconds = 420,
  [int]$HardTimeoutSeconds = 900
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$body = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "accept-modelstreak-20261010.sh")
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $body `
  -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
Write-Host "ACCEPT_DRIVER=DONE"

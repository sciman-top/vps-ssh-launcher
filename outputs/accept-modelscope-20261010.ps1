#requires -Version 7
# Thin driver for the 2026-10-10 model-scoped capacity acceptance. The remote
# payload lives in accept-modelscope-20261010.sh so the bash side keeps its own
# quoting; the transport stays in scripts/lib/project_environment.ps1.
param(
  [string]$Profile = "bwg",
  [int]$IdleTimeoutSeconds = 420,
  [int]$HardTimeoutSeconds = 900
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") {
  throw "This acceptance is restricted to the bwg profile."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$body = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "accept-modelscope-20261010.sh")

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $body `
  -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
Write-Host "ACCEPT_DRIVER=DONE"

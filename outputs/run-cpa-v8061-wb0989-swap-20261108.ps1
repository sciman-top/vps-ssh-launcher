#requires -Version 7
# Deliver the bwg CPA v8.0.59 -> v8.0.61 host swap together with the official
# cpa-multi-plugins v0.8.97 WorkBuddy plugin 0.9.88 -> 0.9.89 in a single
# drain transaction. The config baseline pin is substituted from the value
# recorded by the fresh pre-swap probe; the transaction refuses any drift.
param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ConfigSha256,
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$BinarySha256
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$payloadPath = Join-Path $PSScriptRoot 'cpa-v8061-swap-transaction-20261108.sh'
$payload = Get-Content -LiteralPath $payloadPath -Raw
if ($payload -match '__CPA_SWAP_END__') {
  throw 'Unexpected remote payload delimiter'
}
$payload = $payload.Replace('__CONFIG_SHA__', $ConfigSha256)
$payload = $payload.Replace('__NEW_BINARY_SHA__', $BinarySha256)
if ($payload -match '__CONFIG_SHA__' -or $payload -match '__NEW_BINARY_SHA__') {
  throw 'Unfilled baseline placeholder'
}
$command = @"
bash -s <<'__CPA_SWAP_END__'
$payload
__CPA_SWAP_END__
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

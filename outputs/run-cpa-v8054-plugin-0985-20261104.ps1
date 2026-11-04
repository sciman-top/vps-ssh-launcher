#requires -Version 7
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$payloadPath = Join-Path $PSScriptRoot 'cpa-v8054-plugin-0985-swap-20261104.sh'
$payload = Get-Content -LiteralPath $payloadPath -Raw
if ($payload -match '__CPA_SWAP_END__') {
  throw 'Unexpected remote payload delimiter'
}
$command = @"
bash -s <<'__CPA_SWAP_END__'
$payload
__CPA_SWAP_END__
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

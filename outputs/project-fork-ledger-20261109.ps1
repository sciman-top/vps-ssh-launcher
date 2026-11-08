#requires -Version 7
# Project the committed fork-binary ledger next to the deployment so an
# operator reading the host directly can answer "which binary should be
# running" without cloning the repo. The doctor still takes its expected
# value from the committed HEAD blob (wrapper injection); this copy is
# operator convenience and is deliberately NOT a second source of truth.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$ledger = (Get-Content -LiteralPath (Join-Path $repoRoot 'scripts/remote/fork-binary.sha256') -Raw).Trim()
if ($ledger -notmatch '^[0-9a-f]{64}$') { throw 'Local ledger is not a bare sha256.' }
$command = @'
set -Eeuo pipefail
umask 077
LEDGER=__LEDGER__
DIR=/opt/cliproxyapi
printf '%s\n' "$LEDGER" > "$DIR/fork-binary.sha256.new"
chmod 600 "$DIR/fork-binary.sha256.new"
mv -f "$DIR/fork-binary.sha256.new" "$DIR/fork-binary.sha256"
READBACK=$(cat "$DIR/fork-binary.sha256")
[ "$READBACK" = "$LEDGER" ] || { echo LEDGER_READBACK_MISMATCH; exit 10; }
LIVE=$(docker exec cli-proxy-api sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)
[ "$LIVE" = "$LEDGER" ] || { echo "LIVE_BINARY_DIFFERS_FROM_LEDGER live=$LIVE"; exit 10; }
echo "LEDGER_PROJECTED sha=$LEDGER live_matches=true"
'@
$command = $command.Replace('__LEDGER__', $ledger)
if ($command -match '__LEDGER__') { throw 'Unfilled ledger placeholder.' }
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300

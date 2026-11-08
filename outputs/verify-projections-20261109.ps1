#requires -Version 7
# Read-only verification of the projection state across BOTH entry lanes.
# Answers "is anything projected stale" without touching the host.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -uo pipefail
DIR=/opt/cliproxyapi
ADRIFT=0
check() {
  name=$1 path=$2 want=$3
  if [ ! -f "$path" ]; then
    echo "projection $name MISSING"; ADRIFT=1; return
  fi
  got=$(sha256sum "$path" | cut -d' ' -f1)
  if [ "$got" = "$want" ]; then echo "projection $name MATCH"
  else echo "projection $name MISMATCH want=$want got=$got"; ADRIFT=1; fi
}
check cpa-auto-update.sh "$DIR/auto-update.sh" 79c2af644b5b7ca4b118d94ede7d9b801b2c348d06543042179b5fab90669786
check cpa-health.py "$DIR/cpa-health.py" 418eb770a0478a3458cf21c6846e69907155c04221a6496eb143b3890b13a5c7
check cpa_policy.py "$DIR/cpa_policy.py" ad31c0f6b8e6f8c05ddb42e9af33175344bc7720ac41787a1decbaf3984cd19d
check cpa_provider_routes.json "$DIR/cpa_provider_routes.json" 5bf5dfc8c44123b7e43affa1dc6d43501c53451974a43cf2b8add61aad711056
check cpa-admission.py "$DIR/cpa-admission.py" 285b4a638be90aac0b8f413b7859ca772cfc40a7d1670215dcfc332a5b99f03b
echo "PROJECTION_DRIFT=$ADRIFT"
echo "=== entry lanes both carry the CPA capability route"
for f in /etc/nginx/conf.d/cpa-gateway.conf /etc/nginx/conf.d/subscribe.conf; do
  n=$(grep -cE 'proxy_pass http://127\.0\.0\.1:8318/v1/' "$f" 2>/dev/null || echo 0)
  echo "lane_route $(basename "$f") upstream_8318_count=$n"
done
echo "=== runtime binary vs ledger"
LIVE=$(docker exec cli-proxy-api sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)
LEDGER=$(cat "$DIR/fork-binary.sha256")
[ "$LIVE" = "$LEDGER" ] && echo "runtime_binary=MATCH $LIVE" || echo "runtime_binary=MISMATCH live=$LIVE ledger=$LEDGER"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 180 -HardTimeoutSeconds 400

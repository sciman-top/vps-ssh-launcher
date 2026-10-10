#requires -Version 7
param([string]$Profile = "bwg")
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$remote = @'
set -Eeuo pipefail
echo "== admission journal last 15min =="
journalctl -u cpa-admission --since '15 minutes ago' -o short-iso 2>/dev/null | grep -E 'lane_probe|lane_reject|upstream_result|upstream_error' | tail -n 25
echo "== CPA container markers last 15min =="
CNAME=$(docker ps --format '{{.Names}}' | grep -i -m1 cliproxy || docker ps --format '{{.Names}}' | head -1)
for kw in 'server_is_overloaded' 'at capacity' 'usage_limit_reached' 'model_not_found' 'unknown model'; do
  n=$(docker logs --since 15m "$CNAME" 2>&1 | grep -c -i "$kw" || true)
  echo "kw=$kw count=$n"
done
echo "== CPA container last error-ish lines (types only) =="
docker logs --since 15m "$CNAME" 2>&1 | grep -i -E "error|warn|overload|capacity|429|503" | tail -n 8 | cut -c1-160
echo "== healthz + catalog =="
curl -s --max-time 5 http://127.0.0.1:8318/healthz | python3 -c "
import json,sys
h = json.load(sys.stdin)
for name, lane in sorted(h.get('lanes', {}).items()):
    st = lane.get('state', {})
    print(f\"lane={name} scope={st.get('cooldown_scope')} lane_remaining={st.get('cooldown_remaining')} model_cooldowns={st.get('model_cooldowns')}\")
"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $remote -IdleTimeoutSeconds 90 -HardTimeoutSeconds 240
Write-Host "SOL_NOW_PROBE=DONE"

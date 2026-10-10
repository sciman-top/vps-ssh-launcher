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
echo "== journal since deploy restart (11:53:04Z) =="
journalctl -u cpa-admission --since '11:53:04' -o cat 2>/dev/null > /tmp/j.txt
echo "MODEL_COOLDOWN_REJECTS=$(grep -c 'reason=model_cooldown' /tmp/j.txt || true)"
echo "MODEL_PROBES=$(grep -c 'scope=model' /tmp/j.txt || true)"
echo "LANE_COOLDOWN_REJECTS=$(grep -c 'reason=cooldown' /tmp/j.txt || true)"
echo "CAPACITY_TRUE=$(grep -c 'capacity=true' /tmp/j.txt || true)"
echo "== recent model-scope lines =="
grep -E 'reason=model_cooldown|scope=model' /tmp/j.txt | tail -n 5 || echo NONE
rm -f /tmp/j.txt
echo "== healthz =="
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c "
import json,sys
h = json.load(sys.stdin)
for name, lane in sorted(h.get('lanes', {}).items()):
    s = lane.get('state', {})
    print(f\"lane={name} scope={s.get('cooldown_scope')} model_cooldowns={s.get('model_cooldowns')}\")
"
echo "PIN_MATCH=$( [ "$(sha256sum /opt/cliproxyapi/cpa-admission.py | awk '{print $1}')" = "$(cat /etc/vps-ssh-launcher/cpa-admission.sha256)" ] && echo yes || echo no )"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote
Write-Host "LIVE_PROBE=DONE"

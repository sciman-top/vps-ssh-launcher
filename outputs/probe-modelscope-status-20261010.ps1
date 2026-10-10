#requires -Version 7
# Read-only: model-scope breaker activity in the last 24h journal + live
# /healthz lane snapshot (cooldown_scope / model_cooldowns). Counts and lane
# state only; no request ids, no client text.
param(
  [string]$Profile = "bwg",
  [int]$IdleTimeoutSeconds = 90,
  [int]$HardTimeoutSeconds = 240
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$remote = @'
set -Eeuo pipefail
journalctl -u cpa-admission --since '24 hours ago' -o cat > /tmp/cpa-adm-journal.txt 2>/dev/null || true
echo "MODEL_COOLDOWN_REJECTS=$(grep -c 'reason=model_cooldown' /tmp/cpa-adm-journal.txt || true)"
echo "MODEL_PROBES=$(grep -c 'scope=model' /tmp/cpa-adm-journal.txt || true)"
echo "LANE_COOLDOWN_REJECTS=$(grep -c 'reason=cooldown' /tmp/cpa-adm-journal.txt || true)"
echo "CAPACITY_TRUE=$(grep -c 'capacity=true' /tmp/cpa-adm-journal.txt || true)"
echo "== recent model-scope lines =="
grep -E 'reason=model_cooldown|scope=model' /tmp/cpa-adm-journal.txt | tail -n 6 || echo NONE
rm -f /tmp/cpa-adm-journal.txt
echo "== healthz lane snapshot =="
curl -s --max-time 5 http://127.0.0.1:8318/healthz | python3 -c "
import json,sys
h = json.load(sys.stdin)
for name, lane in sorted(h.get('lanes', {}).items()):
    st = lane.get('state', {})
    print(f\"lane={name} scope={st.get('cooldown_scope')} lane_remaining={st.get('cooldown_remaining')} model_cooldowns={st.get('model_cooldowns')} inflight={st.get('inflight')} pending={st.get('pending')}\")
"
echo "PIN_MATCH=$( [ "$(sha256sum /opt/cliproxyapi/cpa-admission.py | awk '{print $1}')" = "$(cat /etc/vps-ssh-launcher/cpa-admission.sha256)" ] && echo yes || echo no )"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $remote `
  -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
Write-Host "MODELSCOPE_STATUS_PROBE=DONE"

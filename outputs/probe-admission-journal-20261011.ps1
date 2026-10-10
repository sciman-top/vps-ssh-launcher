#requires -Version 7
# Read-only 48h admission journal breakdown for the 2026-10-11 review:
# whether the model-scoped capacity path (model_cooldown / model_half_open)
# has fired on real traffic, per-model capacity vs success counts, healthz
# snapshot, and deployed generation identity. Aggregate counts only.
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
journalctl -u cpa-admission --since '48 hours ago' -o cat > /tmp/cpa-adm-j48.txt 2>/dev/null || true
echo "JOURNAL_LINES=$(wc -l < /tmp/cpa-adm-j48.txt)"
echo "MODEL_COOLDOWN_REJECTS=$(grep -c 'reason=model_cooldown' /tmp/cpa-adm-j48.txt || true)"
echo "MODEL_PROBES=$(grep -c 'scope=model' /tmp/cpa-adm-j48.txt || true)"
echo "LANE_COOLDOWN_REJECTS=$(grep -c 'reason=cooldown' /tmp/cpa-adm-j48.txt || true)"
echo "QUEUE_TIMEOUT_REJECTS=$(grep -c 'reason=queue_timeout' /tmp/cpa-adm-j48.txt || true)"
echo "BUSY_REJECTS=$(grep -c 'reason=busy' /tmp/cpa-adm-j48.txt || true)"
echo "UPSTREAM_RESULTS=$(grep -c 'upstream_result' /tmp/cpa-adm-j48.txt || true)"
echo "CAPACITY_TRUE=$(grep -c 'capacity=true' /tmp/cpa-adm-j48.txt || true)"
echo "== capacity=true by model/status (48h) =="
grep 'capacity=true' /tmp/cpa-adm-j48.txt | grep -o 'model=[^ ]* status=[^ ]*' | sort | uniq -c | sort -rn || true
echo "== capacity=false successes by model (48h) =="
grep 'capacity=false' /tmp/cpa-adm-j48.txt | grep -o 'model=[^ ]* status=200' | sort | uniq -c | sort -rn || true
echo "== retry_after presence on capacity=true =="
grep 'capacity=true' /tmp/cpa-adm-j48.txt | grep -o 'retry_after=[^ ]*' | sort | uniq -c || true
echo "== first/last model-scope lines =="
grep -E 'reason=model_cooldown|scope=model' /tmp/cpa-adm-j48.txt | head -n 3 || echo NONE
grep -E 'reason=model_cooldown|scope=model' /tmp/cpa-adm-j48.txt | tail -n 3 || echo NONE
echo "== healthz oauth lane snapshot =="
curl --noproxy '*' -s --max-time 5 http://127.0.0.1:8318/healthz | python3 -c "import json,sys; d=json.load(sys.stdin); lane=d['lanes']['chatgpt-oauth']; state=lane.get('state', lane); print(json.dumps({k: state.get(k) for k in ('inflight','pending','failure_streak','cooldown_active','cooldown_remaining','cooldown_scope','model_cooldowns','failure_generation')}, sort_keys=True))" || echo HEALTHZ_UNAVAILABLE
echo "== service generation =="
systemctl show -p MainPID --value cpa-admission.service
sha256sum /opt/cliproxyapi/cpa-admission.py
echo "PIN=$(cat /etc/vps-ssh-launcher/cpa-admission.sha256)"
echo "PIN_MATCH=$( [ "$(sha256sum /opt/cliproxyapi/cpa-admission.py | awk '{print $1}')" = "$(cat /etc/vps-ssh-launcher/cpa-admission.sha256)" ] && echo yes || echo no )"
echo "== cpa container =="
docker ps --filter name=cli-proxy-api --format '{{.Image}} {{.Status}}'
rm -f /tmp/cpa-adm-j48.txt
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $remote `
  -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
Write-Host "JOURNAL_PROBE=DONE"

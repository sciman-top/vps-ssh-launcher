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
D=/opt/cliproxyapi
echo "ADMISSION=$(sha256sum "$D/cpa-admission.py" | cut -d' ' -f1)"
echo "PIN=$(awk 'NF {print $1; exit}' /etc/vps-ssh-launcher/cpa-admission.sha256)"
echo "SVC=$(systemctl is-active cpa-admission.service)"
echo "PID=$(systemctl show cpa-admission.service -p MainPID --value)"
echo "SVC_SINCE=$(systemctl show cpa-admission.service -p ActiveEnterTimestamp --value)"
echo "NEW_CODE_MARK=$(grep -c 'model-correlated' "$D/cpa-admission.py" || true)"
echo "== backups =="
ls -dt /root/cpa-admission-modelstreak-backup-* 2>/dev/null | head -3 || echo NONE
echo "== rollback dry-run =="
BD=$(ls -dt /root/cpa-admission-modelstreak-backup-* 2>/dev/null | head -n 1)
[ -n "$BD" ] && bash "$BD/rollback.sh" --dry-run || echo NO_BACKUP
echo "== healthz =="
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c "
import json,sys
h = json.load(sys.stdin)
print('STATUS=' + str(h.get('status')))
for name, lane in sorted(h.get('lanes', {}).items()):
    s = lane.get('state', {})
    print('LANE', name, 'inflight=', s.get('inflight'), 'pending=', s.get('pending'),
          'scope=', s.get('cooldown_scope'), 'model_cooldowns=', s.get('model_cooldowns'))
"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote
Write-Host "DEPLOY_STATE_PROBE=DONE"

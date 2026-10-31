#requires -Version 7
# Authorized release of the workbuddy lane hold latched at 13:57:56Z.
# Signal review: the upstream 403 body is an edge "Access denied" HTML page,
# not a 11140 account-restriction JSON, and the lane completed 200s on both
# sides of it, so this is treated as a transient edge denial, not an account
# ban. Release follows the cpa-gateway.md manual recovery contract: backup,
# assert, clear hold_reason, restart, verify. Entry returns 503 during the
# window so no in-flight turn is killed by the restart.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
STATE=/var/lib/cpa-admission/workbuddy-risk.json
NGX=/etc/nginx/conf.d/cpa-gateway.conf
NGX_BK=/root/cpa-gateway.conf.pre-release-$TS
NGX_SHA_BEFORE=$(sha256sum "$NGX" | cut -d ' ' -f 1)
LOG=/opt/cliproxyapi/canary-plugin-0982.log
cp -a "$NGX" "$NGX_BK"
[ -s "$NGX_BK" ] || { echo 'ENTRY_BACKUP_FAILED'; exit 20; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

all_idle() {
  python3 - <<'PY'
import json
import urllib.request

h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
print(sum(v['state']['inflight'] for v in h['lanes'].values()))
PY
}

systemctl is-active --quiet cpa-admission || { echo 'ADMISSION_NOT_ACTIVE'; exit 1; }
BK=/root/workbuddy-risk-backup-$TS.json
cp -a "$STATE" "$BK"
stat -c '%U:%G %a' "$BK"
sha256sum "$BK"
PRE=$(cat "$STATE")
log "RELEASE_PREFLIGHT state=$PRE backup=$BK"

NGX_ON=0
ROLLBACK_DONE=0
restore_entry() {
  if [ "$NGX_ON" = 1 ] && [ -s "$NGX_BK" ]; then
    cp -a "$NGX_BK" "$NGX"
    nginx -t && nginx -s reload
    NGX_ON=0
  fi
}
rollback_state() {
  trap - ERR HUP INT TERM
  restore_entry
  # Restoring the durable backup re-latches the hold, so only do it when the
  # service did not come back: a failure after a healthy restart must not
  # undo the release.
  if [ "$ROLLBACK_DONE" = 0 ] && ! systemctl is-active --quiet cpa-admission; then
    cp -a "$BK" "$STATE"
    systemctl start cpa-admission || true
    ROLLBACK_DONE=1
    log "RELEASE_ROLLBACK restored_backup=$BK"
  fi
  exit 1
}
trap rollback_state ERR HUP INT TERM

python3 - "$NGX" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding='utf-8').split('\n')
for index, line in enumerate(lines):
    if line.startswith('    location ~') and '/v1/' in line:
        lines.insert(index + 1, '        return 503; # cpa maintenance window')
        path.write_text('\n'.join(lines), encoding='utf-8')
        raise SystemExit(0)
raise SystemExit('entry location anchor not found')
PY
nginx -t
nginx -s reload
NGX_ON=1
log 'MAINTENANCE_GATE_ON entry=503'

DRAIN_DEADLINE=$((SECONDS + 240))
while :; do
  if [ "$(all_idle)" = 0 ]; then
    log 'INFLIGHT_DRAINED'
    break
  fi
  if [ "$SECONDS" -ge "$DRAIN_DEADLINE" ]; then
    log 'DEFER in-flight did not drain within 240s'
    exit 75
  fi
  sleep 1
done

systemctl stop cpa-admission
log 'ADMISSION_STOPPED'
python3 - "$STATE" "$BK" <<'PY'
import json
import os
import sys
from pathlib import Path

state = Path(sys.argv[1])
backup = Path(sys.argv[2])
data = json.loads(state.read_text())
if not (
    data.get('version') == 1
    and data.get('not_before') == 0
    and data.get('hold_reason') == 'retry_window_review_required'
):
    raise SystemExit('HOLD_ASSERTION_FAILED state=' + json.dumps(data))
data['hold_reason'] = None
tmp = state.with_suffix('.json.release')
tmp.write_text(json.dumps(data) + '\n')
os.chown(tmp, state.stat().st_uid, state.stat().st_gid)
os.chmod(tmp, state.stat().st_mode & 0o777)
os.replace(tmp, state)
print('HOLD_CLEARED backup=' + str(backup))
PY
log 'HOLD_CLEARED reason=retry_window_review_required backup=$BK'

systemctl start cpa-admission
for _ in 1 2 3 4 5 6; do
  if curl -sf --max-time 3 http://127.0.0.1:8318/healthz >/dev/null; then
    break
  fi
  sleep 3
done
curl -sf --max-time 3 http://127.0.0.1:8318/healthz >/dev/null || {
  log 'REFUSE admission_not_ready_after_start'
  exit 24
}
log 'ADMISSION_READY'

python3 - <<'PY'
import json
import urllib.request

h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
holds = {name: lane['state']['hold_reason'] for name, lane in h['lanes'].items()}
print('HOLDS ' + json.dumps(holds, sort_keys=True))
if holds.get('workbuddy-oauth') is not None:
    raise SystemExit('workbuddy hold still present')
PY
log 'RELEASE_VERIFIED workbuddy_hold=null'

restore_entry
if ! sha256sum "$NGX" | grep -qF "$NGX_SHA_BEFORE"; then
  log "REFUSE entry_config_drift expected=$NGX_SHA_BEFORE"
  exit 23
fi
log 'MAINTENANCE_GATE_OFF entry=restored'
trap - ERR HUP INT TERM
cat "$STATE"
log "OK workbuddy hold released backup=$BK"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 600

#requires -Version 7
# User-authorized WorkBuddy 0.9.86 official -> 0.9.87 canonical UID rollout.
# Only plugins/workbuddy.so changes. The transaction gates entry traffic,
# drains all lanes under the admission request lock, backs up hashes, swaps
# the artifact atomically, restarts CPA once, and verifies or rolls back.
param(
  [switch]$DryRun,
  [switch]$ForceRestart,
  [string]$Config
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if ($ForceRestart) {
  throw '-ForceRestart is disabled; the transaction proves the drain itself.'
}
$mode = if ($DryRun) { 'dry-run' } else { 'apply' }
$command = @'
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
PLUGIN="$DIR/plugins/workbuddy.so"
OLD_SHA=04ed2703e32e3826257fbd0b78973b9583ad6f816b0b893bd555d5825df8d51d
NEW_SRC=/root/cpa-workbuddy-candidate-0987/plugins/workbuddy/workbuddy-0987-canonical.so
NEW_SHA=7093a190e906a872f7362e1bc225eaf6f0fba30388cdd9066af6d0d8636a89e4
VERSION=0.9.87
MODE='__MODE__'
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
LOG="$DIR/canary-plugin-0987-canonical-$TS.log"
NGX=/etc/nginx/conf.d/cpa-gateway.conf
NGX_SHA_BEFORE=$(sha256sum "$NGX" | cut -d ' ' -f 1)
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo UPDATE_ALREADY_RUNNING; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
docker() { timeout --signal=TERM --kill-after=15s 120s docker "$@"; }

active_state() {
  python3 - <<'PY'
import json
import urllib.request

h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
inflight = sum(int(v['state']['inflight']) for v in h['lanes'].values())
pending = sum(int(v['state']['pending']) for v in h['lanes'].values())
print(f'{inflight} {pending}')
PY
}

hold_state() {
  python3 - <<'PY'
import json
from pathlib import Path

base = Path('/var/lib/cpa-admission')
held = []
for name in ('chatgpt-risk.json', 'workbuddy-risk.json', 'antigravity-risk.json'):
    try:
        data = json.loads((base / name).read_text())
        reason = data.get('hold_reason')
    except (OSError, ValueError, AttributeError):
        raise SystemExit('HOLD_STATE_UNREADABLE file=' + name)
    if reason is not None:
        held.append(name + '=' + reason)
print('\n'.join(held))
PY
}

[ -s "$NEW_SRC" ]
[ "$(sha256sum "$NEW_SRC" | cut -d ' ' -f 1)" = "$NEW_SHA" ] || {
  echo REFUSE_NEW_PLUGIN_SHA
  exit 20
}
current_sha=$(sha256sum "$PLUGIN" | cut -d ' ' -f 1)
if [ "$current_sha" = "$NEW_SHA" ]; then
  python3 "$DIR/cpa-health.py" readiness
  echo "ALREADY_APPLIED mode=$MODE plugin=$VERSION sha=$NEW_SHA no_swap=true"
  exit 0
fi
[ "$current_sha" = "$OLD_SHA" ] || {
  echo "REFUSE_LIVE_PLUGIN_DRIFT sha=$current_sha"
  exit 20
}
read -r PRE_ACTIVE PRE_PENDING <<<"$(active_state)"
echo "PREFLIGHT old=$OLD_SHA new=$NEW_SHA active=$PRE_ACTIVE pending=$PRE_PENDING"
if [ "$MODE" = dry-run ]; then
  echo "DRY_RUN_OK old=$OLD_SHA new=$NEW_SHA active=$PRE_ACTIVE pending=$PRE_PENDING no_swap=true"
  exit 0
fi

BK="$DIR/backups/$TS-plugin-0986-to-0987-canonical"
mkdir -m 700 "$BK"
cp -a "$PLUGIN" "$DIR/compose.yml" "$DIR/config.yaml" "$BK/"
cp -a "$NGX" "$BK/cpa-gateway.conf"
find "$DIR/auth" -maxdepth 1 -type f -name '*.json' -print0 | sort -z |
  xargs -0 -r sha256sum >"$BK/auth.sha256"
log "BACKUP dir=$BK"

NGX_ORIGINAL_ACTIVE=0
ENTRY_PAUSED=0
MUTATED=0
restore_entry() {
  if [ "$ENTRY_PAUSED" = 1 ]; then
    cp -a "$NGX" "$BK/cpa-gateway.paused" || return 1
    cp -a "$BK/cpa-gateway.conf" "$NGX" || return 1
    nginx -t || return 1
    NGX_ORIGINAL_ACTIVE=1
    nginx -s reload || return 1
    ENTRY_PAUSED=0
    log MAINTENANCE_GATE_OFF
  fi
}
pause_entry() {
  if [ "$ENTRY_PAUSED" = 0 ]; then
    if [ "$NGX_ORIGINAL_ACTIVE" = 1 ]; then
      cp -a "$BK/cpa-gateway.paused" "$NGX" || return 1
      nginx -t || return 1
      nginx -s reload || return 1
    else
      cp -a "$NGX" "$BK/cpa-gateway.active" || return 1
      python3 - "$NGX" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding='utf-8').split('\n')
for index, line in enumerate(lines):
    if line.startswith('    location ~') and '/v1/' in line:
        lines.insert(index + 1, '        return 503; # cpa plugin maintenance window')
        path.write_text('\n'.join(lines), encoding='utf-8')
        raise SystemExit(0)
raise SystemExit('entry_location_anchor_missing')
PY
      nginx -t || return 1
      NGX_ORIGINAL_ACTIVE=1
      nginx -s reload || return 1
    fi
    ENTRY_PAUSED=1
  fi
}
rollback() {
  local code=${1:-$?}
  trap '' ERR HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    cp -a "$BK/workbuddy.so" "$PLUGIN.rollback" &&
      mv -f "$PLUGIN.rollback" "$PLUGIN" ||
      log "ROLLBACK_PLUGIN_FAILED backup=$BK"
    (cd "$DIR" && docker compose restart cli-proxy-api) >>"$LOG" 2>&1 ||
      log "ROLLBACK_RESTART_FAILED backup=$BK"
    python3 "$DIR/cpa-health.py" readiness ||
      log "ROLLBACK_READINESS_FAILED backup=$BK"
  fi
  if ! restore_entry; then
    log "ROLLBACK_ENTRY_FAILED backup=$BK"
  fi
  log "ROLLBACK_DONE code=$code backup=$BK"
  [ "$code" != 0 ] || code=1
  exit "$code"
}
trap rollback ERR HUP INT TERM

python3 - "$NGX" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding='utf-8').split('\n')
for index, line in enumerate(lines):
    if line.startswith('    location ~') and '/v1/' in line:
        lines.insert(index + 1, '        return 503; # cpa plugin maintenance window')
        path.write_text('\n'.join(lines), encoding='utf-8')
        raise SystemExit(0)
raise SystemExit('entry_location_anchor_missing')
PY
nginx -t
nginx -s reload
ENTRY_PAUSED=1
log MAINTENANCE_GATE_ON

exec 8>/run/cpa-admission/requests.lock
flock -w 60 -x 8 || { log DRAIN_LOCK_TIMEOUT; exit 30; }
deadline=$((SECONDS + 240))
while :; do
  read -r active pending <<<"$(active_state)"
  if [ "$active" = 0 ] && [ "$pending" = 0 ]; then
    break
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    log "DEFER_INFLIGHT active=$active pending=$pending"
    exit 75
  fi
  sleep 1
done
log DRAIN_OK
lock_handle=$(readlink "/proc/$$/fd/8")
lock_inode=$(stat -c '%d:%i' /run/cpa-admission/requests.lock)
[ -n "$lock_handle" ] && [ "$(stat -Lc '%d:%i' "/proc/$$/fd/8")" = "$lock_inode" ] ||
  { log REFUSE_DRAIN_LOCK_IDENTITY; exit 30; }

PRE_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
MUTATED=1
install -m "$(stat -c %a "$PLUGIN")" "$NEW_SRC" "$PLUGIN.new"
chown --reference="$PLUGIN" "$PLUGIN.new"
mv -f "$PLUGIN.new" "$PLUGIN"
(cd "$DIR" && docker compose restart cli-proxy-api) >>"$LOG" 2>&1
RESTART_TS=$(date -u +%FT%TZ)
log "RESTART_ISSUED plugin=$VERSION sha=$NEW_SHA"
NEW_STARTED=$PRE_STARTED
for _ in 1 2 3 4 5 6; do
  NEW_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
  [ "$NEW_STARTED" != "$PRE_STARTED" ] && break
  sleep 3
done
[ "$NEW_STARTED" != "$PRE_STARTED" ] || { log REFUSE_RESTART_NOT_APPLIED; rollback 22; }
log "RESTART_APPLIED started=$NEW_STARTED"
REGISTERED=1
for _ in 1 2 3 4 5 6 7 8; do
  if docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
     grep -q "plugin registered plugin_id=workbuddy .*version=$VERSION"; then
    REGISTERED=0
    break
  fi
  sleep 3
done
[ "$REGISTERED" = 0 ] || { log REFUSE_PLUGIN_NOT_REGISTERED; rollback 20; }
[ "$(sha256sum "$PLUGIN" | cut -d ' ' -f 1)" = "$NEW_SHA" ] ||
  { log REFUSE_PLUGIN_SHA_AFTER_SWAP; rollback 20; }
python3 - "$DIR/config.yaml" <<'PY'
import sys

import yaml

section = yaml.safe_load(open(sys.argv[1], encoding='utf-8'))['plugins']['configs']['workbuddy']
mode = section.get('scheduler_mode')
reclaim = section.get('scheduler_priority_reclaim')
if mode != 'credits' or reclaim is not True:
    raise SystemExit(f'SCHEDULER_COMPATIBILITY_FAILED mode={mode!r} reclaim={reclaim!r}')
print(f'SCHEDULER_COMPATIBILITY_OK mode={mode} priority_reclaim={str(reclaim).lower()}')
PY
python3 "$DIR/cpa-health.py" readiness || { log REFUSE_READINESS; rollback 21; }
log READINESS_OK
if ! POST_HOLDS=$(hold_state); then
  log UNVERIFIED_HOLD_STATE_UNREADABLE
  exit 10
fi
if [ -n "$POST_HOLDS" ]; then
  log "EXISTING_UNRELATED_HOLD lanes=${POST_HOLDS//$'\n'/,}"
else
  log LANE_HOLD_DELTA=none
fi
sha256sum -c "$BK/auth.sha256" >"$BK/auth-check.log" 2>&1
log AUTH_BYTES_UNCHANGED
[ "$(sha256sum "$DIR/config.yaml" | cut -d ' ' -f 1)" = "$(sha256sum "$BK/config.yaml" | cut -d ' ' -f 1)" ] ||
  { log REFUSE_PLUGIN_ONLY_VIOLATION; rollback 23; }
[ "$(sha256sum "$DIR/compose.yml" | cut -d ' ' -f 1)" = "$(sha256sum "$BK/compose.yml" | cut -d ' ' -f 1)" ] ||
  { log REFUSE_PLUGIN_ONLY_VIOLATION; rollback 23; }
log PLUGIN_REGISTERED version=$VERSION sha=$NEW_SHA
read -r POST_ACTIVE POST_PENDING <<<"$(active_state)" || true
log "POST_READY active=$POST_ACTIVE pending=$POST_PENDING"

restore_entry || rollback 25
[ "$(sha256sum "$NGX" | cut -d ' ' -f 1)" = "$NGX_SHA_BEFORE" ] ||
  { log REFUSE_ENTRY_CONFIG_DRIFT; exit 23; }
trap - ERR HUP INT TERM
exec 8>&-
docker inspect --format 'CONTAINER status={{.State.Status}} restarts={{.RestartCount}}' cli-proxy-api
log "DEPLOY_OK old=$OLD_SHA new=$NEW_SHA backup=$BK"
'@
$command = $command.Replace('__MODE__', $mode)
if ($command -match '__MODE__') {
  throw 'Unfilled WorkBuddy 0.9.87 deployment mode.'
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

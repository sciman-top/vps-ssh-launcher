#requires -Version 7
# Authorized bwg plugin-only swap: WorkBuddy 0.9.81 -> 0.9.82-local
# (exhausted-pool delegation). No image, config, credential or routing change.
#
# Drain strategy (2026-10-31): admission holds a non-blocking shared lock for
# the whole turn, so a waiting exclusive flock starves under live traffic
# (13:17 and the parallel 13:31 transaction both starved). Instead the entry
# vhost returns 503 for the maintenance window, in-flight turns finish, the
# plugin is swapped, CPA is restarted, and the entry config is restored
# byte-exact. The restart is verified by StartedAt, not by the log line.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
PLUGIN="$DIR/plugins/workbuddy.so"
OLD_SHA=36d8b72097845f472ae51e2d579185e579878a0ec564e676be3008520bedf1dc
OLD_VERSION=0.9.81
NEW_SRC=/root/cpa-workbuddy-0982-20261031/plugins/workbuddy/workbuddy.so
NEW_SHA=81f0ccf3082e878fbe3cb66bf167c377c8d1029ece86dac5b322352a9c806be6
NEW_VERSION=0.9.82
LOG="$DIR/canary-plugin-0982.log"
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
NGX=/etc/nginx/conf.d/cpa-gateway.conf
NGX_BK=/root/cpa-gateway.conf.pre-maint-$TS
NGX_SHA_BEFORE=$(sha256sum "$NGX" | cut -d ' ' -f 1)

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
docker() { timeout --signal=TERM --kill-after=15s 120s docker "$@"; }

active_inflight() {
  python3 - <<'PY'
import json
import urllib.request

h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
print(sum(v['state']['inflight'] for v in h['lanes'].values()))
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

[ -s "$NEW_SRC" ] || { log 'REFUSE new plugin artifact missing'; exit 20; }
sha256sum "$NEW_SRC" | grep -qF "$NEW_SHA" || { log 'REFUSE new plugin sha mismatch'; exit 20; }
sha256sum "$PLUGIN" | grep -qF "$OLD_SHA" || { log 'REFUSE live plugin build drift'; exit 20; }
PRE_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
docker inspect --format 'CONTAINER status={{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}}' cli-proxy-api
ACTIVE=$(active_inflight)
if ! PRE_HOLDS=$(hold_state); then
  log 'DEFER: admission durable state unreadable before swap; nothing changed'
  exit 1
fi
log "PREFLIGHT plugin_old=$OLD_VERSION@$OLD_SHA plugin_new=$NEW_VERSION@$NEW_SHA active=$ACTIVE holds=[${PRE_HOLDS//$'\n'/,}]"

BK="$DIR/backups/$TS-plugin-0981-to-0982"
mkdir -m 700 "$BK"
cp -a "$PLUGIN" "$BK/workbuddy.so"
cp -a "$DIR/compose.yml" "$DIR/config.yaml" "$BK/"
cp -a "$NGX" "$BK/cpa-gateway.conf"
find "$DIR/auth" -maxdepth 1 -type f -name '*.json' -print0 | sort -z | xargs -0 -r sha256sum >"$BK/auth.sha256"
log "BACKUP dir=$BK"

NGX_ON=0
MUTATED=0
rollback() {
  local code=${1:-$?}
  trap - ERR HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    if cp -a "$BK/workbuddy.so" "$PLUGIN.rollback" && mv -f "$PLUGIN.rollback" "$PLUGIN"; then
      docker compose -f "$DIR/compose.yml" restart cli-proxy-api >>"$LOG" 2>&1 || true
      sleep 6
    else
      log "ROLLBACK_COPY_FAILED backup=$BK"
    fi
  fi
  if [ "$NGX_ON" = 1 ]; then
    cp -a "$BK/cpa-gateway.conf" "$NGX" || log "ROLLBACK_NGINX_RESTORE_FAILED backup=$BK"
    nginx -t && nginx -s reload || log "ROLLBACK_NGINX_RELOAD_FAILED backup=$BK"
    NGX_ON=0
  fi
  if sha256sum "$NGX" | grep -qF "$NGX_SHA_BEFORE"; then
    log 'ENTRY_CONFIG_RESTORED'
  else
    log "ENTRY_CONFIG_DRIFT expected=$NGX_SHA_BEFORE backup=$BK"
  fi
  log "ROLLBACK_DONE code=$code backup=$BK"
  [ "$code" != 0 ] || code=1
  exit "$code"
}
trap rollback ERR HUP INT TERM

# Maintenance window: the entry vhost answers 503 while live turns finish.
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

MUTATED=1
DRAIN_DEADLINE=$((SECONDS + 240))
while :; do
  if [ "$(active_inflight)" = 0 ]; then
    log 'INFLIGHT_DRAINED'
    break
  fi
  if [ "$SECONDS" -ge "$DRAIN_DEADLINE" ]; then
    log 'DEFER in-flight did not drain within 240s'
    exit 75
  fi
  sleep 1
done

install -m "$(stat -c %a "$PLUGIN")" "$NEW_SRC" "$PLUGIN.new"
chown --reference="$PLUGIN" "$PLUGIN.new"
mv -f "$PLUGIN.new" "$PLUGIN"
(cd "$DIR" && docker compose restart cli-proxy-api >>"$LOG" 2>&1)
RESTART_TS="$(date -u +%FT%TZ)"
log "RESTART_ISSUED plugin=$NEW_VERSION at=$RESTART_TS"

NEW_STARTED="$PRE_STARTED"
for _ in 1 2 3 4 5 6; do
  NEW_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
  if [ "$NEW_STARTED" != "$PRE_STARTED" ]; then
    break
  fi
  sleep 3
done
if [ "$NEW_STARTED" = "$PRE_STARTED" ]; then
  log 'REFUSE restart_did_not_apply started_unchanged'
  rollback 22
fi
log "RESTART_APPLIED started=$NEW_STARTED"

REG=1
for _ in 1 2 3 4 5 6; do
  if docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
     grep -q "plugin registered plugin_id=workbuddy .*version=$NEW_VERSION"; then
    REG=0
    break
  fi
  sleep 5
done
if [ "$REG" != 0 ]; then
  log "REFUSE post_restart_registration_missing version=$NEW_VERSION"
  rollback 20
fi
log "PLUGIN_REGISTERED version=$NEW_VERSION"

READY_RESULT=0
python3 /opt/cliproxyapi/cpa-health.py readiness || READY_RESULT=$?
if [ "$READY_RESULT" != 0 ]; then
  log "REFUSE readiness_failed result=$READY_RESULT"
  rollback 21
fi
log 'READINESS_OK'

if ! POST_HOLDS=$(hold_state); then
  log "UNVERIFIED admission state unreadable after swap; backup=$BK"
  exit 10
fi
NEW_HOLDS=""
if [ -n "$POST_HOLDS" ]; then
  NEW_HOLDS=$(comm -13 <(sort <<<"$PRE_HOLDS") <(sort <<<"$POST_HOLDS") || true)
fi
if [ -n "$NEW_HOLDS" ]; then
  log "UNVERIFIED lane hold appeared across restart lanes=${NEW_HOLDS//$'\n'/,} backup=$BK"
  exit 10
fi
log 'LANE_HOLD_DELTA=none'
sha256sum -c "$BK/auth.sha256" >"$BK/auth-check.log" 2>&1
log 'AUTH_BYTES_UNCHANGED'

# Reopen the entry before declaring success.
cp -a "$BK/cpa-gateway.conf" "$NGX"
nginx -t
nginx -s reload
NGX_ON=0
if ! sha256sum "$NGX" | grep -qF "$NGX_SHA_BEFORE"; then
  log "REFUSE entry_config_drift_after_restore expected=$NGX_SHA_BEFORE"
  exit 23
fi
log 'MAINTENANCE_GATE_OFF entry=restored'
sha256sum "$PLUGIN"
docker inspect --format 'CONTAINER status={{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}}' cli-proxy-api
log "OK: plugin $OLD_VERSION -> $NEW_VERSION backup=$BK"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

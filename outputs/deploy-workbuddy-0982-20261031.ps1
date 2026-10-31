#requires -Version 7
# Authorized bwg plugin-only swap: WorkBuddy 0.9.81 -> 0.9.82-local
# (exhausted-pool delegation). No image, config, credential, admission or
# routing change. The transaction drains admission's traffic guard, backs up
# the live artifact plus auth hashes, swaps atomically, restarts only the CPA
# container, and rolls back if the new build fails to register.
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

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
docker() { timeout --signal=TERM --kill-after=15s 180s docker "$@"; }

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
docker inspect --format 'CONTAINER status={{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}}' cli-proxy-api
python3 - <<'PY'
import json
import urllib.request

h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok'
print('ADMISSION_SNAPSHOT active=%d' % sum(v['state']['inflight'] for v in h['lanes'].values()))
PY
if ! PRE_HOLDS=$(hold_state); then
  log 'DEFER: admission durable state unreadable before swap; nothing changed'
  exit 1
fi
log "PREFLIGHT plugin_old=$OLD_VERSION@$OLD_SHA plugin_new=$NEW_VERSION@$NEW_SHA holds=[${PRE_HOLDS//$'\n'/,}]"

BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-plugin-0981-to-0982"
mkdir -m 700 "$BK"
cp -a "$PLUGIN" "$BK/workbuddy.so"
cp -a "$DIR/compose.yml" "$DIR/config.yaml" "$BK/"
find "$DIR/auth" -maxdepth 1 -type f -name '*.json' -print0 | sort -z | xargs -0 -r sha256sum >"$BK/auth.sha256"
log "BACKUP dir=$BK"

MUTATED=0
rollback() {
  local code=${1:-$?}
  trap - ERR HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    if cp -a "$BK/workbuddy.so" "$PLUGIN.rollback" && mv -f "$PLUGIN.rollback" "$PLUGIN"; then
      (cd "$DIR" && docker compose restart cli-proxy-api >>"$LOG" 2>&1) || true
      sleep 6
      if docker logs cli-proxy-api --since 3m 2>&1 | grep -q "plugin registered plugin_id=workbuddy .*version=$OLD_VERSION"; then
        log "ROLLBACK plugin=$OLD_VERSION backup=$BK"
      else
        log "ROLLBACK_FAILED verify manually backup=$BK"
      fi
    else
      log "ROLLBACK_COPY_FAILED backup=$BK"
    fi
  fi
  [ "$code" != 0 ] || code=1
  exit "$code"
}
trap rollback ERR HUP INT TERM

# Drain: hold admission's request lock so no stream straddles the restart.
# While held, new requests get the bounded maintenance_in_progress 503.
exec 8<"/run/cpa-admission/requests.lock"
if ! flock -w 300 -x 8; then
  log 'DEFER active requests did not drain within 300s; plugin unchanged'
  exit 75
fi
python3 - <<'PY'
import json
import urllib.request

h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
active = sum(v['state']['inflight'] for v in h['lanes'].values())
if active:
    raise SystemExit('active_inflight=%d' % active)
print('ADMISSION_DRAINED')
PY

MUTATED=1
install -m "$(stat -c %a "$PLUGIN")" "$NEW_SRC" "$PLUGIN.new"
chown --reference="$PLUGIN" "$PLUGIN.new"
mv -f "$PLUGIN.new" "$PLUGIN"
RESTART_TS="$(date -u +%FT%TZ)"
(cd "$DIR" && docker compose restart cli-proxy-api >>"$LOG" 2>&1)
log "RESTARTED plugin=$NEW_VERSION at=$RESTART_TS"

REG=1
for _ in 1 2 3 4 5 6; do
  if docker logs cli-proxy-api --since "$RESTART_TS" 2>&1 |
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

docker logs cli-proxy-api --since "$RESTART_TS" >"$BK/new-container.log" 2>&1
READY_RESULT=0
python3 /opt/cliproxyapi/cpa-health.py readiness || READY_RESULT=$?
if [ "$READY_RESULT" = 10 ]; then
  log 'UNVERIFIED readiness upstream_unavailable; plugin retained'
elif [ "$READY_RESULT" != 0 ]; then
  log "REFUSE readiness_failed result=$READY_RESULT after plugin swap"
  rollback 21
else
  log 'READINESS_OK'
fi

sha256sum -c "$BK/auth.sha256" >"$BK/auth-check.log" 2>&1
log 'AUTH_BYTES_UNCHANGED'

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
sha256sum "$PLUGIN"
docker inspect --format 'CONTAINER status={{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}}' cli-proxy-api
log "OK: plugin $OLD_VERSION -> $NEW_VERSION backup=$BK"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

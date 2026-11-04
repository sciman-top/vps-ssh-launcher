#requires -Version 7
# User-authorized WorkBuddy 0.9.85 INTL shared-window plugin deployment.
# This changes only plugins/workbuddy.so. It drains new traffic, waits for
# in-flight turns, atomically swaps the artifact, restarts CPA, verifies
# registration/readiness/hashes, and rolls back on failure.
param(
  [switch]$DryRun,
  [switch]$ForceRestart
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
if ($ForceRestart) {
  throw "-ForceRestart is disabled; the transaction proves drain and readiness itself."
}
$mode = if ($DryRun) { 'dry-run' } else { 'apply' }
$command = @'
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
PLUGIN="$DIR/plugins/workbuddy.so"
CONFIG="$DIR/config.yaml"
OLD_SHA=79aa1491047c7f58898e38989df6e6b00dcc3213b2f1ee038526b9511049e508
NEW_SRC=/root/cpa-workbuddy-0985-intl-shared-20261105-v2/src/plugins/workbuddy/workbuddy.so
NEW_SHA=264722fa43b9da592fa3138ba29eeef35e7299ea54ff59beae41263c06e92f53
CONFIG_SHA=dc5969f1fc31864e848dc38e832cb38cb8611be6749a2314f334d69ab5fdfa82
VERSION=0.9.85
MODE='__MODE__'
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
LOG="$DIR/canary-plugin-0985-intl-shared-$TS.log"
NGX=/etc/nginx/conf.d/cpa-gateway.conf
NGX_BK=/root/cpa-gateway.conf.pre-intl-shared-$TS
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

[ -s "$NEW_SRC" ] || { echo 'REFUSE new plugin artifact missing'; exit 20; }
[ "$(sha256sum "$NEW_SRC" | awk '{print $1}')" = "$NEW_SHA" ] || {
  echo 'REFUSE new plugin sha mismatch'
  exit 20
}
patch_marker=$(
  strings -a "$NEW_SRC" |
    grep -m1 -F 'WorkBuddy INTL shared upstream rate-limit window' ||
    true
)
[ -n "$patch_marker" ] || { echo 'REFUSE patch marker missing'; exit 20; }
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$CONFIG_SHA" ] || {
  echo 'REFUSE config baseline drift'
  exit 20
}
current_live_sha=$(sha256sum "$PLUGIN" | awk '{print $1}')
if [ "$current_live_sha" = "$NEW_SHA" ]; then
  python3 /opt/cliproxyapi/cpa-health.py readiness
  echo "ALREADY_APPLIED mode=$MODE plugin=$VERSION sha=$NEW_SHA no_swap=true"
  exit 0
fi
[ "$current_live_sha" = "$OLD_SHA" ] || {
  echo "REFUSE live plugin build drift sha=$current_live_sha"
  exit 20
}
if ! PRE_HOLDS=$(hold_state); then
  echo 'REFUSE admission durable state unreadable; nothing changed'
  exit 20
fi
PRE_ACTIVE=$(active_inflight)
echo "PREFLIGHT plugin_old=$OLD_SHA plugin_new=$NEW_SHA active=$PRE_ACTIVE holds=[${PRE_HOLDS//$'\n'/,}]"

if [ "$MODE" = 'dry-run' ]; then
  echo "DRY_RUN_OK old=$OLD_SHA new=$NEW_SHA active=$PRE_ACTIVE no_swap=true"
  exit 0
fi

BK="$DIR/backups/$TS-plugin-0985-intl-shared"
mkdir -m 700 "$BK"
cp -a "$PLUGIN" "$BK/workbuddy.so"
cp -a "$DIR/compose.yml" "$DIR/config.yaml" "$BK/"
cp -a "$NGX" "$BK/cpa-gateway.conf"
find "$DIR/auth" -maxdepth 1 -type f -name '*.json' -print0 | sort -z |
  xargs -0 -r sha256sum >"$BK/auth.sha256"
log "BACKUP dir=$BK"

NGX_ON=0
MUTATED=0
rollback() {
  local code=${1:-$?}
  trap - ERR HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    if cp -a "$BK/workbuddy.so" "$PLUGIN.rollback" &&
       mv -f "$PLUGIN.rollback" "$PLUGIN"; then
      (cd "$DIR" && docker compose restart cli-proxy-api) >>"$LOG" 2>&1 || true
      sleep 6
    else
      log "ROLLBACK_COPY_FAILED backup=$BK"
    fi
  fi
  if [ "$NGX_ON" = 1 ]; then
    cp -a "$BK/cpa-gateway.conf" "$NGX" ||
      log "ROLLBACK_NGINX_RESTORE_FAILED backup=$BK"
    nginx -t && nginx -s reload ||
      log "ROLLBACK_NGINX_RELOAD_FAILED backup=$BK"
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

deadline=$((SECONDS + 240))
while :; do
  if [ "$(active_inflight)" = 0 ]; then
    log 'INFLIGHT_DRAINED'
    break
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    log 'DEFER in-flight did not drain within 240s'
    exit 75
  fi
  sleep 1
done

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

REGISTERED=1
for _ in 1 2 3 4 5 6; do
  if docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
     grep -q "plugin registered plugin_id=workbuddy .*version=$VERSION"; then
    REGISTERED=0
    break
  fi
  sleep 5
done
if [ "$REGISTERED" != 0 ]; then
  log "REFUSE post_restart_registration_missing version=$VERSION"
  rollback 20
fi
[ "$(sha256sum "$PLUGIN" | awk '{print $1}')" = "$NEW_SHA" ] || {
  log 'REFUSE live plugin sha mismatch'
  rollback 20
}
patch_marker=$(
  strings -a "$PLUGIN" |
    grep -m1 -F 'WorkBuddy INTL shared upstream rate-limit window' ||
    true
)
[ -n "$patch_marker" ] || {
  log 'REFUSE live patch marker missing'
  rollback 20
}
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$CONFIG_SHA" ] || {
  log 'REFUSE config changed during plugin-only swap'
  rollback 20
}
log "PLUGIN_REGISTERED version=$VERSION sha=$NEW_SHA PATCH_MARKER_OK"

if ! python3 /opt/cliproxyapi/cpa-health.py readiness; then
  log 'REFUSE readiness_failed'
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

cp -a "$BK/cpa-gateway.conf" "$NGX"
nginx -t
nginx -s reload
NGX_ON=0
[ "$(sha256sum "$NGX" | awk '{print $1}')" = "$NGX_SHA_BEFORE" ] || {
  log "REFUSE entry_config_drift_after_restore expected=$NGX_SHA_BEFORE"
  exit 23
}
log 'MAINTENANCE_GATE_OFF entry=restored'
sha256sum "$PLUGIN"
docker inspect --format 'CONTAINER status={{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}}' cli-proxy-api
log "DEPLOY_OK old=$OLD_SHA new=$NEW_SHA backup=$BK"
'@
$command = $command.Replace("__MODE__", $mode)
if ($command -match "__[A-Z_]+__") {
  throw "Unfilled WorkBuddy 0.9.85 deployment placeholder."
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

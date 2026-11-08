#!/usr/bin/env bash
# Replace the CPA host binary inside the existing v8.0.59 container with the
# unsigned-Gemini-thought stream fix. Plugin and config stay byte-identical.
# Drain first with requests.lock, back up the live binary, replace atomically
# inside the container, restart, verify readiness/plugin/auth/holds, and roll
# back locally on any post-mutation failure. No credentials are printed.
set -Eeuo pipefail
umask 077

DIR=/opt/cliproxyapi
CONTAINER=cli-proxy-api
NEW=/tmp/CLIProxyAPI-v8059-raw-thought-streamfix-cgo
NEW_SHA=330b7a559ad3956bdbe090190ec822982ca26eb8817839bfe32d31a1724ed17f
OLD_SHA=95452348a3e3192cd17e3a12027e4becc75fb6071aeee568d2ffb06655c26fd5
PLUGIN_SHA=6b2b2284ddd35a4461f39130fe95772c86744b59f36406a96d509c7dec8b9135
PLUGIN="$DIR/plugins/workbuddy.so"
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK="$DIR/backups/$TS-cpa-v8059-raw-thought-streamfix"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

[ "$(sha256sum "$NEW" | cut -d' ' -f1)" = "$NEW_SHA" ] || {
  log 'NEW_BINARY_SHA_MISMATCH'
  exit 10
}
[ "$(docker exec "$CONTAINER" sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)" = "$OLD_SHA" ] || {
  log 'LIVE_BINARY_SHA_MISMATCH'
  exit 10
}
[ "$(sha256sum "$PLUGIN" | cut -d' ' -f1)" = "$PLUGIN_SHA" ] || {
  log 'LIVE_PLUGIN_SHA_MISMATCH'
  exit 10
}
[ "$(docker exec "$CONTAINER" sha256sum /CLIProxyAPI/plugins/workbuddy.so | cut -d' ' -f1)" = "$PLUGIN_SHA" ] || {
  log 'CONTAINER_PLUGIN_SHA_MISMATCH'
  exit 10
}
free_kib=$(df -Pk "$DIR" | awk 'NR==2{print $4}')
[ "$free_kib" -ge 2097152 ] || {
  log "DISK_LOW free_kib=$free_kib"
  exit 10
}

python3 - <<'PY'
import json
from pathlib import Path
import urllib.request

with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
    'http://127.0.0.1:8318/healthz', timeout=5
) as response:
    health = json.load(response)
assert health.get('traffic_guard') == '/run/cpa-admission/requests.lock'
lane = health['lanes']['antigravity-oauth']['state']
assert lane['inflight'] == 0 and lane['pending'] == 0
assert lane['hold_reason'] is None and not lane['cooldown_active']

base = Path('/var/lib/cpa-admission')
for file_name in ('chatgpt-risk.json', 'workbuddy-risk.json', 'antigravity-risk.json'):
    reason = json.loads((base / file_name).read_text()).get('hold_reason')
    if reason is None:
        continue
    lane_name = file_name.split('-risk.json')[0] + '-oauth'
    live = health['lanes'][lane_name]['state'].get('hold_reason')
    if reason == 'unverified_request' and live is None:
        print('PRE_WRITE_AHEAD_MIRROR lane=%s live=none' % lane_name)
        continue
    raise SystemExit('PRE_HOLD_PRESENT %s=%s live=%s' % (file_name, reason, live))
print('PRE_ASSERTS_OK')
PY

PRE_STARTED=$(docker inspect --format '{{.State.StartedAt}}' "$CONTAINER")
mkdir -m 700 "$BK"
docker cp "$CONTAINER:/CLIProxyAPI/CLIProxyAPI" "$BK/CLIProxyAPI.pre-streamfix"
[ "$(sha256sum "$BK/CLIProxyAPI.pre-streamfix" | cut -d' ' -f1)" = "$OLD_SHA" ] || {
  log 'BACKUP_BINARY_SHA_MISMATCH'
  exit 10
}
sha256sum "$DIR"/auth/*.json >"$BK/credentials.sha256"
chmod 600 "$BK"/*
log "BACKUP dir=$BK"

rollback() {
  local rc=$?
  trap '' ERR HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    log "ROLLBACK_BEGIN rc=$rc"
    docker cp "$BK/CLIProxyAPI.pre-streamfix" "$CONTAINER:/CLIProxyAPI/CLIProxyAPI.rollback" ||
      log 'ROLLBACK_COPY_FAILED'
    docker exec "$CONTAINER" sh -c 'chmod 0755 /CLIProxyAPI/CLIProxyAPI.rollback && mv -f /CLIProxyAPI/CLIProxyAPI.rollback /CLIProxyAPI/CLIProxyAPI' ||
      log 'ROLLBACK_INSTALL_FAILED'
    docker restart "$CONTAINER" >/dev/null || log 'ROLLBACK_RESTART_FAILED'
    python3 "$DIR/cpa-health.py" readiness && log 'ROLLBACK_READINESS_OK' ||
      log 'ROLLBACK_READINESS_FAILED'
  fi
  exit "$rc"
}
MUTATED=0
SUCCESS=0
ROLLBACK_STARTED=0
cleanup() {
  local rc=$?
  trap '' EXIT ERR HUP INT TERM
  [ "$SUCCESS" = 1 ] && return 0
  [ "$MUTATED" = 1 ] || return "$rc"
  [ "$ROLLBACK_STARTED" = 1 ] || {
    ROLLBACK_STARTED=1
    rollback "$rc"
  }
}
trap cleanup EXIT ERR HUP INT TERM

exec 8</run/cpa-admission/requests.lock
flock -w 60 -x 8 || {
  log 'DRAIN_TIMEOUT'
  exit 30
}
log 'DRAIN_OK'

MUTATED=1
docker cp "$NEW" "$CONTAINER:/CLIProxyAPI/CLIProxyAPI.next"
docker exec "$CONTAINER" sh -c 'chmod 0755 /CLIProxyAPI/CLIProxyAPI.next && mv -f /CLIProxyAPI/CLIProxyAPI.next /CLIProxyAPI/CLIProxyAPI'
[ "$(docker exec "$CONTAINER" sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)" = "$NEW_SHA" ] || {
  log 'BINARY_INSTALL_READBACK_FAILED'
  exit 10
}
log 'BINARY_INSTALLED'
docker restart "$CONTAINER" >/dev/null
RESTART_TS=$(date -u +%FT%TZ)

NEW_STARTED="$PRE_STARTED"
for _ in 1 2 3 4 5 6 7 8; do
  NEW_STARTED=$(docker inspect --format '{{.State.StartedAt}}' "$CONTAINER")
  [ "$NEW_STARTED" != "$PRE_STARTED" ] && break
  sleep 2
done
[ "$NEW_STARTED" != "$PRE_STARTED" ] || {
  log 'RESTART_NOT_APPLIED'
  exit 10
}
log "RESTART_APPLIED started=$NEW_STARTED"

[ "$(docker exec "$CONTAINER" sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)" = "$NEW_SHA" ] || {
  log 'POST_RESTART_BINARY_SHA_MISMATCH'
  exit 10
}
log 'BINARY_READBACK_OK'

registered=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if docker logs --since "$RESTART_TS" "$CONTAINER" 2>&1 |
    grep -q 'plugin registered plugin_id=workbuddy .*version=0.9.88'; then
    registered=1
    break
  fi
  sleep 2
done
[ "$registered" = 1 ] || {
  log 'PLUGIN_REGISTRATION_MISSING'
  exit 10
}
log 'PLUGIN_REGISTERED version=0.9.88'
docker logs --since "$RESTART_TS" "$CONTAINER" 2>&1 |
  grep -m1 'CLIProxyAPI Version: v8.0.59'
if docker logs --since "$RESTART_TS" "$CONTAINER" 2>&1 |
  grep -Eiq 'panic|failed to load'; then
  log 'START_LOG_FAULT_PATTERN'
  exit 10
fi
log 'START_LOG_CLEAN'

[ "$(sha256sum "$PLUGIN" | cut -d' ' -f1)" = "$PLUGIN_SHA" ] || {
  log 'PLUGIN_SHA_DRIFTED'
  exit 10
}
python3 "$DIR/cpa-health.py" readiness || {
  log 'READINESS_FAILED'
  exit 10
}
log 'READINESS_OK'

sha256sum "$DIR"/auth/*.json >"$BK/credentials-post.sha256"
before_auth_count=$(wc -l <"$BK/credentials.sha256")
after_auth_count=$(wc -l <"$BK/credentials-post.sha256")
[ "$before_auth_count" = "$after_auth_count" ] || {
  log 'AUTH_FILE_SET_CHANGED'
  exit 10
}
auth_drift=$(sha256sum -c "$BK/credentials.sha256" 2>/dev/null | grep -v ': OK$' || true)
if [ -n "$auth_drift" ]; then
  log "AUTH_RUNTIME_REFRESH count=$(printf '%s\n' "$auth_drift" | wc -l)"
else
  log 'AUTH_BYTES_UNCHANGED'
fi

for f in chatgpt-risk.json workbuddy-risk.json antigravity-risk.json; do
  reason=$(python3 -c "import json;print(json.load(open('/var/lib/cpa-admission/$f')).get('hold_reason'))")
  [ "$reason" = "None" ] && continue
  lane_name=${f%-risk.json}-oauth
  live_hold=$(python3 - "$lane_name" <<'PY'
import json
import sys
import urllib.request

with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
    'http://127.0.0.1:8318/healthz', timeout=5
) as response:
    health = json.load(response)
print(health['lanes'][sys.argv[1]]['state']['hold_reason'])
PY
)
  if [ "$reason" != "unverified_request" ] || [ "$live_hold" != "None" ]; then
    log "LANE_HOLD_APPEARED $f=$reason live=$live_hold"
    exit 10
  fi
  log "LANE_WRITE_AHEAD_MIRROR_WAIT $f=$reason live=$live_hold"
done
log 'LANE_HOLD_DELTA=none'
SUCCESS=1
trap - EXIT ERR HUP INT TERM
flock -u 8
log "restarts=$(docker inspect --format '{{.RestartCount}}' "$CONTAINER")"
log "STREAMFIX_DEPLOY_OK backup=$BK"

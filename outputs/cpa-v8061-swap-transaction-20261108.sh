#!/usr/bin/env bash
# bwg CPA v8.0.59 -> v8.0.61 host swap plus the official cpa-multi-plugins
# v0.8.97 WorkBuddy plugin 0.9.88 -> 0.9.89, in one drain transaction and a
# single container recreation. config.yaml and every credential stay
# byte-identical.
#
# This deployment is NOT a plain image swap: the runtime binary is the local
# fork build (official tag + the Gemini raw-reasoning patch), bind-mounted
# read-only from /opt/cliproxyapi/CLIProxyAPI-patched. The transaction
# therefore rotates that binary as well; otherwise the new image tag would
# keep running the old binary and every v8.0.60/v8.0.61 fix would stay dead.
set -Eeuo pipefail
umask 077

DIR=/opt/cliproxyapi
OLD_IMAGE='eceasy/cli-proxy-api:v8.0.59@sha256:40dda818164c1012154e18999d35d9cf14d936bd74eabdd62e22284e8898d50d'
NEW_IMAGE='eceasy/cli-proxy-api:v8.0.61@sha256:fc714b4339f20b59ee4781eb0f35a9e563aa357daece3081b0e266caeb11187e'
OLD_PLUGIN_SHA=6b2b2284ddd35a4461f39130fe95772c86744b59f36406a96d509c7dec8b9135
NEW_PLUGIN_SHA=f2c678ff42a0cc9c21057473e0e7530754d9a058355b9e28190f52d930ad5de2
NEW_PLUGIN_VERSION=0.9.89
OLD_BINARY_SHA=330b7a559ad3956bdbe090190ec822982ca26eb8817839bfe32d31a1724ed17f
NEW_BINARY_SHA=__NEW_BINARY_SHA__
NEW_BINARY=/root/cpa-v8061-gemini-raw-build/CLIProxyAPI-v8061-gemini-raw
STAGED_PLUGIN=/root/cpa-multi-plugins-v0.8.97-staging/workbuddy.so
CONFIG_SHA=__CONFIG_SHA__
PLUGIN="$DIR/plugins/workbuddy.so"
PATCHED="$DIR/CLIProxyAPI-patched"
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK="$DIR/backups/$TS-from-v8.0.59-to-v8.0.61-wb0989"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

# Readiness is a bounded wait, never a single instant. The first 2026-11-08
# attempt asserted 0.4 s after the container started, cpa-health.py hit
# ConnectionResetError while the new process was still binding 8317, and a
# healthy swap was rolled back. This window only ever extends a passing check.
wait_readiness() {
  local deadline=$((SECONDS + 120))
  while :; do
    if python3 "$DIR/cpa-health.py" readiness; then
      return 0
    fi
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 3
  done
}

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

grep -qF "image: $OLD_IMAGE" "$DIR/compose.yml" || {
  log 'COMPOSE_PIN_MISMATCH'
  exit 10
}
[ "$(sha256sum "$PLUGIN" | cut -d' ' -f1)" = "$OLD_PLUGIN_SHA" ] || {
  log 'LIVE_PLUGIN_SHA_MISMATCH'
  exit 10
}
[ "$(sha256sum "$PATCHED" | cut -d' ' -f1)" = "$OLD_BINARY_SHA" ] || {
  log 'LIVE_BINARY_SHA_MISMATCH'
  exit 10
}
[ "$(docker exec cli-proxy-api sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)" = "$OLD_BINARY_SHA" ] || {
  log 'CONTAINER_BINARY_SHA_MISMATCH'
  exit 10
}
[ -s "$NEW_BINARY" ] || {
  log 'NEW_BINARY_MISSING'
  exit 10
}
[ "$(sha256sum "$NEW_BINARY" | cut -d' ' -f1)" = "$NEW_BINARY_SHA" ] || {
  log 'NEW_BINARY_SHA_MISMATCH'
  exit 10
}
[ -s "$STAGED_PLUGIN" ] || {
  log 'STAGED_PLUGIN_MISSING'
  exit 10
}
[ "$(sha256sum "$STAGED_PLUGIN" | cut -d' ' -f1)" = "$NEW_PLUGIN_SHA" ] || {
  log 'STAGED_PLUGIN_SHA_MISMATCH'
  exit 10
}
[ "$(sha256sum "$DIR/config.yaml" | cut -d' ' -f1)" = "$CONFIG_SHA" ] || {
  log 'CONFIG_BASELINE_MISMATCH'
  exit 10
}
docker image inspect "$OLD_IMAGE" >/dev/null || {
  log 'ROLLBACK_IMAGE_MISSING'
  exit 10
}
docker image inspect "$NEW_IMAGE" >/dev/null || {
  log 'TARGET_IMAGE_MISSING'
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

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health.get('traffic_guard') == '/run/cpa-admission/requests.lock'

# The doctor's lane-hold contract is authoritative here too: a disk
# unverified_request whose live healthz hold is none is the benign
# write-ahead mirror of "a request was in flight when risk state persisted",
# not a durable hold. Any other reason on disk, or any live hold, refuses
# the swap.
base = Path('/var/lib/cpa-admission')
lane_by_file = {
    'chatgpt-risk.json': 'chatgpt-oauth',
    'workbuddy-risk.json': 'workbuddy-oauth',
    'antigravity-risk.json': 'antigravity-oauth',
}
for file_name, lane in lane_by_file.items():
    reason = json.loads((base / file_name).read_text()).get('hold_reason')
    if reason is None:
        continue
    live = health['lanes'][lane]['state'].get('hold_reason')
    if reason == 'unverified_request' and live is None:
        print('PRE_WRITE_AHEAD_MIRROR lane=%s live=none' % lane)
        continue
    raise SystemExit('PRE_HOLD_PRESENT %s=%s live=%s' % (file_name, reason, live))
print('PRE_HOLDS_CLEAN')
PY
PRE_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
log 'PRE_ASSERTS_OK'

mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$DIR/config.yaml" "$PLUGIN" "$PATCHED" "$BK/"
sha256sum "$DIR"/auth/*.json >"$BK/credentials.sha256"
chmod 600 "$BK"/*
cmp -s "$BK/compose.yml" "$DIR/compose.yml" || { log 'BACKUP_COMPOSE_DIFFERS'; exit 10; }
cmp -s "$BK/workbuddy.so" "$PLUGIN" || { log 'BACKUP_PLUGIN_DIFFERS'; exit 10; }
cmp -s "$BK/CLIProxyAPI-patched" "$PATCHED" || { log 'BACKUP_BINARY_DIFFERS'; exit 10; }
log "BACKUP dir=$BK"

MUTATED=0
rollback() {
  local rc=$?
  trap '' ERR HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    log "ROLLBACK_BEGIN rc=$rc"
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || log 'ROLLBACK_COMPOSE_FAILED'
    cp -a "$BK/workbuddy.so" "$PLUGIN.rollback" &&
      mv -f "$PLUGIN.rollback" "$PLUGIN" || log 'ROLLBACK_PLUGIN_FAILED'
    cp -a "$BK/CLIProxyAPI-patched" "$PATCHED.rollback" &&
      mv -f "$PATCHED.rollback" "$PATCHED" &&
      chmod 700 "$PATCHED" || log 'ROLLBACK_BINARY_FAILED'
    (cd "$DIR" && docker compose up -d --pull never) || log 'ROLLBACK_RECREATE_FAILED'
    wait_readiness && log 'ROLLBACK_READINESS_OK' || log 'ROLLBACK_READINESS_FAILED'
  fi
  exit "$rc"
}
trap rollback ERR HUP INT TERM

exec 8</run/cpa-admission/requests.lock
flock -w 60 -x 8 || {
  log 'DRAIN_TIMEOUT'
  exit 30
}
log 'DRAIN_OK'

MUTATED=1
python3 - "$DIR/compose.yml" "$NEW_IMAGE" <<'PY'
import os
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text, count = re.subn(
    r'image:\s*eceasy/cli-proxy-api:[^\s]+',
    'image: ' + sys.argv[2],
    path.read_text(encoding="utf-8"),
)
if count != 1:
    raise SystemExit("REFUSE image replacement count=%d" % count)
temporary = path.with_suffix(".yml.new")
temporary.write_text(text, encoding="utf-8")
os.chmod(temporary, path.stat().st_mode & 0o777)
os.replace(temporary, path)
PY
install -m "$(stat -c %a "$PATCHED")" "$NEW_BINARY" "$PATCHED.new"
chown --reference="$PATCHED" "$PATCHED.new"
# The bind-mounted binary is the container entrypoint (exec ./CLIProxyAPI):
# its exec bit is load-bearing. install -m only propagates the previous mode,
# and a umask-077 build or a 600-hardened backup restore silently strips it
# (2026-11-08 exit-126 crash loop), so pin owner-executable explicitly.
chmod 700 "$PATCHED.new"
mv -f "$PATCHED.new" "$PATCHED"
[ -x "$PATCHED" ] || { log 'BINARY_NOT_EXECUTABLE'; exit 10; }
install -m "$(stat -c %a "$PLUGIN")" "$STAGED_PLUGIN" "$PLUGIN.new"
chown --reference="$PLUGIN" "$PLUGIN.new"
mv -f "$PLUGIN.new" "$PLUGIN"

(cd "$DIR" && docker compose config --quiet)
(cd "$DIR" && docker compose up -d --pull never)
RESTART_TS=$(date -u +%FT%TZ)

running=$(docker inspect --format '{{.Image}}' cli-proxy-api)
expected=$(docker image inspect --format '{{.ID}}' "$NEW_IMAGE")
[ "$running" = "$expected" ] || {
  log 'DIGEST_MISMATCH'
  exit 10
}
log 'DIGEST_READBACK=OK'

NEW_STARTED="$PRE_STARTED"
for _ in 1 2 3 4 5 6 7 8; do
  NEW_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
  [ "$NEW_STARTED" != "$PRE_STARTED" ] && break
  sleep 2
done
[ "$NEW_STARTED" != "$PRE_STARTED" ] || {
  log 'RESTART_NOT_APPLIED'
  exit 10
}
log "RESTART_APPLIED started=$NEW_STARTED"

# Capture the startup log once and grep the captured text. Piping
# \`docker logs\` into \`grep -m1\` under \`set -o pipefail\` fails the whole
# pipeline with SIGPIPE (141) the moment grep exits early, which is exactly
# what rolled a healthy v8.0.61 swap back on 2026-11-08.
START_LOG=''
registered=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  START_LOG=$(docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 || true)
  if printf '%s\n' "$START_LOG" |
    grep -q "plugin registered plugin_id=workbuddy .*version=$NEW_PLUGIN_VERSION"; then
    registered=1
    break
  fi
  sleep 2
done
[ "$registered" = 1 ] || {
  log 'PLUGIN_REGISTRATION_MISSING'
  exit 10
}
log "PLUGIN_REGISTERED version=$NEW_PLUGIN_VERSION"
printf '%s\n' "$START_LOG" | grep -m1 'CLIProxyAPI Version: v8.0.61' || {
  log 'HOST_VERSION_LINE_MISSING'
  printf '%s\n' "$START_LOG" | tail -20
  log 'HOST_VERSION_LINE_TAIL_END'
  exit 10
}
if printf '%s\n' "$START_LOG" | grep -Eiq 'panic|failed to load'; then
  log 'START_LOG_FAULT_PATTERN'
  exit 10
fi
log 'START_LOG_CLEAN'

[ "$(sha256sum "$PATCHED" | cut -d' ' -f1)" = "$NEW_BINARY_SHA" ] || {
  log 'HOST_BINARY_SHA_DRIFTED'
  exit 10
}
[ "$(docker exec cli-proxy-api sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)" = "$NEW_BINARY_SHA" ] || {
  log 'CONTAINER_BINARY_SHA_DRIFTED'
  exit 10
}
log "RUNTIME_BINARY sha=$NEW_BINARY_SHA host_and_container"
[ "$(sha256sum "$PLUGIN" | cut -d' ' -f1)" = "$NEW_PLUGIN_SHA" ] || {
  log 'PLUGIN_SHA_DRIFTED'
  exit 10
}
[ "$(sha256sum "$DIR/config.yaml" | cut -d' ' -f1)" = "$CONFIG_SHA" ] || {
  log 'CONFIG_SHA_DRIFTED'
  exit 10
}
log 'PLUGIN_AND_CONFIG_PINNED'

wait_readiness || {
  log 'READINESS_FAILED'
  exit 10
}
log 'READINESS_OK'

sha256sum "$DIR"/auth/*.json >"$BK/credentials-post.sha256"
cmp -s "$BK/credentials.sha256" "$BK/credentials-post.sha256" || {
  log 'AUTH_BYTES_CHANGED'
  exit 10
}
log 'AUTH_BYTES_UNCHANGED'
if [ -f "$DIR/auth/kv.db" ]; then
  log "KVSTORE_DB_PRESENT size_bytes=$(stat -c %s "$DIR/auth/kv.db")"
else
  log 'KVSTORE_DB_ABSENT'
fi

for f in chatgpt-risk.json workbuddy-risk.json antigravity-risk.json; do
  reason=$(python3 -c "import json;print(json.load(open('/var/lib/cpa-admission/$f')).get('hold_reason'))")
  [ "$reason" = "None" ] || {
    log "LANE_HOLD_APPEARED $f=$reason"
    exit 10
  }
done
log 'LANE_HOLD_DELTA=none'
flock -u 8
log "restarts=$(docker inspect --format '{{.RestartCount}}' cli-proxy-api)"
log "SWAP_OK v8.0.59->v8.0.61 plugin=0.9.88->$NEW_PLUGIN_VERSION binary=$NEW_BINARY_SHA backup=$BK"

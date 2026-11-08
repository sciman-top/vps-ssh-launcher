#!/usr/bin/env bash
# Project the accepted CPA binary from the mutable container layer to a host
# bind mount so it survives compose recreation. Drain first, mutate compose,
# recreate once, verify readiness/plugin/hash/mount/holds, and roll back to the
# same running patch by restoring the old compose and re-copying the binary.
set -Eeuo pipefail
umask 077

DIR=/opt/cliproxyapi
CONTAINER=cli-proxy-api
BINARY_SHA=330b7a559ad3956bdbe090190ec822982ca26eb8817839bfe32d31a1724ed17f
PLUGIN_SHA=6b2b2284ddd35a4461f39130fe95772c86744b59f36406a96d509c7dec8b9135
PLUGIN="$DIR/plugins/workbuddy.so"
HOST_BINARY="$DIR/CLIProxyAPI-patched"
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK="$DIR/backups/$TS-cpa-v8059-raw-thought-projection"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

[ "$(docker exec "$CONTAINER" sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)" = "$BINARY_SHA" ] || {
  log 'LIVE_BINARY_SHA_MISMATCH'
  exit 10
}
[ "$(sha256sum "$PLUGIN" | cut -d' ' -f1)" = "$PLUGIN_SHA" ] || {
  log 'PLUGIN_SHA_MISMATCH'
  exit 10
}
grep -qF 'image: eceasy/cli-proxy-api:v8.0.59@sha256:40dda818164c1012154e18999d35d9cf14d936bd74eabdd62e22284e8898d50d' "$DIR/compose.yml" || {
  log 'COMPOSE_IMAGE_PIN_MISMATCH'
  exit 10
}
if grep -Eq '/CLIProxyAPI/CLIProxyAPI(:|$)' "$DIR/compose.yml"; then
  log 'BINARY_MOUNT_ALREADY_PRESENT'
  exit 10
fi
free_kib=$(df -Pk "$DIR" | awk 'NR==2{print $4}')
[ "$free_kib" -ge 2097152 ] || {
  log "DISK_LOW free_kib=$free_kib"
  exit 10
}

PRE_STARTED=$(docker inspect --format '{{.State.StartedAt}}' "$CONTAINER")
mkdir -m 700 "$BK"
docker cp "$CONTAINER:/CLIProxyAPI/CLIProxyAPI" "$BK/CLIProxyAPI.pre-projection"
[ "$(sha256sum "$BK/CLIProxyAPI.pre-projection" | cut -d' ' -f1)" = "$BINARY_SHA" ] || {
  log 'BACKUP_BINARY_SHA_MISMATCH'
  exit 10
}
docker cp "$CONTAINER:/CLIProxyAPI/CLIProxyAPI" "$HOST_BINARY.next"
chmod 0755 "$HOST_BINARY.next"
[ "$(sha256sum "$HOST_BINARY.next" | cut -d' ' -f1)" = "$BINARY_SHA" ] || {
  log 'PROJECTED_BINARY_SHA_MISMATCH'
  exit 10
}
sha256sum "$DIR"/auth/*.json >"$BK/credentials.sha256"
chmod 600 "$BK"/*
log "BACKUP dir=$BK"

MUTATED=0
SUCCESS=0
ROLLBACK_STARTED=0
restore_running_patch() {
  cp -a "$BK/compose.yml" "$DIR/compose.yml" || log 'ROLLBACK_COMPOSE_FAILED'
  (cd "$DIR" && docker compose up -d --pull never) || log 'ROLLBACK_RECREATE_FAILED'
  python3 "$DIR/cpa-health.py" readiness || log 'ROLLBACK_READINESS_FAILED'
  docker cp "$BK/CLIProxyAPI.pre-projection" "$CONTAINER:/CLIProxyAPI/CLIProxyAPI.rollback" ||
    log 'ROLLBACK_COPY_FAILED'
  docker exec "$CONTAINER" sh -c 'chmod 0755 /CLIProxyAPI/CLIProxyAPI.rollback && mv -f /CLIProxyAPI/CLIProxyAPI.rollback /CLIProxyAPI/CLIProxyAPI' ||
    log 'ROLLBACK_INSTALL_FAILED'
  docker restart "$CONTAINER" >/dev/null || log 'ROLLBACK_RESTART_FAILED'
  python3 "$DIR/cpa-health.py" readiness && log 'ROLLBACK_READINESS_OK' ||
    log 'ROLLBACK_READINESS_FAILED'
  [ "$(docker exec "$CONTAINER" sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)" = "$BINARY_SHA" ] &&
    log 'ROLLBACK_BINARY_OK' || log 'ROLLBACK_BINARY_FAILED'
}
rollback() {
  local rc=$?
  trap '' EXIT ERR HUP INT TERM
  [ "$MUTATED" = 1 ] || exit "$rc"
  [ "$ROLLBACK_STARTED" = 1 ] || {
    ROLLBACK_STARTED=1
    log "ROLLBACK_BEGIN rc=$rc"
    restore_running_patch
  }
  exit "$rc"
}
trap rollback EXIT ERR HUP INT TERM

exec 8</run/cpa-admission/requests.lock
flock -w 180 -x 8 || {
  log 'DRAIN_TIMEOUT'
  exit 30
}
log 'DRAIN_OK'

MUTATED=1
mv -f "$HOST_BINARY.next" "$HOST_BINARY"
python3 - "$DIR/compose.yml" <<'PY'
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding='utf-8')
needle = '    - ./plugins:/CLIProxyAPI/plugins\n'
addition = '    - ./CLIProxyAPI-patched:/CLIProxyAPI/CLIProxyAPI:ro\n'
if text.count(needle) != 1 or '/CLIProxyAPI/CLIProxyAPI' in text:
    raise SystemExit('REFUSE compose projection target')
temporary = path.with_suffix('.yml.next')
temporary.write_text(text.replace(needle, needle + addition), encoding='utf-8')
os.chmod(temporary, path.stat().st_mode & 0o777)
os.replace(temporary, path)
PY
(cd "$DIR" && docker compose config --quiet)
log 'COMPOSE_PROJECTED'
(cd "$DIR" && docker compose up -d --pull never)
RESTART_TS=$(date -u +%FT%TZ)

NEW_STARTED="$PRE_STARTED"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  NEW_STARTED=$(docker inspect --format '{{.State.StartedAt}}' "$CONTAINER")
  [ "$NEW_STARTED" != "$PRE_STARTED" ] && break
  sleep 2
done
[ "$NEW_STARTED" != "$PRE_STARTED" ] || {
  log 'RECREATION_NOT_APPLIED'
  exit 10
}
log "RECREATION_APPLIED started=$NEW_STARTED"

for _ in 1 2 3 4 5 6 7 8 9 10; do
  python3 "$DIR/cpa-health.py" readiness && break
  sleep 2
done
python3 "$DIR/cpa-health.py" readiness || {
  log 'READINESS_FAILED'
  exit 10
}
log 'READINESS_OK'

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

[ "$(docker exec "$CONTAINER" sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)" = "$BINARY_SHA" ] || {
  log 'CONTAINER_BINARY_SHA_MISMATCH'
  exit 10
}
[ "$(sha256sum "$HOST_BINARY" | cut -d' ' -f1)" = "$BINARY_SHA" ] || {
  log 'HOST_BINARY_SHA_MISMATCH'
  exit 10
}
mount=$(docker inspect "$CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/CLIProxyAPI/CLIProxyAPI"}}{{.Type}}={{.Source}}:RW={{.RW}}{{end}}{{end}}')
[ "$mount" = "bind=$HOST_BINARY:RW=false" ] || {
  log "BINARY_MOUNT_MISMATCH mount=$mount"
  exit 10
}
log "BINARY_MOUNT_OK mount=$mount"

sha256sum "$DIR"/auth/*.json >"$BK/credentials-post.sha256"
auth_drift=$(sha256sum -c "$BK/credentials.sha256" 2>/dev/null | grep -v ': OK$' || true)
if [ -n "$auth_drift" ]; then
  log "AUTH_RUNTIME_REFRESH count=$(printf '%s\n' "$auth_drift" | wc -l)"
else
  log 'AUTH_BYTES_UNCHANGED'
fi

for lane in chatgpt workbuddy antigravity; do
  reason=$(python3 -c "import json;print(json.load(open('/var/lib/cpa-admission/${lane}-risk.json')).get('hold_reason'))")
  [ "$reason" = "None" ] || {
    log "LANE_HOLD_APPEARED lane=$lane reason=$reason"
    exit 10
  }
done
log 'LANE_HOLD_DELTA=none'

SUCCESS=1
trap - EXIT ERR HUP INT TERM
flock -u 8
log "PROJECTION_OK backup=$BK binary=$HOST_BINARY"

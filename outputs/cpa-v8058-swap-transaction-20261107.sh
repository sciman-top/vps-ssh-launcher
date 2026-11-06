#!/usr/bin/env bash
# bwg CPA v8.0.55 -> v8.0.58 host-only swap. WorkBuddy plugin (official
# 0.9.87 canonical build, sha 7093a190) and config.yaml stay byte-identical;
# only the compose image line changes. Maintenance lock, request drain,
# backups, atomic image swap, post-registration assertions, local rollback.
# No credentials are read, copied, printed, rotated, or deleted.
set -Eeuo pipefail
umask 077

DIR=/opt/cliproxyapi
OLD_IMAGE='eceasy/cli-proxy-api:v8.0.55@sha256:afe1135e8d8752867b73d717c1ca8ed27c44bfe8c6e1a2bebf228dba98889eac'
NEW_IMAGE='eceasy/cli-proxy-api:v8.0.58@sha256:060f352248b413d2edac58d816aa5437972ce764a07250d2b9aef82db549cb6e'
PLUGIN_SHA=7093a190e906a872f7362e1bc225eaf6f0fba30388cdd9066af6d0d8636a89e4
CONFIG_SHA=__CONFIG_SHA__
PLUGIN="$DIR/plugins/workbuddy.so"
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK="$DIR/backups/$TS-from-v8.0.55-to-v8.0.58"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

grep -qF "image: $OLD_IMAGE" "$DIR/compose.yml" || {
  log 'COMPOSE_PIN_MISMATCH'
  exit 10
}
[ "$(sha256sum "$PLUGIN" | cut -d' ' -f1)" = "$PLUGIN_SHA" ] || {
  log 'LIVE_PLUGIN_SHA_MISMATCH'
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

for f in chatgpt-risk.json workbuddy-risk.json antigravity-risk.json; do
  reason=$(python3 -c "import json;print(json.load(open('/var/lib/cpa-admission/$f')).get('hold_reason'))")
  [ "$reason" = "None" ] || {
    log "PRE_HOLD_PRESENT $f=$reason"
    exit 10
  }
done
python3 - <<'PY'
import json
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8318/healthz', timeout=5) as response:
    state = json.load(response)
assert state.get('traffic_guard') == '/run/cpa-admission/requests.lock'
PY
PRE_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
log 'PRE_ASSERTS_OK'

mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$DIR/config.yaml" "$PLUGIN" "$BK/"
sha256sum "$DIR"/auth/*.json >"$BK/credentials.sha256"
chmod 600 "$BK"/*
cmp -s "$BK/compose.yml" "$DIR/compose.yml" || {
  log 'BACKUP_COMPOSE_DIFFERS'
  exit 10
}
log "BACKUP dir=$BK"

MUTATED=0
rollback() {
  local rc=$?
  trap '' ERR HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    log "ROLLBACK_BEGIN rc=$rc"
    cp -a "$BK/compose.yml" "$DIR/compose.yml" ||
      log 'ROLLBACK_COMPOSE_FAILED'
    (cd "$DIR" && docker compose up -d --pull never) ||
      log 'ROLLBACK_RECREATE_FAILED'
    python3 "$DIR/cpa-health.py" readiness && log 'ROLLBACK_READINESS_OK' ||
      log 'ROLLBACK_READINESS_FAILED'
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

registered=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
    grep -q 'plugin registered plugin_id=workbuddy .*version=0.9.87'; then
    registered=1
    break
  fi
  sleep 2
done
[ "$registered" = 1 ] || {
  log 'PLUGIN_REGISTRATION_MISSING'
  exit 10
}
log 'PLUGIN_REGISTERED version=0.9.87'
docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
  grep -m1 'CLIProxyAPI Version: v8.0.58'
if docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
  grep -Eiq 'panic|failed to load'; then
  log 'START_LOG_FAULT_PATTERN'
  exit 10
fi
log 'START_LOG_CLEAN'
[ "$(sha256sum "$PLUGIN" | cut -d' ' -f1)" = "$PLUGIN_SHA" ] || {
  log 'PLUGIN_SHA_DRIFTED'
  exit 10
}
[ "$(sha256sum "$DIR/config.yaml" | cut -d' ' -f1)" = "$CONFIG_SHA" ] || {
  log 'CONFIG_SHA_DRIFTED'
  exit 10
}
log 'PLUGIN_AND_CONFIG_UNCHANGED'

python3 "$DIR/cpa-health.py" readiness || {
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
log "SWAP_OK v8.0.55->v8.0.58 backup=$BK"

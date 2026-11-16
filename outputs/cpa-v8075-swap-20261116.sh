#!/usr/bin/env bash
# bwg CPA generation swap: cli-proxy-api-fork v8.0.61-gemini-raw ->
# v8.0.75-gemini-raw. Only the compose image line changes; config.yaml,
# credentials, the WorkBuddy plugin and the admission service are untouched.
#
# Why the swap exists: the derived image carries the local Gemini
# raw-reasoning translator change, so a new upstream release needs a rebuilt
# binary AND a moved image tag -- never one without the other. v8.0.75 brings
# the Claude idle-connection fix (2c5b01f4), credential-level TOS deactivate
# (5a351d95), Gemini Retry-After honouring (ed1c23f9) and Gemini zero-output
# no-cool (7e82d0d1).
#
# Convergence contract: the compose line may be at old or target on entry and
# must equal target afterwards; anything else refuses. EXIT is trapped as well
# as ERR so a failed post-condition still rolls back through one path.
set -Eeuo pipefail
umask 077

DIR=/opt/cliproxyapi
OLD_IMAGE='cli-proxy-api-fork:v8.0.61-gemini-raw'
NEW_IMAGE='cli-proxy-api-fork:v8.0.75-gemini-raw'
NEW_IMAGE_ID=sha256:0d9f911ea505ad345be000be135be2acd66b87f4a27589997c2afd29de61dd2c
NEW_BINARY_SHA=2bdc6cbd3f21d7951ab779b38b10a932293679ec1cde5ba3809e0ea5fd876e73
BASE_DIGEST=sha256:16bdaaf25b86cd1974ec9bfcc2253a44ac6510ea22f7a483b36f8dee13b003c2
CONTAINER=cli-proxy-api
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK="$DIR/backups/$TS-swap-v8.0.75-gemini-raw"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

wait_readiness() {
  local deadline=$((SECONDS + 180))
  while :; do
    if python3 "$DIR/cpa-health.py" readiness; then return 0; fi
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 3
  done
}

compose_image() {
  python3 - "$DIR/compose.yml" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding='utf-8')
matches = re.findall(r'image:\s*([^\s#]+)', text)
if len(matches) != 1:
    raise SystemExit('COMPOSE_DECLARATION_AMBIGUOUS')
print(matches[0])
PY
}

sha_of() { sha256sum "$1" | cut -d' ' -f1; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

CUR_IMAGE=$(compose_image) || { log 'COMPOSE_UNREADABLE'; exit 10; }
case "$CUR_IMAGE" in
  "$OLD_IMAGE") STATE=old ;;
  "$NEW_IMAGE") STATE=new ;;
  *) log "COMPOSE_UNEXPECTED ref=$CUR_IMAGE"; exit 10 ;;
esac
docker image inspect "$NEW_IMAGE" >/dev/null 2>&1 || { log 'TARGET_IMAGE_MISSING'; exit 10; }
GOT_ID=$(docker image inspect --format '{{.Id}}' "$NEW_IMAGE")
[ "$GOT_ID" = "$NEW_IMAGE_ID" ] || { log "TARGET_IMAGE_ID_MISMATCH got=$GOT_ID"; exit 10; }
GOT_BASE=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.base.digest"}}' "$NEW_IMAGE")
[ "$GOT_BASE" = "$BASE_DIGEST" ] || { log "TARGET_BASE_LABEL_MISMATCH got=$GOT_BASE"; exit 10; }
GOT_FORK=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.fork-binary.sha256"}}' "$NEW_IMAGE")
[ "$GOT_FORK" = "$NEW_BINARY_SHA" ] || { log "TARGET_FORK_LABEL_MISMATCH got=$GOT_FORK"; exit 10; }
[ -x "$DIR/CLIProxyAPI-patched" ] || { log 'ARCHIVE_BINARY_MISSING'; exit 10; }
free_kib=$(df -Pk "$DIR" | awk 'NR==2{print $4}')
[ "$free_kib" -ge 2097152 ] || { log "DISK_LOW free_kib=$free_kib"; exit 10; }
log "ENTRY_STATE compose=$STATE cur=$CUR_IMAGE"

if [ "$STATE" = new ]; then
  if wait_readiness; then log "ALREADY_CONVERGED image=$NEW_IMAGE"; exit 0; fi
  log 'ALREADY_CONVERGED_BUT_UNREADY'
  exit 10
fi

python3 - <<'PY'
import json
from pathlib import Path
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health.get('traffic_guard') == '/run/cpa-admission/requests.lock'
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

PRE_STARTED=$(docker inspect --format '{{.State.StartedAt}}' "$CONTAINER")
mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$DIR/config.yaml" "$BK/"
sha256sum "$DIR"/auth/*.json >"$BK/credentials.sha256"
sha256sum "$DIR/config.yaml" >"$BK/config.sha256"
chmod 600 "$BK"/*
cmp -s "$BK/compose.yml" "$DIR/compose.yml" || { log 'BACKUP_COMPOSE_DIFFERS'; exit 10; }
log "BACKUP dir=$BK"
log 'PRE_ASSERTS_OK'

MUTATED=0
SUCCESS=0
ROLLED_BACK=0
restore_all() {
  local rc=$?
  trap '' EXIT ERR HUP INT TERM
  [ "$SUCCESS" = 1 ] && exit 0
  if [ "$MUTATED" = 1 ] && [ "$ROLLED_BACK" = 0 ]; then
    ROLLED_BACK=1
    log "ROLLBACK_BEGIN rc=$rc"
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || log 'ROLLBACK_COMPOSE_FAILED'
    (cd "$DIR" && docker compose up -d --pull never) || log 'ROLLBACK_RECREATE_FAILED'
    wait_readiness && log 'ROLLBACK_READINESS_OK' || log 'ROLLBACK_READINESS_FAILED'
    log 'ROLLBACK_DONE'
  fi
  [ "$rc" = 0 ] && rc=1
  exit "$rc"
}
trap restore_all EXIT
trap 'exit $?' ERR HUP INT TERM

exec 8</run/cpa-admission/requests.lock
flock -w 120 -x 8 || { log 'DRAIN_TIMEOUT'; exit 30; }
log 'DRAIN_OK'

MUTATED=1
python3 - "$DIR/compose.yml" "$NEW_IMAGE" "$OLD_IMAGE" <<'PY'
import os, sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text(encoding='utf-8')
needle = 'image: ' + sys.argv[3]
if text.count(needle) != 1:
    raise SystemExit('REFUSE image declaration count')
text = text.replace(needle, 'image: ' + sys.argv[2])
temporary = path.with_suffix('.yml.new')
temporary.write_text(text, encoding='utf-8')
os.chmod(temporary, path.stat().st_mode & 0o777)
os.replace(temporary, path)
PY
(cd "$DIR" && docker compose config --quiet)
UP_OUT=$(cd "$DIR" && docker compose up -d --pull never 2>&1) || { log "COMPOSE_UP_FAILED $UP_OUT"; exit 10; }
printf '%s\n' "$UP_OUT"
RESTART_TS=$(date -u +%FT%TZ)

RUNNING=$(docker inspect --format '{{.Config.Image}}' "$CONTAINER")
[ "$RUNNING" = "$NEW_IMAGE" ] || { log "RUNNING_IMAGE_MISMATCH got=$RUNNING"; exit 10; }
RUNNING_ID=$(docker inspect --format '{{.Image}}' "$CONTAINER")
[ "$RUNNING_ID" = "$NEW_IMAGE_ID" ] || { log "RUNNING_IMAGE_ID_MISMATCH got=$RUNNING_ID"; exit 10; }
log 'RUNTIME_IMAGE_OK'
[ "$(docker exec "$CONTAINER" sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)" = "$NEW_BINARY_SHA" ] || {
  log 'CONTAINER_BINARY_SHA_DRIFTED'; exit 10; }
log "RUNTIME_BINARY sha=$NEW_BINARY_SHA container"
[ "$(sha_of "$DIR/config.yaml")" = "$(cut -d' ' -f1 "$BK/config.sha256")" ] || { log 'CONFIG_SHA_DRIFTED'; exit 10; }
log 'CONFIG_PINNED'
wait_readiness || { log 'READINESS_FAILED'; exit 10; }
log 'READINESS_OK'
sha256sum "$DIR"/auth/*.json >"$BK/credentials-post.sha256"
cmp -s "$BK/credentials.sha256" "$BK/credentials-post.sha256" || { log 'AUTH_BYTES_CHANGED'; exit 10; }
log 'AUTH_BYTES_UNCHANGED'
for f in chatgpt-risk.json workbuddy-risk.json antigravity-risk.json; do
  reason=$(python3 -c "import json;print(json.load(open('/var/lib/cpa-admission/$f')).get('hold_reason'))")
  [ "$reason" = "None" ] || { log "LANE_HOLD_APPEARED $f=$reason"; exit 10; }
done
log 'LANE_HOLD_DELTA=none'
if docker logs --since "$RESTART_TS" "$CONTAINER" 2>&1 | grep -qE 'panic:|fatal error:|exec format error|CONFIG_LOAD_FAILED'; then
  log 'START_LOG_FAULT_PATTERN'
  exit 10
fi
log 'START_LOG_CLEAN'

flock -u 8
SUCCESS=1
log "restarts=$(docker inspect --format '{{.RestartCount}}' "$CONTAINER")"
log "SWAP_OK v8.0.61-gemini-raw->v8.0.75-gemini-raw image_id=$NEW_IMAGE_ID backup=$BK"

#requires -Version 7
# Switch the running container onto the freshly rebuilt (and provenance-
# labelled) fork image. The tag already moved, but the container still
# references the old image ID, so restarting alone would keep serving the old
# layers. Drain transaction: exclusive requests.lock, lane-hold preflight,
# backup, recreate, then binary/label/readiness verification with rollback.
param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^sha256:[0-9a-f]{64}$")]
  [string]$OldImageId,
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^sha256:[0-9a-f]{64}$")]
  [string]$NewImageId
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
OLD_ID='__OLD__'
NEW_ID='__NEW__'
IMAGE=cli-proxy-api-fork:v8.0.61-gemini-raw
LEDGER_SHA=$(cat "$DIR/fork-binary.sha256" 2>/dev/null || true)
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK="$DIR/backups/$TS-fork-image-relabel"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
wait_readiness() {
  local deadline=$(($SECONDS + 120))
  while :; do
    python3 "$DIR/cpa-health.py" readiness && return 0
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 3
  done
}

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

CUR_ID=$(docker inspect --format '{{.Image}}' cli-proxy-api)
[ "$CUR_ID" = "$OLD_ID" ] || { log "CURRENT_IMAGE_UNEXPECTED got=$CUR_ID"; exit 10; }
TAG_ID=$(docker image inspect --format '{{.Id}}' "$IMAGE")
[ "$TAG_ID" = "$NEW_ID" ] || { log "TAG_POINTS_ELSEWHERE got=$TAG_ID want=$NEW_ID"; exit 10; }
log "PRE_STATE_OK current=$CUR_ID tag=$TAG_ID ledger=${LEDGER_SHA:-none}"

python3 - <<'PY'
import json
from pathlib import Path
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health.get('traffic_guard') == '/run/cpa-admission/requests.lock'
base = Path('/var/lib/cpa-admission')
lanes = {'chatgpt-risk.json': 'chatgpt-oauth',
         'workbuddy-risk.json': 'workbuddy-oauth',
         'antigravity-risk.json': 'antigravity-oauth'}
for name, lane in lanes.items():
    reason = json.loads((base / name).read_text()).get('hold_reason')
    if reason is None:
        continue
    live = health['lanes'][lane]['state'].get('hold_reason')
    if reason == 'unverified_request' and live is None:
        continue
    raise SystemExit('PRE_HOLD_PRESENT %s=%s live=%s' % (name, reason, live))
print('PRE_HOLDS_CLEAN')
PY

mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$BK/"
sha256sum "$DIR"/auth/*.json >"$BK/credentials.sha256"
chmod 600 "$BK"/*
log "BACKUP dir=$BK"
MUTATED=0
SUCCESS=0
cleanup() {
  local rc=$?
  trap '' EXIT ERR HUP INT TERM
  [ "$SUCCESS" = 1 ] && exit 0
  if [ "$MUTATED" = 1 ]; then
    log "ROLLBACK_BEGIN rc=$rc"
    docker image tag "$OLD_ID" "$IMAGE" && log 'ROLLBACK_TAG_RESTORED' || log 'ROLLBACK_TAG_FAILED'
    (cd "$DIR" && docker compose up -d --force-recreate --pull never) || log 'ROLLBACK_RECREATE_FAILED'
    wait_readiness && log 'ROLLBACK_READINESS_OK' || log 'ROLLBACK_READINESS_FAILED'
    log 'ROLLBACK_DONE'
  fi
  [ "$rc" = 0 ] && rc=1
  exit "$rc"
}
trap cleanup EXIT
trap 'exit $?' ERR HUP INT TERM

exec 8</run/cpa-admission/requests.lock
flock -w 60 -x 8 || { log 'DRAIN_TIMEOUT'; exit 30; }
log 'DRAIN_OK'

PRE_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
MUTATED=1
(cd "$DIR" && docker compose up -d --force-recreate --pull never)
RESTART_TS=$(date -u +%FT%TZ)

RUNNING=$(docker inspect --format '{{.Image}}' cli-proxy-api)
[ "$RUNNING" = "$NEW_ID" ] || { log "IMAGE_ID_MISMATCH got=$RUNNING"; exit 10; }
log "IMAGE_ID_READBACK=OK $NEW_ID"

NEW_STARTED="$PRE_STARTED"
for _ in 1 2 3 4 5 6 7 8; do
  NEW_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
  [ "$NEW_STARTED" != "$PRE_STARTED" ] && break
  sleep 2
done
[ "$NEW_STARTED" != "$PRE_STARTED" ] || { log 'RESTART_NOT_APPLIED'; exit 10; }
log "RESTART_APPLIED started=$NEW_STARTED"
# Bounded poll, never a single sample: this transaction first asserted 1 s
# after the container started and reported PLUGIN_NOT_REGISTERED while the
# plugin was in fact still loading (it registered 2 s later). Same defect class
# as the 2026-11-08 readiness sample; do not reintroduce it.
START_LOG=''
registered=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  START_LOG=$(docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 || true)
  if printf '%s\n' "$START_LOG" | grep -q 'plugin registered plugin_id=workbuddy .*version=0.9.89'; then
    registered=1
    break
  fi
  sleep 2
done
[ "$registered" = 1 ] || { log 'PLUGIN_NOT_REGISTERED'; printf '%s\n' "$START_LOG" | tail -15; exit 10; }
printf '%s\n' "$START_LOG" | grep -m1 'CLIProxyAPI Version: v8.0.61' || {
  log 'VERSION_LINE_MISSING'
  printf '%s\n' "$START_LOG" | tail -15
  exit 10
}
if printf '%s\n' "$START_LOG" | grep -Eiq 'panic|failed to load'; then
  log 'START_LOG_FAULT'
  exit 10
fi
log 'START_LOG_CLEAN version=v8.0.61 plugin=0.9.89'

BIN_SHA=$(docker exec cli-proxy-api sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)
if [ -n "$LEDGER_SHA" ] && [ "$BIN_SHA" != "$LEDGER_SHA" ]; then
  log "BINARY_SHA_DIFFERS_FROM_LEDGER got=$BIN_SHA"
  exit 10
fi
log "BINARY_SHA_OK $BIN_SHA"
BASE_LABEL=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.base.digest"}}' "$NEW_ID")
FORK_LABEL=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.fork-binary.sha256"}}' "$NEW_ID")
[ "$FORK_LABEL" = "$BIN_SHA" ] || { log "LABEL_FORK_MISMATCH got=${FORK_LABEL:-none}"; exit 10; }
log "LABELS_OK base=${BASE_LABEL:-none} fork=$FORK_LABEL"

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
flock -u 8
SUCCESS=1
log "restarts=$(docker inspect --format '{{.RestartCount}}' cli-proxy-api)"
log "RELABEL_OK old=$OLD_ID new=$NEW_ID backup=$BK"
'@
$command = $command.Replace('__OLD__', $OldImageId).Replace('__NEW__', $NewImageId)
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

#requires -Version 7
# Replace BWG CPA fork image v8.1.1-gemini-raw-daily-cap -> v8.1.1-gemini-raw-daily-cap-authiso
# (v8.1.1 + gemini-raw + agy-effort) in one drain-locked, rollback-armed transaction.
param(
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$mode = if ($DryRun) { 'dry-run' } else { 'apply' }

$command = @'
set -Eeuo pipefail
umask 077
MODE='__MODE__'
DIR=/opt/cliproxyapi
CONTAINER=cli-proxy-api
OLD_IMAGE='cli-proxy-api-fork:v8.1.1-gemini-raw-daily-cap'
NEW_IMAGE='cli-proxy-api-fork:v8.1.1-gemini-raw-daily-cap-authiso'
OLD_ID='sha256:9824bf829ebb53dfd61fe327e66bc8d50ca387288deb3fc4d376751e1c3b6bf0'
NEW_ID='sha256:b40bc82f881a875eeb9bf877c6bfa65b5e8c62f3c28156c9209476d38c537fc7'
OLD_BINARY_SHA='e82865333d2b86d9790ee518a50a6b089627aae8529fcd01b7c260b24e6e9c96'
NEW_BINARY_SHA='b4fedb2d884730df04c849e5de8714de368201ae122597ffcc6b293809ea6571'
NEW_BINARY=/root/cpa-v8111-build/CLIProxyAPI-8.1.1-gemini-raw-agy-effort-daily-cap-authiso
CONFIG_SHA='7cde212b36f29062d07a04651b6f5d13776e62e8db5cddabf2f63a464e729773'
WB_CN_SHA='931bf2f2440ab8a85aa304b87d7b1a35fa3d7901e2f2c599a6434bd7aa567c04'
WB_INTL_SHA='8fa992e8c4e36d6af8ab5b347d750d1296ab4b9d1482610470f3f6c8356d11f6'
BASE_DIGEST='sha256:e2f9c436ff992ef5830227ccc9fb67d40246bb1420708fbcd18e1de675e88c98'
WB_CN_PLUGIN="$DIR/plugins/workbuddy-provider.so"
WB_INTL_PLUGIN="$DIR/plugins/workbuddy-ai-provider.so"
COMPOSE="$DIR/compose.yml"
CONTAINER_BIN=/CLIProxyAPI/CLIProxyAPI
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK="$DIR/backups/$TS-swap-v8111-to-authiso"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
sha_of() { sha256sum "$1" | cut -d' ' -f1; }

wait_readiness() {
  local deadline=$((SECONDS + 120))
  while :; do
    if python3 "$DIR/cpa-health.py" readiness; then
      return 0
    fi
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 2
  done
}

compose_ref() {
  python3 - "$COMPOSE" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
matches = re.findall(r"(?m)^[ \t]*image:[ \t]*(\S+)[ \t]*$", text)
if len(matches) != 1:
    raise SystemExit("COMPOSE_IMAGE_DECLARATION_AMBIGUOUS")
print(matches[0])
PY
}

assert_lane_health() {
  local require_idle="$1"
  python3 - "$require_idle" <<'PY'
import json
import sys
import urllib.request

require_idle = sys.argv[1] == "1"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:8318/healthz", timeout=5) as response:
    health = json.load(response)
if health.get("traffic_guard") != "/run/cpa-admission/requests.lock":
    raise SystemExit("TRAFFIC_GUARD_UNEXPECTED")
for name, lane in health.get("lanes", {}).items():
    state = lane.get("state", {})
    if state.get("hold_reason") is not None:
        raise SystemExit("HOLD_PRESENT %s=%s" % (name, state["hold_reason"]))
    if state.get("cooldown_active"):
        raise SystemExit("COOLDOWN_PRESENT %s=%s" % (name, state))
    if require_idle and (state.get("inflight", 0) or state.get("pending", 0)):
        raise SystemExit("QUEUE_NOT_IDLE %s=inflight:%s,pending:%s" % (name, state.get("inflight"), state.get("pending")))
print("LANE_HEALTH_OK idle_checked=%s" % require_idle)
PY
}

dump_auth_disabled_map() {
  python3 - "$DIR/auth" "$1" <<'PY'
import json
import sys
from pathlib import Path

auth_dir = Path(sys.argv[1])
out_path = Path(sys.argv[2])
records = {}
for item in sorted(auth_dir.glob("*.json")):
    data = json.loads(item.read_text(encoding="utf-8"))
    records[item.name] = bool(data.get("disabled", False)) if isinstance(data, dict) else False
out_path.write_text(json.dumps(records, sort_keys=True), encoding="utf-8")
PY
}

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

CUR_REF=$(compose_ref)
[ "$CUR_REF" = "$OLD_IMAGE" ] || { log "COMPOSE_REF_UNEXPECTED got=$CUR_REF want=$OLD_IMAGE"; exit 10; }
CUR_ID=$(docker inspect --format '{{.Image}}' "$CONTAINER")
[ "$CUR_ID" = "$OLD_ID" ] || { log "CURRENT_IMAGE_UNEXPECTED got=$CUR_ID want=$OLD_ID"; exit 10; }
TAG_ID=$(docker image inspect --format '{{.Id}}' "$NEW_IMAGE")
[ "$TAG_ID" = "$NEW_ID" ] || { log "TARGET_TAG_UNEXPECTED got=$TAG_ID want=$NEW_ID"; exit 10; }
docker image inspect "$OLD_ID" >/dev/null || { log 'OLD_IMAGE_MISSING'; exit 10; }
docker image inspect "$NEW_ID" >/dev/null || { log 'NEW_IMAGE_MISSING'; exit 10; }

BASE_LABEL=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.base.digest"}}' "$NEW_ID")
FORK_LABEL=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.fork-binary.sha256"}}' "$NEW_ID")
TAG_LABEL=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.upstream.tag"}}' "$NEW_ID")
[ "$BASE_LABEL" = "$BASE_DIGEST" ] || { log "BASE_LABEL_MISMATCH got=${BASE_LABEL:-none}"; exit 10; }
[ "$FORK_LABEL" = "$NEW_BINARY_SHA" ] || { log "FORK_LABEL_MISMATCH got=${FORK_LABEL:-none}"; exit 10; }
[ "$TAG_LABEL" = 'v8.1.1' ] || { log "UPSTREAM_TAG_LABEL_MISMATCH got=${TAG_LABEL:-none}"; exit 10; }
[ -s "$NEW_BINARY" ] && [ "$(sha_of "$NEW_BINARY")" = "$NEW_BINARY_SHA" ] || { log 'CANDIDATE_BINARY_MISSING_OR_MISMATCHED'; exit 10; }
[ "$(sha_of "$DIR/config.yaml")" = "$CONFIG_SHA" ] || { log 'CONFIG_BASELINE_MISMATCH'; exit 10; }
[ "$(sha_of "$WB_CN_PLUGIN")" = "$WB_CN_SHA" ] || { log 'WB_CN_PLUGIN_BASELINE_MISMATCH'; exit 10; }
[ "$(sha_of "$WB_INTL_PLUGIN")" = "$WB_INTL_SHA" ] || { log 'WB_INTL_PLUGIN_BASELINE_MISMATCH'; exit 10; }
[ "$(docker exec "$CONTAINER" sha256sum "$CONTAINER_BIN" | cut -d' ' -f1)" = "$OLD_BINARY_SHA" ] || { log 'LIVE_BINARY_BASELINE_MISMATCH'; exit 10; }
free_kib=$(df -Pk "$DIR" | awk 'NR==2{print $4}')
[ "$free_kib" -ge 2097152 ] || { log "DISK_LOW free_kib=$free_kib"; exit 10; }
assert_lane_health 0
wait_readiness || { log 'PREFLIGHT_READINESS_FAILED'; exit 10; }
log "PREFLIGHT_OK mode=$MODE current=$CUR_REF current_id=$CUR_ID target=$NEW_IMAGE target_id=$NEW_ID"

if [ "$MODE" = 'dry-run' ]; then
  log 'DRY_RUN_OK no_files_or_containers_changed'
  exit 0
fi

mkdir -m 700 "$BK"
cp -a "$COMPOSE" "$DIR/config.yaml" "$WB_CN_PLUGIN" "$WB_INTL_PLUGIN" "$BK/"
printf '%s\n' "$OLD_ID" >"$BK/image-id"
printf '%s\n' "$OLD_IMAGE" >"$BK/image-ref"
printf '%s\n' "$NEW_ID" >"$BK/target-image-id"
printf '%s\n' "$OLD_BINARY_SHA" >"$BK/old-binary-sha"
sha256sum "$DIR"/auth/*.json >"$BK/credentials.sha256"
dump_auth_disabled_map "$BK/auth-disabled-pre.json"
chmod 600 "$BK"/*
cmp -s "$BK/compose.yml" "$COMPOSE" || { log 'BACKUP_COMPOSE_DIFFERS'; exit 10; }
cmp -s "$BK/config.yaml" "$DIR/config.yaml" || { log 'BACKUP_CONFIG_DIFFERS'; exit 10; }
log "BACKUP_READY dir=$BK"

MUTATED=0
SUCCESS=0
ROLLED_BACK=0
rollback() {
  local rc=$?
  trap '' EXIT ERR HUP INT TERM
  [ "$SUCCESS" = 1 ] && exit 0
  if [ "$MUTATED" = 1 ] && [ "$ROLLED_BACK" = 0 ]; then
    ROLLED_BACK=1
    log "ROLLBACK_BEGIN rc=$rc"
    cp -a "$BK/compose.yml" "$COMPOSE.rollback"
    mv -f "$COMPOSE.rollback" "$COMPOSE"
    (cd "$DIR" && docker compose up -d --force-recreate --pull never) || log 'ROLLBACK_RECREATE_FAILED'
    if [ "$(docker inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null || true)" = "$OLD_ID" ]; then
      log 'ROLLBACK_IMAGE_ID_OK'
    else
      log 'ROLLBACK_IMAGE_ID_FAILED'
    fi
    wait_readiness && log 'ROLLBACK_READINESS_OK' || log 'ROLLBACK_READINESS_FAILED'
    log "ROLLBACK_DONE backup=$BK"
  fi
  [ "$rc" = 0 ] && rc=1
  exit "$rc"
}
trap rollback EXIT
trap 'exit $?' ERR HUP INT TERM

exec 8</run/cpa-admission/requests.lock
flock -w 120 -x 8 || { log 'DRAIN_TIMEOUT'; exit 30; }
log 'DRAIN_OK'
assert_lane_health 1

MUTATED=1
python3 - "$COMPOSE" "$OLD_IMAGE" "$NEW_IMAGE" <<'PY'
import os
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
old = sys.argv[2]
new = sys.argv[3]
text = path.read_text(encoding="utf-8")
pattern = r"(?m)^([ \t]*image:[ \t]*)" + re.escape(old) + r"([ \t]*)$"
text, count = re.subn(pattern, r"\g<1>" + new + r"\g<2>", text)
if count != 1:
    raise SystemExit("COMPOSE_REPLACEMENT_COUNT=%d" % count)
temporary = path.with_suffix(".yml.new")
temporary.write_text(text, encoding="utf-8")
os.chmod(temporary, path.stat().st_mode & 0o777)
os.replace(temporary, path)
PY
(cd "$DIR" && docker compose config --quiet)
PRE_RESTART_TS=$(date -u -d '2 seconds ago' +%FT%TZ)
(cd "$DIR" && docker compose up -d --force-recreate --pull never)

RUNNING_ID=$(docker inspect --format '{{.Image}}' "$CONTAINER")
[ "$RUNNING_ID" = "$NEW_ID" ] || { log "IMAGE_ID_MISMATCH got=$RUNNING_ID"; exit 10; }
log "IMAGE_ID_READBACK_OK $RUNNING_ID"

START_LOG=''
VERSION_OK=0
PLUGIN_OK=0
for _ in $(seq 1 30); do
  START_LOG=$(docker logs --since "$PRE_RESTART_TS" "$CONTAINER" 2>&1 || true)
  printf '%s\n' "$START_LOG" | grep -q "CLIProxyAPI Version: v8.1.1" && VERSION_OK=1 || true
  printf '%s\n' "$START_LOG" | grep -q "plugin registered plugin_id=workbuddy-provider .*version=0.21.30" && PLUGIN_OK=1 || true
  [ "$VERSION_OK" = 1 ] && [ "$PLUGIN_OK" = 1 ] && break
  sleep 1
done
[ "$VERSION_OK" = 1 ] || { log 'VERSION_LINE_MISSING'; printf '%s\n' "$START_LOG" | tail -20; exit 10; }
[ "$PLUGIN_OK" = 1 ] || { log 'PLUGIN_REGISTRATION_MISSING'; printf '%s\n' "$START_LOG" | tail -20; exit 10; }
if printf '%s\n' "$START_LOG" | grep -q "plugin registered plugin_id=workbuddy-ai-provider"; then
  log 'UNEXPECTED_INTL_PLUGIN_REGISTERED'
  exit 10
fi
if printf '%s\n' "$START_LOG" | grep -Eiq 'panic:|fatal error:|failed to load|exec format error|CONFIG_LOAD_FAILED'; then
  log 'START_LOG_FAULT_PATTERN'
  exit 10
fi
log 'START_LOG_CLEAN version=v8.1.1 plugin=workbuddy-provider:0.21.27 intl=closed_dark'

[ "$(docker exec "$CONTAINER" sha256sum "$CONTAINER_BIN" | cut -d' ' -f1)" = "$NEW_BINARY_SHA" ] || {
  log 'CONTAINER_BINARY_SHA_MISMATCH'
  exit 10
}
log "RUNTIME_BINARY_OK sha=$NEW_BINARY_SHA"
wait_readiness || { log 'READINESS_FAILED'; exit 10; }
log 'READINESS_OK'

[ "$(sha_of "$DIR/config.yaml")" = "$CONFIG_SHA" ] || { log 'CONFIG_SHA_DRIFT'; exit 10; }
[ "$(sha_of "$WB_CN_PLUGIN")" = "$WB_CN_SHA" ] || { log 'WB_CN_PLUGIN_SHA_DRIFT'; exit 10; }
[ "$(sha_of "$WB_INTL_PLUGIN")" = "$WB_INTL_SHA" ] || { log 'WB_INTL_PLUGIN_SHA_DRIFT'; exit 10; }
dump_auth_disabled_map "$BK/auth-disabled-post.json"
cmp -s "$BK/auth-disabled-pre.json" "$BK/auth-disabled-post.json" || { log 'AUTH_DISABLED_SET_CHANGED'; exit 10; }
sha256sum "$DIR"/auth/*.json >"$BK/credentials-post.sha256"
if cmp -s "$BK/credentials.sha256" "$BK/credentials-post.sha256"; then
  log 'AUTH_BYTES_UNCHANGED'
else
  log 'AUTH_TOKEN_REFRESHED_SAFE disabled_set_unchanged=true'
fi
printf '%s\n' "$NEW_BINARY_SHA" >"$DIR/fork-binary.sha256"
chmod 600 "$DIR/fork-binary.sha256"
LANE_OK=0
for _ in $(seq 1 15); do
  if assert_lane_health 1 >/dev/null 2>&1; then LANE_OK=1; break; fi
  sleep 2
done
[ "$LANE_OK" = 1 ] || { log 'LANE_HEALTH_TIMEOUT'; exit 10; }
log 'LANE_STATE_DELTA=none'

flock -u 8
SUCCESS=1
log "SWAP_OK old=$OLD_IMAGE old_id=$OLD_ID new=$NEW_IMAGE new_id=$NEW_ID binary=$NEW_BINARY_SHA backup=$BK"
'@
$command = $command.Replace('__MODE__', $mode)
if ($command -match '__MODE__') { throw 'Unfilled placeholder.' }
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

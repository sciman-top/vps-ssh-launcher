#!/usr/bin/env bash
# Authorized bwg CPA v8.0.50 -> v8.0.51 joint canary, 2026-11-02.
# Plugin stays 0.9.84 (upstream v0.8.92 is still latest; ABI compatibility is
# proven by the isolated load fixture on the v8.0.51 binary). Immutable digest,
# traffic-guard drain, credential-safe backups, plugin registration and
# safety-pin assertions, rollback, and hold delta.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LOG="$DIR/canary-v8051-20261102.log"
CUR=v8.0.50
CUR_DIGEST=sha256:21f823945675a80b219bddc632a3716079b5a6fcecf32c840cad29dba5754e84
TARGET=v8.0.51
DIGEST=sha256:15d13889e1a39a688be306dfbf2f6cf9003e99cd913b5070fa463f8ab2aa0031
IMAGE="eceasy/cli-proxy-api:$TARGET@$DIGEST"
PLUGIN="$DIR/plugins/workbuddy.so"
PLUGIN_SHA=bbf57c0ad33a6de33507de9c20fedc505edfa93fecbee05507834255f1e53368
PLUGIN_VERSION=0.9.84
MIN_FREE_KIB=2097152

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
docker() {
  local budget=30
  if [[ "$1" == pull ]]; then budget=600; fi
  if [[ "$1" == compose ]]; then budget=180; fi
  timeout --signal=TERM --kill-after=15s "${budget}s" docker "$@"
}
health() { python3 "$DIR/cpa-health.py" "$1"; }
verify_traffic_guard() {
  python3 - <<'PY'
import json
import os
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(
    os.environ.get("TRAFFIC_GUARD_URL", "http://127.0.0.1:8318/healthz"),
    timeout=5,
) as response:
    data = json.load(response)
if data.get("traffic_guard") != os.environ.get(
    "TRAFFIC_GUARD_LOCK", "/run/cpa-admission/requests.lock"
):
    raise SystemExit("TRAFFIC_GUARD_UNAVAILABLE")
PY
}
lane_hold_state() {
  python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

base = Path(os.environ.get("LANE_HOLD_STATE_DIR", "/var/lib/cpa-admission"))
held = []
for name in ("chatgpt-risk.json", "workbuddy-risk.json", "antigravity-risk.json"):
    try:
        data = json.loads((base / name).read_text())
        reason = data.get("hold_reason")
    except (OSError, ValueError, AttributeError):
        print(f"HOLD_STATE_UNREADABLE file={name}", file=sys.stderr)
        raise SystemExit(2)
    if reason is not None:
        held.append(f"{name}={reason}")
print("\n".join(held))
PY
}
compose_image() {
  python3 - "$DIR/compose.yml" <<'PY'
import re
import sys
from pathlib import Path

matches = re.findall(
    r'image:\s*(eceasy/cli-proxy-api:[^\s]+)\s*(?:\n|$)',
    Path(sys.argv[1]).read_text(),
)
if len(matches) != 1:
    raise SystemExit("REFUSE unexpected image declaration")
print(matches[0])
PY
}

test "$(compose_image)" = "eceasy/cli-proxy-api:$CUR@$CUR_DIGEST" || {
  log 'REFUSE compose pin drift'
  exit 20
}
sha256sum "$PLUGIN" | grep -qF "$PLUGIN_SHA" || {
  log 'REFUSE live plugin build drift'
  exit 20
}
docker image inspect "eceasy/cli-proxy-api:$CUR@$CUR_DIGEST" >/dev/null || {
  log 'REFUSE rollback image absent'
  exit 20
}
AVAIL=$(df -Pk "$DIR" | awk 'NR == 2 {print $4}')
if (( AVAIL < MIN_FREE_KIB )); then
  log "DEFER insufficient_free_space free_kib=$AVAIL minimum_free_kib=$MIN_FREE_KIB"
  exit 1
fi
verify_traffic_guard
log "PREFLIGHT image=$CUR->$TARGET plugin=$PLUGIN_VERSION unchanged"

docker pull "$IMAGE" >>"$LOG" 2>&1
docker image inspect "$IMAGE" >/dev/null
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK="$DIR/backups/$TS-from-$CUR-to-$TARGET-plugin-$PLUGIN_VERSION-unchanged"
mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$DIR/config.yaml" "$BK/"
cp -a "$PLUGIN" "$BK/workbuddy.so"
find "$DIR/auth" -maxdepth 1 -type f -name '*.json' -print0 |
  sort -z | xargs -0 -r sha256sum >"$BK/auth.sha256"
chmod 600 "$BK/auth.sha256"
log "BACKUP dir=$BK"

MUTATED=0
rollback() {
  local code=${1:-$?} rollback_failed=0
  trap - ERR HUP INT TERM
  if [[ "$MUTATED" == 1 ]]; then
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || rollback_failed=1
    sleep 2
    if [[ "$rollback_failed" == 0 ]] &&
       (cd "$DIR" && docker compose up -d --pull never >>"$LOG" 2>&1) &&
       health readiness; then
      log "ROLLBACK restored=$CUR plugin=$PLUGIN_VERSION backup=$BK"
    else
      log 'ROLLBACK_FAILED'
    fi
  fi
  [[ "$code" != 0 ]] || code=1
  exit "$code"
}
trap rollback ERR HUP INT TERM

verify_traffic_guard
exec 8<"/run/cpa-admission/requests.lock"
if ! flock -w 60 -x 8; then
  log 'DEFER active requests did not drain within 60s; image unchanged'
  exit 75
fi
verify_traffic_guard
if ! PRE_HOLDS=$(lane_hold_state); then
  log 'DEFER admission held state unreadable; image unchanged'
  exit 1
fi
MUTATED=1
python3 - "$DIR/compose.yml" "$IMAGE" <<'PY'
import os
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text, count = re.subn(
    r"image:\s*eceasy/cli-proxy-api:[^\s]+",
    "image: " + sys.argv[2],
    path.read_text(),
)
if count != 1:
    raise SystemExit("REFUSE image replacement count")
temporary = path.with_suffix(".yml.new")
temporary.write_text(text)
os.chmod(temporary, path.stat().st_mode & 0o777)
os.replace(temporary, path)
PY
RESTART_TS=$(date -u +%FT%TZ)
(cd "$DIR" &&
  docker compose config --quiet &&
  docker compose up -d --pull never >>"$LOG" 2>&1)
log "RECREATED image=$TARGET at=$RESTART_TS plugin=$PLUGIN_VERSION (not replaced)"
sleep 3
REGISTERED=1
for _ in 1 2 3 4 5 6; do
  if docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
     grep -q "plugin registered plugin_id=workbuddy .*version=$PLUGIN_VERSION"; then
    REGISTERED=0
    break
  fi
  sleep 5
done
if [[ "$REGISTERED" != 0 ]]; then
  log "REFUSE plugin registration missing version=$PLUGIN_VERSION"
  rollback 20
fi
docker logs --since "$RESTART_TS" cli-proxy-api >"$BK/new-container.log" 2>&1
grep -m1 'CLIProxyAPI Version: v8.0.51' "$BK/new-container.log" >/dev/null || {
  log 'REFUSE host version readback missing v8.0.51'
  rollback 23
}
grep -q 'workbuddy: account_max_concurrent=1 applied' "$BK/new-container.log" || {
  log 'REFUSE WorkBuddy per-account concurrency pin missing'
  rollback 21
}
python3 - "$DIR/config.yaml" <<'PY'
import sys
import yaml

config = yaml.safe_load(open(sys.argv[1]))
workbuddy = config["plugins"]["configs"]["workbuddy"]
assert workbuddy["identity_switch_on_429"] is False
assert workbuddy["account_max_concurrent"] == 1
assert workbuddy["scheduler_mode"] == "credits"
assert workbuddy["models_cn"] == "deepseek-v4.1-flash"
assert workbuddy["models_intl"] == "deepseek-v4.1-flash"
print("WORKBUDDY_SAFETY_PINS=OK")
PY
if ! health readiness; then
  log 'REFUSE readiness failed'
  rollback 22
fi
log "PLUGIN_REGISTERED version=$PLUGIN_VERSION"
log 'READINESS_OK'
sha256sum -c "$BK/auth.sha256" >"$BK/auth-check.log" 2>&1 || {
  log 'UNVERIFIED auth bytes changed'
  exit 10
}
log 'AUTH_BYTES_UNCHANGED'
if ! POST_HOLDS=$(lane_hold_state); then
  log 'UNVERIFIED durable hold state unreadable'
  exit 10
fi
NEW_HOLDS=""
if [[ -n "$POST_HOLDS" ]]; then
  NEW_HOLDS=$(comm -13 <(sort <<<"$PRE_HOLDS") <(sort <<<"$POST_HOLDS") || true)
fi
if [[ -n "$NEW_HOLDS" ]]; then
  log "UNVERIFIED lane hold appeared across restart lanes=${NEW_HOLDS//$'\n'/,}"
  exit 10
fi
log 'LANE_HOLD_DELTA=none'
docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE" |
  grep -Fq "eceasy/cli-proxy-api@$DIGEST" || {
  log 'UNVERIFIED repo digest readback'
  exit 10
}
log 'DIGEST_READBACK=OK'
trap - ERR HUP INT TERM
log "OK: updated $CUR -> $TARGET plugin=$PLUGIN_VERSION unchanged backup=$BK"
docker inspect --format \
  'CONTAINER status={{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}}' \
  cli-proxy-api

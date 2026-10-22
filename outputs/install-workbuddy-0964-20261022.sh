#!/usr/bin/env bash
# Manual maintenance window: swap workbuddy plugin to local 0.9.64 build
# (upstream v0.8.78 source + selected-account + account-failover patches;
# 0.9.63 extends 11128 trigger-phrase sanitisation to every role incl. user/
# assistant history, tool_call arguments and reasoning mirrors; 0.9.64 fixes the
# registration metadata repo URL). CPA v8.0.38 image pin is untouched.
# Drain is enforced by the admission requests.lock exclusive flock (not by a
# healthz idle snapshot). Exit: 0 verified | 1 deferred/rolled back |
# 10 UNVERIFIED new lane hold (new plugin retained) | 20 local contract
# refusal | 75 lock busy / drain timeout.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
SRC=/root/cpa-workbuddy-upgrade-20261022/cpa-multi-plugins-0.8.78/plugins/workbuddy/workbuddy.so
EXPECTED_SHA=8d531f30705988256f75cf86082e53acd0879ca8d453b862c718a469228d79e6
LOG="$DIR/canary-plugin-0964.log"
SO="$DIR/plugins/workbuddy.so"

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
health() { python3 "$DIR/cpa-health.py" "$1"; }

export TRAFFIC_GUARD_URL="${TRAFFIC_GUARD_URL:-http://127.0.0.1:8318/healthz}"
export TRAFFIC_GUARD_LOCK="${TRAFFIC_GUARD_LOCK:-/run/cpa-admission/requests.lock}"
verify_traffic_guard() {
  python3 - <<'PY'
import json
import os
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(os.environ['TRAFFIC_GUARD_URL'], timeout=5) as response:
    data = json.load(response)
if data.get('traffic_guard') != os.environ['TRAFFIC_GUARD_LOCK']:
    raise SystemExit('TRAFFIC_GUARD_UNAVAILABLE')
PY
}
export LANE_HOLD_STATE_DIR="${LANE_HOLD_STATE_DIR:-/var/lib/cpa-admission}"
lane_hold_state() {
  python3 - <<'PY'
import json, os, sys
from pathlib import Path

base = Path(os.environ['LANE_HOLD_STATE_DIR'])
held = []
for name in ('chatgpt-risk.json', 'workbuddy-risk.json', 'antigravity-risk.json'):
    try:
        data = json.loads((base / name).read_text())
        reason = data.get('hold_reason')
    except (OSError, ValueError, AttributeError):
        print(f'HOLD_STATE_UNREADABLE file={name}', file=sys.stderr)
        raise SystemExit(2)
    if reason is not None:
        held.append(f'{name}={reason}')
print('\n'.join(held))
PY
}

[[ -s "$SRC" ]] || { log 'REFUSE build artifact missing'; exit 20; }
NEW_SHA=$(sha256sum "$SRC" | awk '{print $1}')
[[ "$NEW_SHA" == "$EXPECTED_SHA" ]] || {
  log "REFUSE artifact sha mismatch got=$NEW_SHA want=$EXPECTED_SHA"; exit 20
}
grep -qF './plugins:/CLIProxyAPI/plugins' "$DIR/compose.yml" || {
  log 'REFUSE plugins mount drift'; exit 20
}

OLD_SHA=$(sha256sum "$SO" | awk '{print $1}')
log "PREFLIGHT old_so=$OLD_SHA new_so=$NEW_SHA"

# Acquire the admission drain lock. Once held, admission answers new requests
# with maintenance_in_progress and existing requests have finished. Refuse an
# old admission build that cannot prove the guard rather than trusting an idle
# snapshot.
verify_traffic_guard
exec 8<"$TRAFFIC_GUARD_LOCK"
if ! flock -w 60 -x 8; then
  log 'DEFER active requests did not drain within 60s; plugin unchanged'
  exit 75
fi
verify_traffic_guard
if ! PRE_HOLDS=$(lane_hold_state); then
  log 'DEFER admission durable state unreadable before swap; plugin unchanged'
  exit 1
fi
log "PRE_HOLDS=${PRE_HOLDS//$'\n'/,}"

python3 - <<'PY' >>"$LOG" 2>&1 || true
import json, urllib.request
h=json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz',timeout=5))
busy=[k for k,v in h.get('lanes',{}).items() if v['state']['inflight'] or v['state']['pending']]
print('ADMISSION_SNAPSHOT busy_lanes=' + (','.join(busy) if busy else 'none'))
PY

docker cp "$SRC" cli-proxy-api:/tmp/workbuddy-new.so >/dev/null
DEPS=$(docker exec cli-proxy-api sh -c 'if command -v ldd >/dev/null 2>&1; then ldd /tmp/workbuddy-new.so >/dev/null 2>&1 && echo LDD_OK; else echo NO_LDD; fi; rm -f /tmp/workbuddy-new.so')
if [[ "$DEPS" != *LDD_OK && "$DEPS" != *NO_LDD ]]; then
  log "DEFER container dependency check failed deps=$DEPS"; exit 1
fi
log "CONTAINER_DEPS $DEPS"

BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-plugin-0962-to-0964"
mkdir -m 700 "$BK"
cp -a "$SO" "$BK/"
log "BACKUP dir=$BK"

rollback() {
  local code=${1:-$?} rollback_failed=0
  trap - ERR INT TERM
  cp -a "$BK/workbuddy.so" "$DIR/plugins/.workbuddy.so.rollback" || rollback_failed=1
  if [[ "$rollback_failed" == 0 ]]; then
    chown --reference="$SO" "$DIR/plugins/.workbuddy.so.rollback" 2>/dev/null || true
    mv -f "$DIR/plugins/.workbuddy.so.rollback" "$SO" || rollback_failed=1
  fi
  sleep 3
  if [[ "$rollback_failed" == 0 ]] &&
     (cd "$DIR" && docker compose restart cli-proxy-api >>"$LOG" 2>&1) &&
     health readiness; then
    log "ROLLBACK restored old_so=$OLD_SHA backup=$BK"
  else
    log "ROLLBACK_FAILED backup=$BK"
  fi
  exit "$code"
}

install -m "$(stat -c %a "$SO")" "$SRC" "$DIR/plugins/.workbuddy.so.new"
chown --reference="$SO" "$DIR/plugins/.workbuddy.so.new"
mv -f "$DIR/plugins/.workbuddy.so.new" "$SO"
log 'REPLACED via rename'

trap rollback ERR INT TERM
(cd "$DIR" && docker compose restart cli-proxy-api >>"$LOG" 2>&1)
REG=""
for _ in $(seq 1 30); do
  REG=$(docker logs --since 5m cli-proxy-api 2>&1 | grep -oE 'plugin registered plugin_id=workbuddy plugin_name=workbuddy version=[0-9.]+' | tail -1 || true)
  [[ "$REG" == *version=0.9.64 ]] && break
  sleep 2
done
if [[ "$REG" != *version=0.9.64 ]]; then
  log "DEFER registration_unverified reg='${REG:-none}'; rolling back"
  rollback 1
fi
if ! health readiness; then
  log 'DEFER readiness failed after swap; rolling back'
  rollback 1
fi
trap - ERR INT TERM

if ! POST_HOLDS=$(lane_hold_state); then
  log "UNVERIFIED admission durable state unreadable after swap; retained sha=$NEW_SHA backup=$BK"
  exit 10
fi
NEW_HOLDS=""
if [[ -n "$POST_HOLDS" ]]; then
  NEW_HOLDS=$(comm -13 <(sort <<<"$PRE_HOLDS") <(sort <<<"$POST_HOLDS") || true)
fi
if [[ -n "$NEW_HOLDS" ]]; then
  log "UNVERIFIED lane hold appeared across the container restart lanes=${NEW_HOLDS//$'\n'/,} retained sha=$NEW_SHA backup=$BK; recover per cpa-gateway.md manual release"
  exit 10
fi
log "OK: plugin swapped to 0.9.64-local sha=$NEW_SHA backup=$BK reg='$REG'"
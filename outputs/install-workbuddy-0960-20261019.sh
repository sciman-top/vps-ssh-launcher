#!/usr/bin/env bash
# Manual maintenance window: swap workbuddy plugin to local 0.9.60 build
# (upstream v0.8.72 source + selected-account + account-failover patches;
# intl-system-first patch retired, superseded by upstream INTL gates).
# CPA v8.0.34 image pin is untouched. Exit: 0 verified | 1 deferred/rolled
# back | 20 local contract refusal | 75 lock busy.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
SRC=/root/cpa-workbuddy-upgrade-20261019/cpa-multi-plugins-0.8.72/plugins/workbuddy/workbuddy.so
LOG="$DIR/canary-plugin-0960.log"
SO="$DIR/plugins/workbuddy.so"

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
health() { python3 "$DIR/cpa-health.py" "$1"; }

[[ -s "$SRC" ]] || { log 'REFUSE build artifact missing'; exit 20; }
grep -qF './plugins:/CLIProxyAPI/plugins' "$DIR/compose.yml" || {
  log 'REFUSE plugins mount drift'; exit 20
}
python3 - <<'PY'
import json, urllib.request
h=json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz',timeout=5))
assert h['status']=='ok'
assert all(not v['state']['inflight'] and not v['state']['pending'] for v in h['lanes'].values()), 'REFUSE active traffic'
print('ADMISSION_IDLE_OK')
PY
OLD_SHA=$(sha256sum "$SO" | awk '{print $1}')
NEW_SHA=$(sha256sum "$SRC" | awk '{print $1}')
log "PREFLIGHT old_so=$OLD_SHA new_so=$NEW_SHA"

docker cp "$SRC" cli-proxy-api:/tmp/workbuddy-new.so >/dev/null
DEPS=$(docker exec cli-proxy-api sh -c 'if command -v ldd >/dev/null 2>&1; then ldd /tmp/workbuddy-new.so >/dev/null 2>&1 && echo LDD_OK; else echo NO_LDD; fi; rm -f /tmp/workbuddy-new.so')
if [[ "$DEPS" != *LDD_OK && "$DEPS" != *NO_LDD ]]; then
  log "DEFER container dependency check failed deps=$DEPS"; exit 1
fi
log "CONTAINER_DEPS $DEPS"

BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-plugin-0958p-to-0960p"
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

install -m $(stat -c %a "$SO") "$SRC" "$DIR/plugins/.workbuddy.so.new"
chown --reference="$SO" "$DIR/plugins/.workbuddy.so.new"
mv -f "$DIR/plugins/.workbuddy.so.new" "$SO"
log 'REPLACED via rename'

MUTATED=1
trap rollback ERR INT TERM
(cd "$DIR" && docker compose restart cli-proxy-api >>"$LOG" 2>&1)
sleep 4
REG=$(docker logs --since 2m cli-proxy-api 2>&1 | grep -oE 'plugin registered plugin_id=workbuddy plugin_name=workbuddy version=[0-9.]+' | tail -1 || true)
if [[ "$REG" != *version=0.9.60 ]]; then
  log "DEFER registration_unverified reg='${REG:-none}'; rolling back"
  rollback 1
fi
trap - ERR INT TERM
health readiness
log "OK: plugin swapped to 0.9.60-local sha=$NEW_SHA backup=$BK reg='$REG'"

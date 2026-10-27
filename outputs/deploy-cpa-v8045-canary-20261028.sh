#!/usr/bin/env bash
# Authorized bwg v8.0.45 host-only canary, 2026-10-28.
# Plugin stays at the in-production WorkBuddy 0.9.77 build (v0.8.87 is the
# latest upstream plugin release; no rebuild this round). No config mutation:
# identity_switch_on_429=false, account_max_concurrent=1 and the CN/INTL pins
# are already in the live config and are only re-asserted read-only here.
# No synthetic OAuth generation probes.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LOG="$DIR/canary-v8045.log"
CUR=v8.0.44
CUR_DIGEST=sha256:be71a92720e8ce9aa51fc24a51bf100af29f59cbd6b5f0d0bbfa4c7ca90e83a2
TARGET=v8.0.45
DIGEST=sha256:8011162530aaac81b3318b7310b1a4ab8bf9e58442e76a53b87b08378605e41a
MIN_FREE_KIB=2097152
PLUGIN="$DIR/plugins/workbuddy.so"
PLUGIN_SHA=3920cbf4dd96f0a38e20c1c73f6e77a0a88d4b4d3b37f358d923e56bf6228c8f
PLUGIN_VERSION=0.9.77

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
health() { python3 "$DIR/cpa-health.py" "$1"; }
# Bound every Docker client call, including rollback. A stalled pull must not
# monopolise the shared maintenance lock (same budgets as the updater).
docker() {
  local budget=30
  if [[ "$1" == pull ]]; then budget=600; fi
  if [[ "$1" == compose ]]; then budget=180; fi
  timeout --signal=TERM --kill-after=15s "${budget}s" docker "$@"
}

# Fixture-overridable targets, production literals by default (same contract
# as the updater; the doctor asserts the literals independently).
verify_traffic_guard() {
  python3 - <<'PY'
import json
import os
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(os.environ.get('TRAFFIC_GUARD_URL', 'http://127.0.0.1:8318/healthz'), timeout=5) as response:
    data = json.load(response)
if data.get('traffic_guard') != os.environ.get('TRAFFIC_GUARD_LOCK', '/run/cpa-admission/requests.lock'):
    raise SystemExit('TRAFFIC_GUARD_UNAVAILABLE')
PY
}
# Post-restart lane-hold delta reads admission's durable state files. The
# claude tombstone (authentication_required) is deliberately absent: it is an
# unconfigured-lane historical marker, inert by design (doctor ==lane-hold==).
lane_hold_state() {
  python3 - <<'PY'
import json, os, sys
from pathlib import Path

base = Path(os.environ.get('LANE_HOLD_STATE_DIR', '/var/lib/cpa-admission'))
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

grep -qF "image: eceasy/cli-proxy-api:$CUR@$CUR_DIGEST" "$DIR/compose.yml" || {
  log 'REFUSE compose pin drift'
  exit 20
}
# Host-only canary: the plugin must be the exact reviewed build before and
# after; this transaction never replaces it.
if ! sha256sum "$PLUGIN" | grep -qF "$PLUGIN_SHA"; then
  log 'REFUSE plugin build drift'
  exit 20
fi
python3 - <<'PY'
import json, urllib.request
h=json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz',timeout=5))
assert h['status']=='ok'
print('ADMISSION_SNAPSHOT active=' + str(sum(v['state']['inflight'] for v in h['lanes'].values())) + '; exclusive drain required before mutation')
print('ADMISSION_PREFLIGHT_OK')
PY
log "PREFLIGHT pin=$CUR@$CUR_DIGEST plugin=$PLUGIN_VERSION@$PLUGIN_SHA target=$TARGET"
docker inspect --format \
  'CONTAINER started={{.State.StartedAt}} restarts={{.RestartCount}} status={{.State.Status}}' \
  cli-proxy-api
AVAIL=$(df -Pk "$DIR" | awk 'NR == 2 {print $4}')
if (( AVAIL < MIN_FREE_KIB )); then
  log "DEFER insufficient_free_space free_kib=$AVAIL minimum_free_kib=$MIN_FREE_KIB"
  exit 1
fi
PRE_GENERATION=0
CPA_HEALTH_NO_OAUTH=1 health generation || PRE_GENERATION=$?
if [[ "$PRE_GENERATION" == 10 ]]; then
  health readiness
  log 'PRE_GENERATION UNVERIFIED upstream unavailable; readiness verified'
elif [[ "$PRE_GENERATION" != 0 ]]; then
  log 'DEFER pre_generation_local_failure'
  exit "$PRE_GENERATION"
else
  log 'PRE_GENERATION OK'
fi

BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-from-$CUR-canary-$TARGET"
mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$DIR/config.yaml" "$BK/"
sha256sum "$PLUGIN" >"$BK/plugin.sha256"
# Snapshot credential bytes without printing filenames or credential contents.
find "$DIR/auth" -maxdepth 1 -type f -name '*.json' -print0 | sort -z | xargs -0 -r sha256sum >"$BK/auth.sha256"
docker image inspect "eceasy/cli-proxy-api:$CUR@$CUR_DIGEST" >/dev/null || {
  log 'DEFER rollback_image_not_local'
  exit 1
}
log "BACKUP dir=$BK"

MUTATED=0
rollback() {
  local code=${1:-$?} rollback_failed=0
  trap - ERR HUP INT TERM
  if [[ "$MUTATED" == 1 ]]; then
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || rollback_failed=1
    sleep 3
    if [[ "$rollback_failed" == 0 ]] &&
       (cd "$DIR" && docker compose up -d --pull never >>"$LOG" 2>&1) &&
       health readiness; then
      log "ROLLBACK restored=$CUR backup=$BK"
    else
      log 'ROLLBACK_FAILED backup=$BK'
    fi
  fi
  [[ "$code" != 0 ]] || code=1
  exit "$code"
}
trap rollback ERR HUP INT TERM

IMAGE="eceasy/cli-proxy-api:$TARGET@$DIGEST"
docker pull "$IMAGE" >>"$LOG" 2>&1
docker image inspect "$IMAGE" >/dev/null
# Once acquired, existing requests have finished and new admission requests
# receive maintenance_in_progress until this process (including rollback)
# exits. Refuse old admission builds rather than trusting an idle snapshot.
verify_traffic_guard
exec 8<"/run/cpa-admission/requests.lock"
if ! flock -w 300 -x 8; then
  log 'DEFER: active requests did not drain within 300s; image unchanged'
  exit 75
fi
verify_traffic_guard
# Baseline for the post-restart hold check: a durable hold that already exists
# before the update belongs to the doctor's ==lane-hold== gate, not to this
# transaction. Only holds that appear across OUR container restart fail it.
if ! PRE_HOLDS=$(lane_hold_state); then
  log 'DEFER: admission durable state unreadable before update; image unchanged'
  exit 1
fi
MUTATED=1
python3 - "$DIR/compose.yml" "$IMAGE" <<'PY'
import os, re, sys
from pathlib import Path
p = Path(sys.argv[1])
text, n = re.subn(r'image:\s*eceasy/cli-proxy-api:[^\s]+', 'image: '+sys.argv[2], p.read_text())
if n != 1:
    raise SystemExit('REFUSE image replacement count')
tmp = p.with_suffix('.yml.new')
tmp.write_text(text)
os.chmod(tmp, p.stat().st_mode & 0o777)
os.replace(tmp, p)
PY
RESTART_TS="$(date -u +%FT%TZ)"
(cd "$DIR" && docker compose config --quiet && docker compose up -d --pull never >>"$LOG" 2>&1)
log "RECREATED target=$TARGET at=$RESTART_TS"
sleep 3
# Joint canary gate: the unchanged c-shared plugin must register on the new
# host before any acceptance. A host that silently drops workbuddy is a local
# contract failure (rollback), not an upstream condition.
REGISTERED=1
for _ in 1 2 3 4; do
  if docker logs cli-proxy-api --since "$RESTART_TS" 2>&1 |
     grep -q "plugin registered plugin_id=workbuddy .*version=$PLUGIN_VERSION"; then
    REGISTERED=0
    break
  fi
  sleep 5
done
if [[ "$REGISTERED" != 0 ]]; then
  log "REFUSE post_restart_plugin_registration plugin_id=workbuddy version=$PLUGIN_VERSION; rolling back retained=$TARGET backup=$BK"
  rollback 20
fi
log "PLUGIN_REGISTERED plugin_id=workbuddy version=$PLUGIN_VERSION"
docker logs cli-proxy-api --since "$RESTART_TS" >"$BK/new-container.log" 2>&1
# Read-only policy re-assertion: no config mutation this round, so both the
# parsed values and the runtime-applied cap line are checked as-is.
python3 - "$DIR/config.yaml" <<'PY'
import sys,yaml
w=yaml.safe_load(open(sys.argv[1]))['plugins']['configs']['workbuddy']
assert w['identity_switch_on_429'] is False
assert w['account_max_concurrent']==1
print('IDENTITY_SWITCH_CONFIG=false')
PY
! grep -q 'identity_switch_on_429 applied' "$BK/new-container.log"
grep -q 'workbuddy: account_max_concurrent=1 applied' "$BK/new-container.log"
sha256sum -c "$BK/plugin.sha256" >/dev/null
sha256sum -c "$BK/auth.sha256" >"$BK/auth-check.log" 2>&1
log 'IDENTITY_SWITCH_OFF_AND_ACCOUNT_CAP_VERIFIED plugin_and_auth_bytes_unchanged=true'
POST_HOLDS=$(lane_hold_state)
if [[ -n "$POST_HOLDS" ]] && [[ -n "$(comm -13 <(sort <<<"$PRE_HOLDS") <(sort <<<"$POST_HOLDS"))" ]]; then
  log 'UNVERIFIED new durable hold after restart; no automatic release'
  exit 10
fi
RESULT=0
CPA_HEALTH_NO_OAUTH=1 health generation || RESULT=$?
if [[ "$RESULT" == 10 ]]; then
  READY_RESULT=0
  health readiness || READY_RESULT=$?
  trap - ERR HUP INT TERM
  if [[ "$READY_RESULT" != 0 ]]; then
    log "DEFER post_readiness_failed result=$READY_RESULT; rolling back retained=$TARGET backup=$BK"
    rollback "$READY_RESULT"
  fi
  log "UNVERIFIED upstream unavailable; retained=$TARGET backup=$BK readiness=HEALTH_OK"
  exit 10
fi
[[ "$RESULT" == 0 ]]
trap - ERR HUP INT TERM
# A hold that appeared across OUR restart means a request straddled the
# container recreation despite the drain (or admission latched something
# else); the image itself is verified, so keep it but leave the resolution to
# the operator instead of reporting a clean success (2026-10-21 canary case).
if ! POST_HOLDS=$(lane_hold_state); then
  log "UNVERIFIED: admission durable state unreadable after update; retained=$TARGET backup=$BK"
  exit 10
fi
NEW_HOLDS=""
if [[ -n "$POST_HOLDS" ]]; then
  NEW_HOLDS=$(comm -13 <(sort <<<"$PRE_HOLDS") <(sort <<<"$POST_HOLDS") || true)
fi
if [[ -n "$NEW_HOLDS" ]]; then
  log "UNVERIFIED: lane hold appeared across the container restart lanes=${NEW_HOLDS//$'\n'/,} retained=$TARGET backup=$BK; recover per cpa-gateway.md manual release"
  exit 10
fi
log "OK: updated $CUR -> $TARGET digest=$DIGEST plugin=$PLUGIN_VERSION@$PLUGIN_SHA (unchanged)"
docker inspect --format \
  'CONTAINER started={{.State.StartedAt}} restarts={{.RestartCount}} status={{.State.Status}}' \
  cli-proxy-api

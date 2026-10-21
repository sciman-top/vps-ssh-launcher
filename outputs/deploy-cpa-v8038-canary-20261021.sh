#!/usr/bin/env bash
# Manual same-day canary: CPA v8.0.37 -> v8.0.38 on bwg, user-authorized 2026-10-21. No OAuth credential-path changes; v8.0.36 touches TTFT streaming measurement (#6794), antigravity Hub request alignment + versioned model-cache invalidation (#6798), executor downstream User-Agent passthrough scoped to xAI/openai-compat executors (codex/antigravity executors untouched; zhipu lane rides openai-compat), claude sdk-ts/cloak + web-search cache scoping (claude lane retired -> inert), SIWC auth hardening (unused), watcher auth-file error hardening; v8.0.38 adds per-stream metadata/tool-map caching perf PRs #6828/#6829/#6831, Claude cache_control empty-text-block skip (inert), Gemini usage-after-cancel fix (antigravity lane), Vertex routing (unused), plugin host auth.models callback (additive), read-only SIWC status endpoint.
# Mirrors scripts/remote/cpa-auto-update.sh --apply discipline with an explicit
# TARGET, because v8.0.38 is below the updater's 72h dual-source maturity gate.
# Deviations from the timer path (both deliberate, documented):
#   - no maintenance-status write: cpa-update.status belongs to the timer's
#     heartbeat contract; a manual DEFER must not turn the doctor red while
#     production is healthy (timer runbook convention for canary deploys).
#   - short bind-wait sleeps before post-start health probes so a normal
#     container start is not misread as LOCAL_CONTRACT failure and rolled back.
# Exit: 0 verified | 1 deferred/rolled back | 10 unverified (upstream
# unavailable, new version retained) | 20 local contract refusal | 75 lock busy.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LOG="$DIR/canary-v8038.log"
CUR=v8.0.37
CUR_DIGEST=sha256:6e36e6aee64a76fc8832b11e127072824dedce8c7eb0d295ee315bf4fdf31071
TARGET=v8.0.38
DIGEST=sha256:113a70240fb48f5c6fd674c9de40b1af4e7ff629f7acc6a412c6014ab1922daf
MIN_FREE_KIB=2097152

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
health() { python3 "$DIR/cpa-health.py" "$1"; }

grep -qF "image: eceasy/cli-proxy-api:$CUR@$CUR_DIGEST" "$DIR/compose.yml" || {
  log 'REFUSE compose pin drift'
  exit 20
}
python3 - <<'PY'
import json, urllib.request
h=json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz',timeout=5))
assert h['status']=='ok'
assert all(not v['state']['inflight'] and not v['state']['pending'] for v in h['lanes'].values()), 'REFUSE active traffic'
print('ADMISSION_IDLE_OK')
PY
log "PREFLIGHT pin=$CUR@$CUR_DIGEST"
docker inspect --format \
  'CONTAINER started={{.State.StartedAt}} restarts={{.RestartCount}} status={{.State.Status}}' \
  cli-proxy-api
AVAIL=$(df -Pk "$DIR" | awk 'NR == 2 {print $4}')
if (( AVAIL < MIN_FREE_KIB )); then
  log "DEFER insufficient_free_space free_kib=$AVAIL minimum_free_kib=$MIN_FREE_KIB"
  exit 1
fi
CPA_HEALTH_NO_OAUTH=1 health generation || { log 'DEFER pre_generation_failed'; exit 1; }
log 'PRE_GENERATION OK'

BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-from-$CUR-canary-$TARGET"
mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$BK/"
docker image inspect "eceasy/cli-proxy-api:$CUR@$CUR_DIGEST" >/dev/null || {
  log 'DEFER rollback_image_not_local'
  exit 1
}
log "BACKUP dir=$BK"

MUTATED=0
rollback() {
  local code=${1:-$?} rollback_failed=0
  trap - ERR INT TERM
  if [[ "$MUTATED" == 1 ]]; then
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || rollback_failed=1
    sleep 3
    if [[ "$rollback_failed" == 0 ]] &&
       (cd "$DIR" && docker compose up -d --pull never >>"$LOG" 2>&1) &&
       health readiness; then
      log "ROLLBACK restored=$CUR backup=$BK"
    else
      log "ROLLBACK_FAILED backup=$BK"
    fi
  fi
  [[ "$code" != 0 ]] || code=1
  exit "$code"
}
trap rollback ERR INT TERM

IMAGE="eceasy/cli-proxy-api:$TARGET@$DIGEST"
docker pull "$IMAGE" >>"$LOG" 2>&1
docker image inspect "$IMAGE" >/dev/null
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
(cd "$DIR" && docker compose config --quiet && docker compose up -d --pull never >>"$LOG" 2>&1)
log "RECREATED target=$TARGET"
sleep 3
RESULT=0
CPA_HEALTH_NO_OAUTH=1 health generation || RESULT=$?
if [[ "$RESULT" == 10 ]]; then
  READY_RESULT=0
  health readiness || READY_RESULT=$?
  trap - ERR INT TERM
  if [[ "$READY_RESULT" != 0 ]]; then
    log "DEFER post_readiness_failed result=$READY_RESULT; rolling back retained=$TARGET backup=$BK"
    rollback "$READY_RESULT"
  fi
  log "UNVERIFIED upstream unavailable; retained=$TARGET backup=$BK readiness=HEALTH_OK"
  exit 10
fi
[[ "$RESULT" == 0 ]]
trap - ERR INT TERM
log "OK: updated $CUR -> $TARGET digest=$DIGEST backup=$BK"
docker inspect --format \
  'CONTAINER started={{.State.StartedAt}} restarts={{.RestartCount}} status={{.State.Status}}' \
  cli-proxy-api




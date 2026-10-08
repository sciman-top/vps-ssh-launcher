#!/usr/bin/env bash
# BWG CPA manual canary transaction v8.0.16 -> v8.0.20 (2026-10-08).
# Replicates cpa-auto-update.sh apply semantics: shared flock, error-dump
# hygiene, pre/post generation gates, compose-only mutation, digest-pinned
# image, automatic rollback on any local-contract failure. No prune: the
# v8.0.16 image stays as the local rollback set.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LOG="$DIR/v8020-canary.log"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'CANARY_ALREADY_RUNNING'; exit 75; }

EXPECTED_CUR='eceasy/cli-proxy-api:v8.0.16@sha256:71c86af15412f16e5bca222dd656faaa0f01c9946cffa281e8ace85ed3f5f7b9'
TARGET='eceasy/cli-proxy-api:v8.0.20@sha256:bdd21270b6e2833bff4ee2b0dd51648a4cbc448569afd6bf028be5f3950dd5fe'
MIN_FREE_KIB=2097152

CUR=$(python3 - "$DIR/compose.yml" <<'PY'
import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))["services"]["cli-proxy-api"]["image"])
PY
)
[[ "$CUR" == "$EXPECTED_CUR" ]] || { log "REFUSE compose_current=$CUR"; exit 2; }
log "CANARY_START current=$CUR target=$TARGET"

if [[ -d "$DIR/auth/logs" ]]; then
  chmod 700 "$DIR/auth/logs"
  find "$DIR/auth/logs" -maxdepth 1 -type f -name 'error-*.log' -exec chmod 600 -- {} + 2>/dev/null || true
fi

if ! python3 "$DIR/cpa-health.py" generation; then
  log 'DEFER: pre-update generation failed; image unchanged'
  exit 1
fi

free_kib=$(df -Pk "$DIR" | awk 'NR == 2 {print $4}')
(( free_kib >= MIN_FREE_KIB )) || { log "DEFER: insufficient_free_space=$free_kib"; exit 1; }

BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-from-v8.0.16-canary-v8020"
[[ -e "$BK" ]] && { log "DEFER: backup path exists path=$BK"; exit 1; }
mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$BK/"
docker image inspect "$CUR" >/dev/null

MUTATED=0
rollback() {
  local code=${1:-$?} rb=0
  trap - ERR INT TERM
  if [[ "$MUTATED" == 1 ]]; then
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || rb=1
    if [[ "$rb" == 0 ]] &&
       (cd "$DIR" && docker compose config --quiet && docker compose up -d --pull never >>"$LOG" 2>&1) &&
       python3 "$DIR/cpa-health.py" readiness; then
      log "ROLLBACK restored=v8.0.16 backup=$BK"
    else
      log "ROLLBACK_FAILED backup=$BK"
    fi
  fi
  exit "$code"
}
trap rollback ERR INT TERM

docker pull "$TARGET" >>"$LOG" 2>&1
docker image inspect "$TARGET" >/dev/null
MUTATED=1

python3 - "$DIR/compose.yml" "$TARGET" <<'PY'
import os, re, sys
from pathlib import Path
p = Path(sys.argv[1])
text, n = re.subn(r'image:\s*eceasy/cli-proxy-api:[^\s]+', 'image: ' + sys.argv[2], p.read_text())
if n != 1:
    raise SystemExit('REFUSE image replacement count')
tmp = p.with_suffix('.yml.new')
tmp.write_text(text)
os.chmod(tmp, p.stat().st_mode & 0o777)
os.replace(tmp, p)
PY

(cd "$DIR" && docker compose config --quiet && docker compose up -d --pull never >>"$LOG" 2>&1)

RESULT=0
python3 "$DIR/cpa-health.py" generation || RESULT=$?
if [[ "$RESULT" == 10 ]]; then
  READY_RESULT=0
  python3 "$DIR/cpa-health.py" readiness || READY_RESULT=$?
  trap - ERR INT TERM
  if [[ "$READY_RESULT" == 10 ]]; then
    log "UNVERIFIED: upstream unavailable after update; retained=v8.0.20 backup=$BK readiness=UPSTREAM_UNAVAILABLE"
    exit 10
  fi
  if [[ "$READY_RESULT" != 0 ]]; then
    log "DEFER: post-update readiness failed result=$READY_RESULT; rolling back"
    rollback "$READY_RESULT"
  fi
  log "UNVERIFIED: upstream unavailable after update; retained=v8.0.20 backup=$BK readiness=HEALTH_OK"
  exit 10
fi
[[ "$RESULT" == 0 ]]
trap - ERR INT TERM
log "OK: updated v8.0.16 -> v8.0.20 digest=sha256:bdd21270b6e2833bff4ee2b0dd51648a4cbc448569afd6bf028be5f3950dd5fe backup=$BK"

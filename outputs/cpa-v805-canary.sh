#!/usr/bin/env bash
# Manual canary adoption of CPA v8.0.5 (user-authorized soak exemption 2026-10-01).
# Mirrors cpa-auto-update.sh apply semantics: pre health gate -> backup compose ->
# digest-pinned pull -> single image-line swap -> up -d --pull never -> generation
# gate; exit 1 rolls back, exit 10 keeps the new image only when readiness says
# the failure is upstream-side.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LOG=/tmp/cpa-v805-canary.log
exec >>"$LOG" 2>&1
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
CUR_IMAGE='eceasy/cli-proxy-api:v8.0.4@sha256:72205ea2dff7e3e3ef23b03de4e17b169ff7449c02b12f2924a3d4d3eee68b7d'
NEW_IMAGE='eceasy/cli-proxy-api:v8.0.5@sha256:a3ffe52bf85677ec465d8cd5d1be70ba31aeb8e3b658e0fb71f9dac9b77bc0d9'
health() { python3 "$DIR/cpa-health.py" "$1"; }
MUTATED=0
BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-canary-from-v8.0.4"
rollback() {
  local code=${1:-$?} rf=0
  trap - ERR INT TERM
  if [[ "$MUTATED" == 1 ]]; then
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || rf=1
    if [[ "$rf" == 0 ]] && (cd "$DIR" && docker compose up -d --pull never >>"$LOG" 2>&1) && health readiness; then
      log "ROLLBACK restored=v8.0.4 backup=$BK"
    else
      log "ROLLBACK_FAILED backup=$BK"
    fi
  fi
  exit "$code"
}
trap rollback ERR INT TERM
CUR_IN_COMPOSE=$(python3 -c "import yaml;print(yaml.safe_load(open('$DIR/compose.yml'))['services']['cli-proxy-api']['image'])")
[[ "$CUR_IN_COMPOSE" == "$CUR_IMAGE" ]] || { log "REFUSE unexpected current image: $CUR_IN_COMPOSE"; exit 1; }
health readiness || { log 'DEFER: pre readiness failed'; exit 1; }
health generation || { log 'DEFER: pre generation failed'; exit 1; }
mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$BK/"
docker image inspect "$CUR_IMAGE" >/dev/null
log "PULL $NEW_IMAGE"
docker pull "$NEW_IMAGE"
docker image inspect "$NEW_IMAGE" >/dev/null
MUTATED=1
python3 - "$DIR/compose.yml" "$NEW_IMAGE" <<'PY'
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
(cd "$DIR" && docker compose config --quiet && docker compose up -d --pull never)
log "SWAPPED image=$NEW_IMAGE"
RESULT=0
health generation || RESULT=$?
if [[ "$RESULT" == 10 ]]; then
  RR=0
  health readiness || RR=$?
  if [[ "$RR" == 10 ]]; then
    trap - ERR INT TERM
    log "UNVERIFIED: upstream unavailable after update; retained=v8.0.5 backup=$BK readiness=UPSTREAM_UNAVAILABLE"
    exit 10
  fi
  [[ "$RR" == 0 ]] || { log "READINESS_FAILED rr=$RR"; exit 1; }
  trap - ERR INT TERM
  log "UNVERIFIED: generation 503-class upstream-side; kept=v8.0.5 backup=$BK readiness=OK"
  exit 10
fi
[[ "$RESULT" == 0 ]] || { log "GENERATION_FAILED result=$RESULT"; exit 1; }
trap - ERR INT TERM
log "CANARY_OK image=$NEW_IMAGE backup=$BK"

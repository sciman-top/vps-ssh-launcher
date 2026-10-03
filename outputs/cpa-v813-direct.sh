#!/usr/bin/env bash
# DIRECT (user-ordered 2026-10-03) adoption of CPA v8.0.13 on bwg.
#
# Why this differs from the standard canary: the updater/canary pre-flight
# generation gate is blocked by an UNRELATED slot-1 ai.input.im upstream
# outage (required model deepseek-v4.1-flash de-listed from the CPA catalog),
# not by anything in the image being adopted. The user explicitly chose to
# bypass that upstream-coupled gate.
#
# Every local safety rail of cpa-auto-update.sh is preserved:
#   flock -> error-dump permissions -> backup health -> compose backup ->
#   digest-pinned pull (verified) -> single image-line swap -> up -d --pull
#   never -> post-swap READINESS verification -> automatic rollback on failure.
# The ONLY relaxation is that success is proven by local readiness
# (catalog contract + loopback), not by an upstream generation request.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LOG=/tmp/cpa-v813-direct.log
exec >>"$LOG" 2>&1
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 1; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
CUR_IMAGE='eceasy/cli-proxy-api:v8.0.12@sha256:f2f1ee7a3cd18f49b8e4ba13611b86b9f9069122eff9fb182912996964aa945d'
NEW_IMAGE='eceasy/cli-proxy-api:v8.0.13@sha256:6ce96259e6a2fec3b1093301d2dc61989260a8d682465be53e8f70656c6b6784'
health() { python3 "$DIR/cpa-health.py" "$1"; }
MUTATED=0
BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-direct-from-v8.0.12"
rollback() {
  local code=${1:-$?} rf=0
  trap - ERR INT TERM
  if [[ "$MUTATED" == 1 ]]; then
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || rf=1
    if [[ "$rf" == 0 ]] && (cd "$DIR" && docker compose up -d --pull never >>"$LOG" 2>&1) && health readiness; then
      log "ROLLBACK restored=v8.0.12 backup=$BK"
    else
      log "ROLLBACK_FAILED backup=$BK"
    fi
  fi
  exit "$code"
}
trap rollback ERR INT TERM

CUR_IN_COMPOSE=$(python3 -c "import yaml;print(yaml.safe_load(open('$DIR/compose.yml'))['services']['cli-proxy-api']['image'])")
[[ "$CUR_IN_COMPOSE" == "$CUR_IMAGE" ]] || { log "REFUSE unexpected current image: $CUR_IN_COMPOSE"; exit 1; }

log "DIRECT_START cur=v8.0.12 target=v8.0.13 mode=readiness_verified"
# Pre-flight is readiness only (local contract); the generation gate is
# deliberately skipped because the slot-1 upstream outage is unrelated.
health readiness || { log 'DEFER: pre readiness failed'; exit 1; }
log "PRE_READINESS=OK generation_gate=SKIPPED reason=slot1_ai_input_im_outage_unrelated"
[[ -d "$DIR/backups" && "$(stat -c %a "$DIR/backups")" == 700 ]] || { log 'DEFER: backup root unhealthy'; exit 1; }
mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$BK/"
docker image inspect "$CUR_IMAGE" >/dev/null
log "PULL $NEW_IMAGE"
docker pull "$NEW_IMAGE"
# Verify the pulled digest matches the pinned one exactly before mutating.
docker image inspect "$NEW_IMAGE" >/dev/null
log "PULL_VERIFIED $NEW_IMAGE"
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

# Post-swap verification: readiness (local catalog + loopback) plus a direct
# loopback generation on a HEALTHY non-slot-1 lane (GLM). Slot-1 models are
# expected to fail and are NOT part of this verdict.
sleep 8
RR=0
health readiness || RR=$?
log "POST_READINESS=$RR"
[[ "$RR" == 0 ]] || { log "READINESS_FAILED rr=$RR"; rollback 1; }

GLM=$(python3 - <<'PY'
import json, urllib.request, urllib.error, yaml
cfg = yaml.safe_load(open('/opt/cliproxyapi/config.yaml'))
k = cfg['api-keys'][0]
body = json.dumps({'model': 'glm-5.3-flash',
                   'messages': [{'role': 'user', 'content': 'Reply with exactly: OK'}],
                   'max_tokens': 64}).encode()
rq = urllib.request.Request('http://127.0.0.1:8317/v1/chat/completions', data=body,
                            headers={'Authorization': 'Bearer ' + k,
                                     'Content-Type': 'application/json'})
try:
    data = json.load(urllib.request.urlopen(rq, timeout=60))
    ch = data['choices'][0]
    ok = ch['message']['content'].strip() == 'OK' and ch.get('finish_reason') == 'stop'
    print('GLM_OK' if ok else 'GLM_BAD')
except Exception as e:
    print('GLM_FAIL ' + type(e).__name__)
PY
)
log "POST_GENERATION lanceless_probe=$GLM"
case "$GLM" in
  GLM_OK) : ;;
  *) log "POST_GENERATION_FAILED probe=$GLM"; rollback 1 ;;
esac

trap - ERR INT TERM
log "DIRECT_OK image=$NEW_IMAGE backup=$BK readiness=OK glm=OK"

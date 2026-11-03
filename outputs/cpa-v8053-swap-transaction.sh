#!/usr/bin/env bash
# v8.0.52 -> v8.0.53 manual joint host/plugin canary swap (2026-11-03).
# Replicates cpa-v8052-swap-transaction.sh discipline: maintenance lock, pin
# asserts, traffic-guard drain, atomic compose image swap, verified recreate,
# rollback trap. Backup compose lives in the window backup dir created
# earlier. No paired re-auth this window: v8.0.52..v8.0.53 has no login- or
# scope-coupled change (round-15 delta review).
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
NEW_IMAGE='eceasy/cli-proxy-api:v8.0.53@sha256:19eae277664c18d980701cf1a283b11440f82efbac17e910a9120fdd5ac0e792'
OLD_REF='eceasy/cli-proxy-api:v8.0.52@sha256:5d5bab318ac048c0f88570c6ffb852ef3e42e9acc9bfcf346c524de7a2468131'
BK=$(ls -d "$DIR"/backups/*-from-v8.0.52-to-v8.0.53-plugin-0.9.84-unchanged | head -1)
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

# --- pre asserts ---
grep -qF "image: $OLD_REF" "$DIR/compose.yml" || { log 'COMPOSE_PIN_MISMATCH'; exit 10; }
cmp -s "$BK/compose.yml" "$DIR/compose.yml" || { log 'BACKUP_COMPOSE_DIFFERS_FROM_LIVE'; exit 10; }
plugin_sha=$(sha256sum "$DIR/plugins/workbuddy.so" | cut -d' ' -f1)
case "$plugin_sha" in 2665af747f48aa3421f2*) : ;; *) log 'PLUGIN_SHA_MISMATCH'; exit 10 ;; esac
log "PLUGIN_SHA_OK ${plugin_sha}"
docker image inspect "$OLD_REF" >/dev/null || { log 'ROLLBACK_IMAGE_MISSING'; exit 10; }
free_kib=$(df -Pk "$DIR" | awk 'NR==2{print $4}')
[ "$free_kib" -ge 2097152 ] || { log "DISK_LOW free_kib=$free_kib"; exit 10; }
for f in chatgpt-risk.json workbuddy-risk.json antigravity-risk.json; do
  r=$(python3 -c "import json;print(json.load(open('/var/lib/cpa-admission/$f')).get('hold_reason'))")
  [ "$r" = "None" ] || { log "PRE_HOLD_PRESENT $f=$r"; exit 10; }
done
python3 - <<'PY' || exit 10
import json, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8318/healthz', timeout=5) as r:
    d = json.load(r)
assert d.get('traffic_guard') == '/run/cpa-admission/requests.lock', 'TRAFFIC_GUARD_UNAVAILABLE'
PY
log 'PRE_ASSERTS_OK'
sha256sum "$DIR"/auth/*.json > /tmp/auth-pre-swap.sha256

# --- rollback trap (armed after backup verified) ---
MUTATED=0
rollback() {
  local rc=$?
  trap '' ERR
  if [ "$MUTATED" = "1" ]; then
    log "ROLLBACK_BEGIN rc=$rc"
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || true
    (cd "$DIR" && docker compose up -d --pull never) || true
    sleep 2
    python3 "$DIR/cpa-health.py" readiness && log 'ROLLBACK_OK restored=v8.0.52' || log 'ROLLBACK_READINESS_FAIL'
  fi
  exit "$rc"
}
trap rollback ERR

# --- drain ---
exec 8</run/cpa-admission/requests.lock
flock -w 60 -x 8 || { log 'DRAIN_TIMEOUT'; exit 30; }
log 'DRAIN_OK'

# --- atomic swap ---
MUTATED=1
python3 - "$DIR/compose.yml" "$NEW_IMAGE" <<'PY'
import os, re, sys
from pathlib import Path
p = Path(sys.argv[1])
text, n = re.subn(r'image:\s*eceasy/cli-proxy-api:[^\s]+', 'image: ' + sys.argv[2], p.read_text())
if n != 1:
    raise SystemExit('REFUSE image replacement count=%d' % n)
tmp = p.with_suffix('.yml.new')
tmp.write_text(text)
os.chmod(tmp, p.stat().st_mode & 0o777)
os.replace(tmp, p)
PY
(cd "$DIR" && docker compose config --quiet)
(cd "$DIR" && docker compose up -d --pull never)

# --- verify ---
sleep 3
running=$(docker inspect --format '{{.Image}}' cli-proxy-api)
expected=$(docker image inspect --format '{{.ID}}' "$NEW_IMAGE")
[ "$running" = "$expected" ] || { log 'DIGEST_MISMATCH'; exit 10; }
log 'DIGEST_READBACK=OK'
docker logs --since 120s cli-proxy-api 2>&1 | grep -m1 'CLIProxyAPI Version' || true
docker logs cli-proxy-api 2>&1 | grep -m1 -oE 'plugin registered plugin_id=workbuddy plugin_name=workbuddy version=[0-9.]+' \
  || { log 'PLUGIN_NOT_REGISTERED'; exit 10; }
docker logs cli-proxy-api 2>&1 | grep -m2 -E 'account_max_concurrent=1 applied|daily_credits_cap=[0-9]+ applied' || true
set +e
python3 "$DIR/cpa-health.py" generation; gen_rc=$?
set -e
if [ "$gen_rc" -ne 0 ]; then
  log "HEALTH_GENERATION rc=$gen_rc (upstream-unavailable path)"
  python3 "$DIR/cpa-health.py" readiness || { log 'READINESS_FAIL'; exit "$gen_rc"; }
  log 'READINESS_OK upstream-unavailable retained (updater precedent)'
else
  log "HEALTH_GENERATION rc=0"
fi
sha256sum "$DIR"/auth/*.json > /tmp/auth-post-swap.sha256
diff -q /tmp/auth-pre-swap.sha256 /tmp/auth-post-swap.sha256 >/dev/null || { log 'AUTH_BYTES_CHANGED'; exit 10; }
log 'AUTH_BYTES_UNCHANGED'
for f in chatgpt-risk.json workbuddy-risk.json antigravity-risk.json; do
  r=$(python3 -c "import json;print(json.load(open('/var/lib/cpa-admission/$f')).get('hold_reason'))")
  [ "$r" = "None" ] || { log "LANE_HOLD_APPEARED $f=$r"; exit 10; }
done
log 'LANE_HOLD_DELTA=none'
log "restarts=$(docker inspect --format '{{.RestartCount}}' cli-proxy-api)"
log 'SWAP_OK v8.0.52->v8.0.53'

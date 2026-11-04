#!/usr/bin/env bash
# bwg CPA v8.0.53 -> v8.0.54 plus WorkBuddy 0.9.84 -> 0.9.85.
# Exact-field config projection, maintenance lock, request drain, backups,
# atomic image/plugin swap, post-registration assertions, and local rollback.
# No credentials are read, copied, printed, rotated, or deleted.
set -Eeuo pipefail
umask 077

DIR=/opt/cliproxyapi
OLD_IMAGE='eceasy/cli-proxy-api:v8.0.53@sha256:19eae277664c18d980701cf1a283b11440f82efbac17e910a9120fdd5ac0e792'
NEW_IMAGE='eceasy/cli-proxy-api:v8.0.54@sha256:66e51ccba4786099f85f40439ba17f975dc5e9468def96ba914d3da39041d03a'
OLD_PLUGIN_SHA=2665af747f48aa3421f2432556c97f59ad538ece966eb93d940655f21c22bc47
NEW_PLUGIN=/root/cpa-multi-plugins-0893-08dkiYqa/src/plugins/workbuddy/workbuddy.so
NEW_PLUGIN_SHA=e4de7853327eb7ec04bfc6da056a5a604b05246641d181239af8d4d7fa5eab42
PLUGIN="$DIR/plugins/workbuddy.so"
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK="$DIR/backups/$TS-from-v8.0.53-to-v8.0.54-plugin-0.9.84-to-0.9.85"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

grep -qF "image: $OLD_IMAGE" "$DIR/compose.yml" || {
  log 'COMPOSE_PIN_MISMATCH'
  exit 10
}
[ "$(sha256sum "$PLUGIN" | cut -d' ' -f1)" = "$OLD_PLUGIN_SHA" ] || {
  log 'LIVE_PLUGIN_SHA_MISMATCH'
  exit 10
}
[ -s "$NEW_PLUGIN" ]
[ "$(sha256sum "$NEW_PLUGIN" | cut -d' ' -f1)" = "$NEW_PLUGIN_SHA" ] || {
  log 'NEW_PLUGIN_SHA_MISMATCH'
  exit 10
}
docker image inspect "$OLD_IMAGE" >/dev/null || {
  log 'ROLLBACK_IMAGE_MISSING'
  exit 10
}
docker image inspect "$NEW_IMAGE" >/dev/null || {
  log 'TARGET_IMAGE_MISSING'
  exit 10
}
free_kib=$(df -Pk "$DIR" | awk 'NR==2{print $4}')
[ "$free_kib" -ge 2097152 ] || {
  log "DISK_LOW free_kib=$free_kib"
  exit 10
}

python3 - "$DIR/config.yaml" <<'PY'
import sys
import yaml
from pathlib import Path

path = Path(sys.argv[1])
config = yaml.safe_load(path.read_text(encoding="utf-8"))
section = config["plugins"]["configs"]["workbuddy"]
assert section.get("enabled") is True
assert section.get("scheduler_mode") == "credits"
assert section.get("account_max_concurrent") == 1
assert section.get("identity_switch_on_429") is False
PY

for f in chatgpt-risk.json workbuddy-risk.json antigravity-risk.json; do
  reason=$(python3 -c "import json;print(json.load(open('/var/lib/cpa-admission/$f')).get('hold_reason'))")
  [ "$reason" = "None" ] || {
    log "PRE_HOLD_PRESENT $f=$reason"
    exit 10
  }
done
python3 - <<'PY'
import json
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8318/healthz', timeout=5) as response:
    state = json.load(response)
assert state.get('traffic_guard') == '/run/cpa-admission/requests.lock'
PY
PRE_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
log 'PRE_ASSERTS_OK'

mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$DIR/config.yaml" "$PLUGIN" "$BK/"
sha256sum "$DIR"/auth/*.json >"$BK/credentials.sha256"
chmod 600 "$BK"/*
cmp -s "$BK/compose.yml" "$DIR/compose.yml" || {
  log 'BACKUP_COMPOSE_DIFFERS'
  exit 10
}
log "BACKUP dir=$BK"

MUTATED=0
rollback() {
  local rc=$?
  trap '' ERR HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    log "ROLLBACK_BEGIN rc=$rc"
    cp -a "$BK/config.yaml" "$DIR/config.yaml.rollback" &&
      mv -f "$DIR/config.yaml.rollback" "$DIR/config.yaml" || log 'ROLLBACK_CONFIG_FAILED'
    install -m 755 "$BK/workbuddy.so" "$PLUGIN.rollback" &&
      mv -f "$PLUGIN.rollback" "$PLUGIN" || log 'ROLLBACK_PLUGIN_FAILED'
    cp -a "$BK/compose.yml" "$DIR/compose.yml" ||
      log 'ROLLBACK_COMPOSE_FAILED'
    (cd "$DIR" && docker compose up -d --pull never) ||
      log 'ROLLBACK_RECREATE_FAILED'
    python3 "$DIR/cpa-health.py" readiness && log 'ROLLBACK_READINESS_OK' ||
      log 'ROLLBACK_READINESS_FAILED'
  fi
  exit "$rc"
}
trap rollback ERR HUP INT TERM

exec 8</run/cpa-admission/requests.lock
flock -w 60 -x 8 || {
  log 'DRAIN_TIMEOUT'
  exit 30
}
log 'DRAIN_OK'

MUTATED=1
python3 - "$DIR/config.yaml" <<'PY'
import os
import sys
import yaml
from pathlib import Path

path = Path(sys.argv[1])
config = yaml.safe_load(path.read_text(encoding="utf-8"))
section = config["plugins"]["configs"]["workbuddy"]
if section.get("enabled") is not True:
    raise SystemExit("REFUSE workbuddy disabled")
section["scheduler_priority_reclaim"] = True
candidate = yaml.safe_dump(
    config,
    allow_unicode=True,
    default_flow_style=False,
    sort_keys=False,
)
if yaml.safe_load(candidate) != config:
    raise SystemExit("candidate YAML semantic round-trip failed")
temporary = path.with_suffix(".yaml.new")
temporary.write_text(candidate, encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY

python3 - "$DIR/compose.yml" "$NEW_IMAGE" <<'PY'
import os
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text, count = re.subn(
    r'image:\s*eceasy/cli-proxy-api:[^\s]+',
    'image: ' + sys.argv[2],
    path.read_text(encoding="utf-8"),
)
if count != 1:
    raise SystemExit("REFUSE image replacement count=%d" % count)
temporary = path.with_suffix(".yml.new")
temporary.write_text(text, encoding="utf-8")
os.chmod(temporary, path.stat().st_mode & 0o777)
os.replace(temporary, path)
PY

install -m 755 "$NEW_PLUGIN" "$PLUGIN.new"
mv -f "$PLUGIN.new" "$PLUGIN"
[ "$(sha256sum "$PLUGIN" | cut -d' ' -f1)" = "$NEW_PLUGIN_SHA" ]
(cd "$DIR" && docker compose config --quiet)
(cd "$DIR" && docker compose up -d --pull never)
RESTART_TS=$(date -u +%FT%TZ)

running=$(docker inspect --format '{{.Image}}' cli-proxy-api)
expected=$(docker image inspect --format '{{.ID}}' "$NEW_IMAGE")
[ "$running" = "$expected" ] || {
  log 'DIGEST_MISMATCH'
  exit 10
}
log 'DIGEST_READBACK=OK'

NEW_STARTED="$PRE_STARTED"
for _ in 1 2 3 4 5 6 7 8; do
  NEW_STARTED=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api)
  [ "$NEW_STARTED" != "$PRE_STARTED" ] && break
  sleep 2
done
[ "$NEW_STARTED" != "$PRE_STARTED" ] || {
  log 'RESTART_NOT_APPLIED'
  exit 10
}
log "RESTART_APPLIED started=$NEW_STARTED"

registered=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
    grep -q 'plugin registered plugin_id=workbuddy .*version=0.9.85'; then
    registered=1
    break
  fi
  sleep 2
done
[ "$registered" = 1 ] || {
  log 'PLUGIN_REGISTRATION_MISSING'
  exit 10
}
log 'PLUGIN_REGISTERED version=0.9.85'
docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
  grep -m1 'scheduler_priority_reclaim=true applied'
docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
  grep -m1 'account_max_concurrent=1 applied'
docker logs --since "$RESTART_TS" cli-proxy-api 2>&1 |
  grep -m1 'CLIProxyAPI Version: v8.0.54'

python3 "$DIR/cpa-health.py" readiness || {
  log 'READINESS_FAILED'
  exit 10
}
log 'READINESS_OK'

sha256sum "$DIR"/auth/*.json >"$BK/credentials-post.sha256"
cmp -s "$BK/credentials.sha256" "$BK/credentials-post.sha256" || {
  log 'AUTH_BYTES_CHANGED'
  exit 10
}
log 'AUTH_BYTES_UNCHANGED'

for f in chatgpt-risk.json workbuddy-risk.json antigravity-risk.json; do
  reason=$(python3 -c "import json;print(json.load(open('/var/lib/cpa-admission/$f')).get('hold_reason'))")
  [ "$reason" = "None" ] || {
    log "LANE_HOLD_APPEARED $f=$reason"
    exit 10
  }
done
log 'LANE_HOLD_DELTA=none'
flock -u 8
log "restarts=$(docker inspect --format '{{.RestartCount}}' cli-proxy-api)"
log "SWAP_OK v8.0.53->v8.0.54 plugin-0.9.84->0.9.85 backup=$BK"

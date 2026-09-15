#!/usr/bin/env bash
# BWG's existing timer entrypoint: mature releases only, one smoke, rollback,
# bounded backup/image retention after a verified success.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LOG="$DIR/auto-update.log"
exec 9>"$DIR/auto-update.lock"
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 1; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
MODE=${1:---apply}
[[ "$MODE" == --check || "$MODE" == --apply ]] || exit 2
MIN_FREE_KIB=2097152
BACKUP_ROOT="$DIR/backups"

backup_health() {
  local backup_mode backup_count backup_size_kib available_kib
  if [[ -L "$BACKUP_ROOT" || ( -e "$BACKUP_ROOT" && ! -d "$BACKUP_ROOT" ) ]]; then
    log "BACKUP_HEALTH status=invalid_root"
    return 1
  fi
  if [[ ! -e "$BACKUP_ROOT" ]]; then
    if [[ "$MODE" == --apply ]]; then
      mkdir -m 700 -p "$BACKUP_ROOT" || {
        log "BACKUP_HEALTH status=mkdir_failed"
        return 1
      }
    else
      log "BACKUP_HEALTH status=not_initialized backups=0 size_kib=0"
      return 0
    fi
  fi
  if ! backup_mode=$(stat -c %a -- "$BACKUP_ROOT"); then
    log "BACKUP_HEALTH status=stat_failed"
    return 1
  fi
  if [[ "$backup_mode" != 700 ]]; then
    log "BACKUP_HEALTH status=unsafe_permissions mode=$backup_mode"
    return 1
  fi
  if ! available_kib=$(df -Pk "$DIR" | awk 'NR == 2 {print $4}') ||
     [[ ! "$available_kib" =~ ^[0-9]+$ ]]; then
    log "BACKUP_HEALTH status=disk_stat_failed"
    return 1
  fi
  if (( available_kib < MIN_FREE_KIB )); then
    log "BACKUP_HEALTH status=insufficient_free_space free_kib=$available_kib minimum_free_kib=$MIN_FREE_KIB"
    return 1
  fi
  if ! backup_count=$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf . | wc -c) ||
     ! backup_size_kib=$(du -sk -- "$BACKUP_ROOT" | awk 'NR == 1 {print $1}') ||
     [[ ! "$backup_count" =~ ^[0-9]+$ || ! "$backup_size_kib" =~ ^[0-9]+$ ]]; then
    log "BACKUP_HEALTH status=inventory_failed"
    return 1
  fi
  log "BACKUP_HEALTH status=ok backups=$backup_count size_kib=$backup_size_kib free_kib=$available_kib minimum_free_kib=$MIN_FREE_KIB"
}

# Select the highest semver present in both official releases and Docker Hub,
# aged at least 72h in both. A new release must not starve mature updates.
if ! SELECTION=$(python3 - "$DIR/compose.yml" <<'PY'
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'bwg-cpa-updater'})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)

def version(tag):
    return tuple(map(int, tag[1:].split('.')))

def mature(timestamp):
    when = dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    return (dt.datetime.now(dt.timezone.utc) - when).total_seconds() >= 259200

text = Path(sys.argv[1]).read_text()
matches = re.findall(r'image:\s*eceasy/cli-proxy-api:(v\d+\.\d+\.\d+)(?:@sha256:[0-9a-f]{64})?\s*(?:\n|$)', text)
if len(matches) != 1:
    raise SystemExit('REFUSE unexpected image declaration')
current = matches[0]
current_version = version(current)
releases = fetch('https://api.github.com/repos/router-for-me/CLIProxyAPI/releases?per_page=100')
eligible = {r['tag_name'] for r in releases if not r['draft'] and not r['prerelease']
            and re.fullmatch(r'v\d+\.\d+\.\d+', r['tag_name']) and mature(r['published_at'])}
tags = fetch('https://hub.docker.com/v2/repositories/eceasy/cli-proxy-api/tags?page_size=100')['results']
candidates = [t for t in tags if t['name'] in eligible and mature(t['last_updated'])
              # Automatic maintenance may cross minor releases within the
              # current major line. Major upgrades still require an explicit
              # review/canary and are intentionally excluded here.
              and version(t['name'])[0] == current_version[0]
              and version(t['name']) > current_version
              and re.fullmatch(r'sha256:[0-9a-f]{64}', t.get('digest', ''))]
if candidates:
    chosen = max(candidates, key=lambda t: version(t['name']))
    print(current, chosen['name'], chosen['digest'])
else:
    print(current, current, '-')
PY
); then
  log 'METADATA_FETCH_FAILED: release metadata unavailable; image unchanged'
  exit 1
fi
read -r CUR TARGET DIGEST <<<"$SELECTION"
log "CANDIDATE current=$CUR target=$TARGET soak=72h"
if [[ "$MODE" != --apply ]]; then
  if ! backup_health; then
    log 'DEFER: backup health unavailable; image unchanged'
    exit 1
  fi
  exit 0
fi
health() { python3 "$DIR/cpa-health.py" "$1"; }
RETENTION_KEEP_BACKUPS=8
CPA_IMAGE_REPO=eceasy/cli-proxy-api

compose_image_ref() {
  python3 - "$1" <<'PY'
import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))['services']['cli-proxy-api']['image'])
PY
}

prune_backups() {
  local total removed=0 entry
  total=$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '*-from-v[0-9]*' -printf . | wc -c) || {
    log 'PRUNE_FAILED scope=backups stage=inventory'
    return 0
  }
  while IFS= read -r entry; do
    if ! rm -rf -- "$entry"; then
      log "PRUNE_FAILED scope=backups path=$entry"
      return 0
    fi
    removed=$((removed + 1))
  done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '*-from-v[0-9]*' | sort | head -n "-$RETENTION_KEEP_BACKUPS")
  log "PRUNE scope=backups kept=$((total - removed)) removed=$removed policy=keep_$RETENTION_KEEP_BACKUPS"
}

prune_images() {
  # Digest-pinned pulls leave untagged repo images, so the rollback image is
  # protected by ID via the backup compose, and untagged refs are removed by ID.
  local running_id protected_id removed=0 freed=0 processed=0 size entry id target
  # docker inspect returns sha256:<full-id>, while docker images --format
  # '{{.ID}}' returns a 12-character short ID. Compare the same representation
  # or a successful update will try to remove its running image.
  short_image_id() {
    local image_id=${1#sha256:}
    printf '%s\n' "${image_id:0:12}"
  }
  running_id=$(short_image_id "$(docker inspect --format '{{.Image}}' cli-proxy-api 2>/dev/null || true)")
  protected_id=$(short_image_id "$(docker image inspect --format '{{.ID}}' "$(compose_image_ref "$BK/compose.yml")" 2>/dev/null || true)")
  while IFS= read -r entry; do
    processed=$((processed + 1))
    id=${entry%% *}
    target=${entry#* }
    if [[ "$target" == *':<none>' ]]; then
      target=$id
    fi
    if [[ "$id" == "$running_id" || "$id" == "$protected_id" ]]; then
      continue
    fi
    size=$(docker image inspect --format '{{.Size}}' "$id" 2>/dev/null || echo 0)
    if docker rmi "$target" >>"$LOG" 2>&1; then
      removed=$((removed + 1))
      freed=$((freed + size))
    else
      log "PRUNE_FAILED scope=images ref=$target"
      return 0
    fi
  done < <(docker images --format '{{.ID}} {{.Repository}}:{{.Tag}}' | awk -v repo="$CPA_IMAGE_REPO:" '$2 ~ "^"repo {print}')
  log "PRUNE scope=images kept=$((processed - removed)) removed=$removed freed_bytes=$freed policy=current_plus_previous"
}
if [[ "$CUR" == "$TARGET" ]]; then
  health generation
  log "OK: no newer mature release; current=$CUR health verified"
  exit 0
fi
if ! health generation; then
  log 'DEFER: pre-update health unavailable; image unchanged'
  exit 1
fi
if ! backup_health; then
  log 'DEFER: backup health unavailable; image unchanged'
  exit 1
fi

BK="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-from-$CUR"
mkdir -m 700 -p "$BK"
cp -a "$DIR/config.yaml" "$DIR/compose.yml" "$DIR/cpa-health.py" "$BK/"
mkdir -m 700 "$BK/auth"
find "$DIR/auth" -maxdepth 1 -type f \( -name '*.json' -o -name '*.cds' \) -exec cp -a -t "$BK/auth" {} +
# The rollback image pinned by the backup compose must stay locally available;
# retention pruning runs only after a verified success below.
docker image inspect "$(compose_image_ref "$DIR/compose.yml")" >/dev/null
MUTATED=0
rollback() {
  local code=$?
  trap - ERR INT TERM
  if [[ "$MUTATED" == 1 ]]; then
    cp -a "$BK/compose.yml" "$DIR/compose.yml"
    if (cd "$DIR" && docker compose up -d --pull never >>"$LOG" 2>&1) && health readiness; then
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
RESULT=0
health generation || RESULT=$?
if [[ "$RESULT" == 10 ]]; then
  log 'WAIT: upstream unavailable; one recheck after 65s'
  sleep 65
  RESULT=0
  health generation || RESULT=$?
fi
if [[ "$RESULT" == 10 ]]; then
  # Keep a locally healthy image; report uncertainty instead of restart churn.
  health readiness
  trap - ERR INT TERM
  log "UNVERIFIED: upstream unavailable after update; retained=$TARGET backup=$BK"
  exit 10
fi
[[ "$RESULT" == 0 ]]
trap - ERR INT TERM
log "OK: updated $CUR -> $TARGET digest=$DIGEST backup=$BK"
# UNVERIFIED and rollback paths never reach these; failure here only logs and
# retries on the next update, never fails the completed update itself.
prune_backups
prune_images

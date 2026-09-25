#!/usr/bin/env bash
# BWG's existing timer entrypoint: mature releases only, one smoke, image
# rollback, and bounded backup/image retention after a verified success.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LOG="$DIR/auto-update.log"
exec 9>/run/vps-ssh-launcher-maintenance.lock
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

# Select the highest patch release present in both official releases and Docker
# Hub, aged at least 72h in both. Minor and major releases are visible but
# require an explicit canary because their behavior can change materially.
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
              and version(t['name'])[:2] == current_version[:2]
              and version(t['name']) > current_version
              and re.fullmatch(r'sha256:[0-9a-f]{64}', t.get('digest', ''))]
minor_candidates = [t for t in tags if t['name'] in eligible and mature(t['last_updated'])
                    and version(t['name'])[0] == current_version[0]
                    and version(t['name'])[1] > current_version[1]
                    and version(t['name']) > current_version
                    and re.fullmatch(r'sha256:[0-9a-f]{64}', t.get('digest', ''))]
# Minor and major candidates are reported for visibility only; the updater
# never crosses either boundary on its own.
major_candidates = [t for t in tags if t['name'] in eligible and mature(t['last_updated'])
                    and version(t['name'])[0] > current_version[0]
                    and re.fullmatch(r'sha256:[0-9a-f]{64}', t.get('digest', ''))]
if candidates:
    chosen = max(candidates, key=lambda t: version(t['name']))
    print(current, chosen['name'], chosen['digest'])
else:
    print(current, current, '-')
minor_picks = sorted(minor_candidates, key=lambda t: version(t['name']))
if minor_picks:
    print('MINOR_CANDIDATE available=' + minor_picks[-1]['name'])
major_picks = sorted(major_candidates, key=lambda t: version(t['name']))
if major_picks:
    print('MAJOR_CANDIDATE available=' + major_picks[-1]['name'])
PY
); then
  log 'METADATA_FETCH_FAILED: release metadata unavailable; image unchanged'
  exit 1
fi
read -r CUR TARGET DIGEST <<<"$SELECTION"
log "CANDIDATE current=$CUR target=$TARGET soak=72h"
while IFS= read -r major_line; do
  log "$major_line"
done < <(printf '%s\n' "$SELECTION" | grep -E '^(MINOR|MAJOR)_CANDIDATE ' || true)
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

secure_error_dumps() {
  # CPA writes failed-request bodies into auth/logs. Keep the directory and
  # every retained dump private even if the container recreates a file with a
  # permissive umask; the doctor enforces the same invariant.
  if [[ ! -d "$DIR/auth/logs" ]]; then
    return 0
  fi
  if ! chmod 700 -- "$DIR/auth/logs"; then
    log 'SECURITY_BLOCK scope=error_dumps action=chmod_directory_failed'
    return 1
  fi
  while IFS= read -r entry; do
    if ! chmod 600 -- "$entry"; then
      log "SECURITY_BLOCK scope=error_dumps action=chmod_file_failed path=$entry"
      return 1
    fi
  done < <(find "$DIR/auth/logs" -maxdepth 1 -type f -name 'error-*.log' -print)
}

prune_error_dumps() {
  # Error-request dumps are age-bounded hygiene: sweep them independently of
  # whether an image update happened, but never delete a recent dump. 24h
  # bounds the plaintext request bodies' at-rest window (2026-09-21 review)
  # while keeping a short incident-debugging window; CPA additionally keeps only
  # the newest error-logs-max-files dumps on its own.
  local removed=0 entry
  while IFS= read -r entry; do
    if rm -f -- "$entry"; then
      removed=$((removed + 1))
    else
      log "PRUNE_FAILED scope=error_dumps path=$entry"
      return 0
    fi
  done < <(find "$DIR/auth/logs" -maxdepth 1 -type f -name 'error-*.log' -mmin +1440 2>/dev/null)
  if ((removed > 0)); then
    log "PRUNE scope=error_dumps removed=$removed policy=mtime_24h"
  fi
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
if ! secure_error_dumps; then
  log 'DEFER: error-dump permissions unavailable; provider probe/update blocked'
  exit 1
fi
prune_error_dumps
if [[ "$CUR" == "$TARGET" ]]; then
  # No candidate: consume nothing on the subscription OAuth account. A daily
  # fixed-window machine generation is avoidable account exposure
  # (2026-09-21 review); local readiness plus retained-log refresh signals
  # cover the timer's assurance without sending upstream traffic.
  if ! health readiness; then
    log "DEFER: no-update readiness failed; image unchanged"
    exit 1
  fi
  REFRESH_SIGNALS=$(docker logs --since 24h cli-proxy-api 2>&1 | grep -cE 'credential refresh failed|invalid_grant' || true)
  log "REFRESH_SIGNALS_24H=$REFRESH_SIGNALS non_consuming=true"
  log "OK: no newer mature release; current=$CUR readiness verified"
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

BK="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%S.%NZ)-from-$CUR"
if ! mkdir -m 700 "$BK"; then
  log "DEFER: backup path already exists or cannot be created path=$BK"
  exit 1
fi
cp -a "$DIR/compose.yml" "$BK/"
# This updater changes only the image declaration in compose.yml. OAuth
# refreshes can rotate auth JSON while the candidate runs, so an automatic
# rollback must never delete or replay the live credential directory.
# The rollback image pinned by the backup compose must stay locally available;
# retention pruning runs only after a verified success below.
docker image inspect "$(compose_image_ref "$DIR/compose.yml")" >/dev/null
MUTATED=0
rollback() {
  local code=${1:-$?} rollback_failed=0
  trap - ERR INT TERM
  if [[ "$MUTATED" == 1 ]]; then
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || rollback_failed=1
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
RESULT=0
health generation || RESULT=$?
if [[ "$RESULT" == 10 ]]; then
  # A provider-side stop signal (including 403/408/429/5xx) confirms that the
  # route decision is upstream. Confirm local readiness but
  # do not send another generation request from the updater.
  READY_RESULT=0
  health readiness || READY_RESULT=$?
  trap - ERR INT TERM
  if [[ "$READY_RESULT" == 10 ]]; then
    log "UNVERIFIED: upstream unavailable after update; retained=$TARGET backup=$BK readiness=UPSTREAM_UNAVAILABLE"
    exit 10
  fi
  if [[ "$READY_RESULT" != 0 ]]; then
    log "DEFER: post-update readiness failed result=$READY_RESULT; rolling back retained=$TARGET backup=$BK"
    rollback "$READY_RESULT"
  fi
  log "UNVERIFIED: upstream unavailable after update; retained=$TARGET backup=$BK readiness=HEALTH_OK"
  exit 10
fi
[[ "$RESULT" == 0 ]]
trap - ERR INT TERM
log "OK: updated $CUR -> $TARGET digest=$DIGEST backup=$BK"
# UNVERIFIED and rollback paths never reach these; failure here only logs and
# retries on the next update, never fails the completed update itself.
prune_backups
prune_images
secure_error_dumps
prune_error_dumps

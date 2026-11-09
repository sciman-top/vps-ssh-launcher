#!/usr/bin/env bash
# BWG's existing timer entrypoint: mature releases only, one smoke, image
# rollback, and bounded backup/image retention after a verified success.
#
# Exit-code contract, read by the guardrail doctor's ==timer-result== section:
#   0   no change, or a verified update
#   1   generic failure (including a failed update that rolled back)
#   2   bad mode argument
#   10  upstream unavailable after update, or a lane hold appeared across the
#       recreated container that was not present before it (UNVERIFIED; image
#       retained)
#   75  another maintenance transaction holds the shared lock (EX_TEMPFAIL,
#       same convention as the other wrappers; previously 1, which made a
#       busy skip indistinguishable from a real failure in ExecMainStatus)
#   30  pre-update provider probe failed before any backup or image change
#   76  error-dump hygiene refused before any remote write
#   77  enabled native plugins require a reviewed joint host/plugin canary
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LOG="$DIR/auto-update.log"
STATUS_DIR=/var/lib/vps-ssh-launcher/maintenance-status
STATUS_FILE="$STATUS_DIR/cpa-update.status"
PENDING_FILE="$STATUS_DIR/cpa-update.pending"
STATUS_STARTED_AT="$(date -u +%FT%TZ)"

write_status() {
  local result="$1" code="$2" finished tmp reason="${3:-}"
  finished="$(date -u +%FT%TZ)"
  mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
  tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
  [[ -n "$tmp" ]] || return 0
  {
    printf 'name=cpa-update\n'
    printf 'started_at=%s\n' "$STATUS_STARTED_AT"
    printf 'finished_at=%s\n' "$finished"
    printf 'result=%s\n' "$result"
    printf 'exit_code=%s\n' "$code"
    if [ -n "$reason" ]; then
      printf 'reason=%s\n' "$reason"
    fi
  } > "$tmp" 2>/dev/null || true
  chmod 600 "$tmp" 2>/dev/null || true
  mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

status_on_exit() {
  local code="$?" result=failed
  if (( code == 0 )); then result=success; fi
  if (( code == 10 )); then result=unverified; fi
  if (( code == 75 )); then result=busy; fi
  if (( code == 76 )); then result=deferred; fi
  if (( code == 77 )); then result=deferred; fi
  if (( code == 30 )); then
    write_status deferred "$code" preflight_or_drain_failed
    exit "$code"
  fi
  write_status "$result" "$code"
  exit "$code"
}
trap status_on_exit EXIT
write_status running 0

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
# Bound every Docker client call, including rollback. A stalled pull must not
# monopolise the shared maintenance lock or block certificate renewal forever.
docker() {
  local budget=30
  if [[ "$1" == pull ]]; then budget=600; fi
  if [[ "$1" == compose ]]; then budget=180; fi
  timeout --signal=TERM --kill-after=15s "${budget}s" docker "$@"
}

require_plugin_canary() {
  python3 - "$DIR/config.yaml" <<'PY'
import sys
from pathlib import Path
import yaml

try:
    config = yaml.safe_load(Path(sys.argv[1]).read_text())
    plugins = config.get('plugins', {})
    if not isinstance(plugins, dict):
        raise ValueError('invalid plugins section')
    if plugins.get('enabled', False) is not False:
        # A host patch release does not establish Go plugin ABI compatibility.
        # Do not download/replace custom .so builds or exercise their accounts.
        print('PLUGIN_CANARY_REQUIRED enabled_native_plugins=true')
        raise SystemExit(77)
except (OSError, ValueError, AttributeError, yaml.YAMLError):
    print('PLUGIN_CANARY_REQUIRED plugin_configuration_unverified=true')
    raise SystemExit(77)
PY
}

require_runtime_binary_canary() {
  # A compose image update cannot change a binary that is bind-mounted over
  # the image entrypoint, and once the fork-image derivation carries the
  # binary (docs/runbooks/cpa-fork-image-derivation-rfc.md) an automatic
  # switch back to the official image would silently drop the fork patch.
  # The bwg fork is intentional while the Gemini raw-reasoning patch remains
  # outside upstream, so both deployment shapes refuse the automatic
  # image-only path: a future mature release can never report success while
  # the old fork keeps serving requests.  Adopting a new upstream release is
  # a rebuild transaction (rebuild image, update the ledger, update compose),
  # never this script.
  python3 - "$DIR/compose.yml" <<'PY'
import sys
from pathlib import Path

try:
    import yaml
    compose = yaml.safe_load(Path(sys.argv[1]).read_text())
    service = compose.get("services", {}).get("cli-proxy-api", {})
    volumes = service.get("volumes", [])
    for volume in volumes if isinstance(volumes, list) else []:
        target = ""
        if isinstance(volume, str):
            parts = volume.split(":", 2)
            target = parts[1] if len(parts) >= 2 else ""
        elif isinstance(volume, dict):
            target = str(volume.get("target", ""))
        if target.rstrip("/") == "/CLIProxyAPI/CLIProxyAPI":
            print("RUNTIME_BINARY_CANARY_REQUIRED bind_mount=/CLIProxyAPI/CLIProxyAPI")
            raise SystemExit(77)
    image = str(service.get("image") or "")
    if image and not image.startswith("eceasy/cli-proxy-api"):
        print("FORK_IMAGE_REBUILD_REQUIRED image=%s" % image)
        raise SystemExit(77)
except SystemExit:
    raise
except Exception:
    print("RUNTIME_BINARY_CANARY_REQUIRED compose_unreadable=true")
    raise SystemExit(77)
PY
}

# The acceptance fixture runs in a mount namespace where /run is shared with
# the host, so both targets must be overridable: the fixture points the guard
# at its own admission stub and a fixture-local lock. Unset values keep the
# production literals, and the doctor asserts those literals independently.
export TRAFFIC_GUARD_URL="${TRAFFIC_GUARD_URL:-http://127.0.0.1:8318/healthz}"
export TRAFFIC_GUARD_LOCK="${TRAFFIC_GUARD_LOCK:-/run/cpa-admission/requests.lock}"
verify_traffic_guard() {
  python3 - <<'PY'
import json
import os
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(os.environ['TRAFFIC_GUARD_URL'], timeout=5) as response:
    data = json.load(response)
if data.get('traffic_guard') != os.environ['TRAFFIC_GUARD_LOCK']:
    raise SystemExit('TRAFFIC_GUARD_UNAVAILABLE')
PY
}
# The post-restart lane-hold delta check reads admission's durable state files.
# Fixture-overridable for the same reason as TRAFFIC_GUARD_URL; the doctor
# asserts the production literal independently. The claude tombstone
# (authentication_required) is deliberately absent from this list: it is an
# unconfigured-lane historical marker, inert by design (doctor ==lane-hold==).
export LANE_HOLD_STATE_DIR="${LANE_HOLD_STATE_DIR:-/var/lib/cpa-admission}"
lane_hold_state() {
  python3 - <<'PY'
import json, os, sys
from pathlib import Path

base = Path(os.environ['LANE_HOLD_STATE_DIR'])
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
MODE=${1:---apply}
[[ "$MODE" == --check || "$MODE" == --apply || "$MODE" == --confirm-pending ]] || exit 2
if [[ "$MODE" == --apply && -f "$PENDING_FILE" ]]; then
  pending_version=$(awk -F= '$1 == "version" {print $2; exit}' "$PENDING_FILE")
  log "UNVERIFIED: pending CPA acceptance version=$pending_version requires explicit --confirm-pending"
  exit 10
fi
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

health() { python3 "$DIR/cpa-health.py" "$1"; }

compose_cpa_version() {
  python3 - "$DIR/compose.yml" <<'PY'
import re
import sys
from pathlib import Path

# The fork-image era (docs/runbooks/cpa-fork-image-derivation-rfc.md) serves
# requests from cli-proxy-api-fork:v<upstream>-gemini-raw instead of the
# official tag, so the version reader must understand both declarations or
# every daily run would misreport a plain parse failure as a metadata outage.
# A fork tag that drifts from the v<upstream>-gemini-raw convention still
# refuses here: the rebuild transaction that changes the convention must
# update this parser, the canary and the ledger together.
text = Path(sys.argv[1]).read_text()
matches = re.findall(
    r'image:\s*(?:eceasy/cli-proxy-api|cli-proxy-api-fork):(v\d+\.\d+\.\d+)(?:-gemini-raw)?(?:@sha256:[0-9a-f]{64})?\s*(?:\n|$)',
    text,
)
if len(matches) != 1:
    raise SystemExit('REFUSE unexpected image declaration')
print(matches[0])
PY
}

# Select the highest patch release present in both official releases and Docker
# Hub, aged at least 72h in both. Minor and major releases are visible but
# require an explicit canary because their behavior can change materially.
if [[ "$MODE" == --confirm-pending ]]; then
  if ! CUR=$(compose_cpa_version); then
    log 'UNVERIFIED: cannot read the configured CPA image version'
    exit 10
  fi
else
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
matches = re.findall(r'image:\s*(?:eceasy/cli-proxy-api|cli-proxy-api-fork):(v\d+\.\d+\.\d+)(?:-gemini-raw)?(?:@sha256:[0-9a-f]{64})?\s*(?:\n|$)', text)
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
# Same-line visibility for releases that exist but are still inside the soak
# window. Without it a freshly published update looks exactly like "nothing to
# do" (2026-11-08/09: v8.0.60 and then v8.0.61 each sat unseen until a human
# compared the GitHub release page by hand, and a user asking "why is it not
# updated" has no recorded answer). These lines are observability only: the
# target selection above is unchanged and nothing here can cross a boundary or
# bypass the soak. Announced alongside (not instead of) MINOR/MAJOR_CANDIDATE,
# which by definition always accompany a still-soaking newer patch.
if not candidates:
    all_tags = {t['name'] for t in tags if re.fullmatch(r'v\d+\.\d+\.\d+', t['name'])
                and re.fullmatch(r'sha256:[0-9a-f]{64}', t.get('digest', ''))}
    published = {r['tag_name']: r['published_at'] for r in releases
                 if not r['draft'] and not r['prerelease']
                 and re.fullmatch(r'v\d+\.\d+\.\d+', r['tag_name'])}
    pending = [version(t) for t in all_tags & published.keys()
               if version(t) >= current_version and t != current
               and not mature(published[t])]
    if pending:
        newest = max(pending)
        tag = 'v' + '.'.join(str(part) for part in newest)
        released = dt.datetime.fromisoformat(published[tag].replace('Z', '+00:00'))
        remaining = released + dt.timedelta(seconds=259200) - dt.datetime.now(dt.timezone.utc)
        print('SOAK_PENDING newest=%s remaining_hours=%.1f' % (tag, max(0.0, remaining.total_seconds() / 3600)))
PY
); then
  log 'METADATA_FETCH_FAILED: release metadata unavailable; image unchanged'
  exit 1
fi
read -r CUR TARGET DIGEST <<<"$SELECTION"
log "CANDIDATE current=$CUR target=$TARGET soak=72h"
while IFS= read -r major_line; do
  log "$major_line"
done < <(printf '%s\n' "$SELECTION" | grep -E '^(MINOR|MAJOR)_CANDIDATE |^SOAK_PENDING ' || true)
fi
if [[ "$MODE" == --check ]]; then
  if ! backup_health; then
    log 'DEFER: backup health unavailable; image unchanged'
    exit 1
  fi
  exit 0
fi
if [[ "$MODE" == --confirm-pending ]]; then
  if [[ ! -f "$PENDING_FILE" ]]; then
    log 'REFUSE: no CPA version is awaiting verification'
    exit 1
  fi
  PENDING_VERSION=$(awk -F= '$1 == "version" {print $2; exit}' "$PENDING_FILE")
  PENDING_DIGEST=$(awk -F= '$1 == "digest" {print $2; exit}' "$PENDING_FILE")
  if [[ ! "$PENDING_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
     [[ ! "$PENDING_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    log 'UNVERIFIED: pending CPA acceptance record is malformed'
    exit 10
  fi
  if [[ "$PENDING_VERSION" != "$CUR" ]]; then
    log "UNVERIFIED: pending version=$PENDING_VERSION does not match compose version=$CUR"
    exit 10
  fi
  if ! grep -q '^hold_lanes=' "$PENDING_FILE"; then
    log "UNVERIFIED: pending record predates lane-state receipts; review and recreate it version=$CUR"
    exit 10
  fi
  PENDING_HOLD_LANES=$(awk -F= '$1 == "hold_lanes" {print $2; exit}' "$PENDING_FILE")
  if [[ -z "$PENDING_HOLD_LANES" ]]; then
    log "UNVERIFIED: pending lane hold record is malformed version=$CUR"
    exit 10
  fi
  RUNNING_IMAGE_ID=$(docker inspect --format '{{.Image}}' cli-proxy-api 2>/dev/null || true)
  if [[ -z "$RUNNING_IMAGE_ID" ]] ||
     ! docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$RUNNING_IMAGE_ID" 2>/dev/null | grep -Fxq "eceasy/cli-proxy-api@$PENDING_DIGEST"; then
    log "UNVERIFIED: running CPA image does not match pending digest version=$CUR"
    exit 10
  fi
  if [[ -n "$PENDING_HOLD_LANES" ]]; then
    if ! CURRENT_HOLDS=$(lane_hold_state); then
      log "UNVERIFIED: pending lane hold state is unreadable version=$CUR"
      exit 10
    fi
    if [[ "$PENDING_HOLD_LANES" != "unknown" ]]; then
      IFS=',' read -r -a pending_lanes <<< "$PENDING_HOLD_LANES"
      held_lanes=()
      for pending_lane in "${pending_lanes[@]}"; do
        case "$pending_lane" in
          chatgpt-risk.json|workbuddy-risk.json|antigravity-risk.json) : ;;
          *)
            log "UNVERIFIED: pending lane hold record is malformed version=$CUR"
            exit 10
            ;;
        esac
        while IFS= read -r current_hold; do
          if [[ "$current_hold" == "$pending_lane="* ]]; then
            held_lanes+=("$pending_lane")
            break
          fi
        done <<< "$CURRENT_HOLDS"
      done
      if (( ${#held_lanes[@]} > 0 )); then
        remaining=$(IFS=,; printf '%s' "${held_lanes[*]}")
        log "UNVERIFIED: pending lane holds remain lanes=$remaining version=$CUR"
        exit 10
      fi
    elif [[ -n "$CURRENT_HOLDS" ]]; then
      log "UNVERIFIED: lane holds remain after an unreadable state receipt version=$CUR"
      exit 10
    fi
  fi
  RESULT=0
  health generation || RESULT=$?
  if [[ "$RESULT" != 0 ]]; then
    log "UNVERIFIED: pending version=$CUR generation_result=$RESULT"
    exit "$RESULT"
  fi
  rm -f -- "$PENDING_FILE"
  log "PENDING_VERIFIED version=$CUR digest=$PENDING_DIGEST"
  exit 0
fi
if [[ "$MODE" != --apply ]]; then
  log "REFUSE unsupported mode=$MODE"
  exit 2
fi
RETENTION_KEEP_BACKUPS=8
CPA_IMAGE_REPO=eceasy/cli-proxy-api
# Families created by manual transactions. The updater's own backups
# ('*-from-v[0-9]*') keep their existing path below; everything else was
# previously unbounded (measured 2026-11-09: 117 dirs, ~800 MiB, on a disk at
# 77%). Each pattern is explicit rather than derived, so a typo cannot silently
# widen a delete.
RETENTION_BACKUP_PATTERNS=(
  'admission-*'
  'plugin-*'
  'converge-*'
  'guardrail-*'
  'workbuddy-*'
  'fork-image-*'
)
# Nothing younger than this is ever pruned, so a debugging window always
# outlives the shift that created it.
RETENTION_MIN_AGE_HOURS=168

compose_image_ref() {
  python3 - "$1" <<'PY'
import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))['services']['cli-proxy-api']['image'])
PY
}

# Rollback sets must name the image they actually captured. A backup compose
# carries "cli-proxy-api-fork:v8.0.61-gemini-raw" -- a MUTABLE tag. Resolving it
# later yields whatever the tag points at then, not the id that was running
# when the backup was taken (verified 2026-11-09: two retained backups resolve
# to the current image while the pre-switch image 8c6ab375 is gone). That makes
# prune_images() protect the wrong image and makes a "restore this backup"
# instruction silently restore different code.
#
# image_id_file() is the single place that answers "which image id did this
# backup capture". It prefers the recorded id and falls back to resolving the
# compose ref only for backups that predate the record.
image_id_file() {
  printf '%s/image-id\n' "$1"
}

record_image_ids() {
  # Called right after the backup directory is populated: stamp the concrete
  # image id that the live compose resolved to at that moment.
  local backup_dir="$1" image_ref image_id
  image_ref=$(compose_image_ref "$backup_dir/compose.yml") || return 1
  image_id=$(docker image inspect --format '{{.Id}}' "$image_ref" 2>/dev/null) || return 1
  printf '%s\n' "$image_id" > "$(image_id_file "$backup_dir")"
  chmod 600 "$(image_id_file "$backup_dir")"
  printf '%s\n' "$image_id"
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

prune_manual_backup_families() {
  # Bound the families created by manual transactions. They were never pruned:
  # the updater only ever matched '*-from-v[0-9]*'. Measured 2026-11-09:
  # 184 backup dirs / 1247 MiB, of which 117 dirs (~800 MiB) could not expire.
  #
  # Per family: keep the newest RETENTION_KEEP_BACKUPS dirs, and never delete
  # one younger than RETENTION_MIN_AGE_HOURS even when over budget. Patterns
  # are listed explicitly rather than derived, so a typo cannot widen a delete.
  local pattern total removed=0 entry kept_here
  for pattern in "${RETENTION_BACKUP_PATTERNS[@]}"; do
    total=$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name "$pattern" -printf . | wc -c)
    [ "$total" -gt "$RETENTION_KEEP_BACKUPS" ] || continue
    removed=0
    while IFS= read -r entry; do
      [ -n "$entry" ] || continue
      if ! rm -rf -- "$entry"; then
        log "PRUNE_FAILED scope=backup_family pattern=$pattern path=$entry"
        continue
      fi
      removed=$((removed + 1))
    done < <(
      find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name "$pattern" -printf '%T@ %p\n' |
        sort -rn |
        awk -v keep="$RETENTION_KEEP_BACKUPS" 'NR > keep { sub(/^[^ ]+ /, ""); print }' |
        while IFS= read -r path; do
          age_h=$(( ( $(date +%s) - $(stat -c %Y "$path") ) / 3600 ))
          [ "$age_h" -lt "$RETENTION_MIN_AGE_HOURS" ] && continue
          printf '%s\n' "$path"
        done
    )
    kept_here=$((total - removed))
    log "PRUNE scope=backup_family pattern=$pattern kept=$kept_here removed=$removed min_age_h=$RETENTION_MIN_AGE_HOURS"
  done
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

write_pending_verification() {
  local tmp hold_lanes="${1:?pending lane hold receipt is required}"
  mkdir -m 700 -p "$STATUS_DIR"
  tmp=$(mktemp "$PENDING_FILE.XXXXXX")
  {
    printf 'version=%s\n' "$TARGET"
    printf 'digest=%s\n' "$DIGEST"
    printf 'backup=%s\n' "$BK"
    printf 'hold_lanes=%s\n' "$hold_lanes"
  } > "$tmp"
  chmod 600 "$tmp"
  mv -f -- "$tmp" "$PENDING_FILE"
}

prune_images() {
  # Digest-pinned pulls leave untagged repo images. Protect the running image
  # and every image referenced by every retained backup, so retention remains
  # a usable local rollback set instead of a directory-only archive.
  local running_id protected_ids removed=0 freed=0 processed=0 size entry id target
  # docker inspect returns sha256:<full-id>, while docker images --format
  # '{{.ID}}' returns a 12-character short ID. Compare the same representation
  # or a successful update will try to remove its running image.
  short_image_id() {
    local image_id=${1#sha256:}
    printf '%s\n' "${image_id:0:12}"
  }
  running_id=$(short_image_id "$(docker inspect --format '{{.Image}}' cli-proxy-api 2>/dev/null || true)")
  protected_ids=$running_id
  while IFS= read -r backup_dir; do
    [[ -f "$backup_dir/compose.yml" ]] || continue
    # Prefer the id captured at backup time: a compose ref is usually a mutable
    # tag, so resolving it now would protect whatever the tag points at today
    # instead of the image this backup actually preserved.
    if [[ -f "$(image_id_file "$backup_dir")" ]]; then
      image_id=$(short_image_id "$(cat "$(image_id_file "$backup_dir")")")
      if ! docker image inspect "$image_id" >/dev/null 2>&1; then
        # The recorded image is already gone; nothing to protect, and the
        # backup must be reported as degraded rather than silently trusted.
        log "PRUNE_WARN scope=images stage=recorded_id_missing backup=$(basename "$backup_dir")"
        continue
      fi
    else
      image_ref=$(compose_image_ref "$backup_dir/compose.yml") || {
        log "PRUNE_FAILED scope=images stage=backup_reference path=$backup_dir/compose.yml"
        return 0
      }
      image_id=$(docker image inspect --format '{{.ID}}' "$image_ref" 2>/dev/null || true)
      if [[ -z "$image_id" ]]; then
        log "PRUNE_FAILED scope=images stage=backup_reference image=$image_ref"
        return 0
      fi
      image_id=$(short_image_id "$image_id")
    fi
    case " $protected_ids " in
      *" $image_id "*) : ;;
      *) protected_ids="$protected_ids $image_id" ;;
    esac
  done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '*-from-v[0-9]*' | sort)
  while IFS= read -r entry; do
    processed=$((processed + 1))
    id=${entry%% *}
    target=${entry#* }
    if [[ "$target" == *':<none>' ]]; then
      target=$id
    fi
    case " $protected_ids " in
      *" $id "*) continue ;;
    esac
    size=$(docker image inspect --format '{{.Size}}' "$id" 2>/dev/null || echo 0)
    if docker rmi "$target" >>"$LOG" 2>&1; then
      removed=$((removed + 1))
      freed=$((freed + size))
    else
      log "PRUNE_FAILED scope=images ref=$target"
      return 0
    fi
  done < <(docker images --format '{{.ID}} {{.Repository}}:{{.Tag}}' | awk -v repo="$CPA_IMAGE_REPO:" '$2 ~ "^"repo {print}')
  log "PRUNE scope=images kept=$((processed - removed)) removed=$removed freed_bytes=$freed policy=running_plus_retained_backups"
}
if ! secure_error_dumps; then
  log 'DEFER: error-dump permissions unavailable; provider probe/update blocked'
  exit 76
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
# Enabling plugins requires a joint compatibility canary, never a blind host
# update. The no-candidate daily readiness path above remains non-consuming.
require_plugin_canary
require_runtime_binary_canary
if ! health generation; then
  # A provider probe failure is not an update failure. Exit before the backup
  # directory and rollback trap are armed so the status cannot imply that an
  # update was attempted or rolled back.
  log 'DEFER: pre-update probe unavailable; image unchanged'
  exit 30
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
ROLLBACK_IMAGE_REF=$(compose_image_ref "$DIR/compose.yml")
docker image inspect "$ROLLBACK_IMAGE_REF" >/dev/null
# Stamp the concrete image id this backup captured. The compose ref alone is
# often a mutable tag; without this record a later prune protects the wrong
# image and a "restore this backup" instruction restores different code.
ROLLBACK_IMAGE_ID=$(record_image_ids "$BK") || {
  log "DEFER: cannot record rollback image id ref=$ROLLBACK_IMAGE_REF"
  exit 1
}
log "BACKUP_IMAGE_ID=$ROLLBACK_IMAGE_ID"
MUTATED=0
ROLLBACK_STAGE=prepare
ROLLBACK_VERIFIED=0
rollback() {
  local code=${1:-$?} rollback_failed=0
  trap '' ERR INT TERM HUP
  if [[ "$MUTATED" == 1 ]]; then
    ROLLBACK_STAGE=restore
    cp -a "$BK/compose.yml" "$DIR/compose.yml" || rollback_failed=1
    if [[ "$rollback_failed" == 0 ]]; then
      ROLLBACK_STAGE=compose_up
      (cd "$DIR" && docker compose up -d --pull never >>"$LOG" 2>&1) || rollback_failed=1
    fi
    if [[ "$rollback_failed" == 0 ]]; then
      ROLLBACK_STAGE=health_readiness
      health readiness || rollback_failed=1
    fi
    if [[ "$rollback_failed" == 0 ]]; then
      ROLLBACK_STAGE=image_readback
      running_image=$(docker inspect --format '{{.Image}}' cli-proxy-api 2>/dev/null || true)
      expected_image=$(docker image inspect --format '{{.ID}}' "$ROLLBACK_IMAGE_REF" 2>/dev/null || true)
      if [[ -z "$running_image" || "$running_image" != "$expected_image" ]]; then
        rollback_failed=1
      fi
    fi
    if [[ "$rollback_failed" == 0 ]]; then
      ROLLBACK_STAGE=config_readback
      cmp -s "$BK/compose.yml" "$DIR/compose.yml" || rollback_failed=1
    fi
    if [[ "$rollback_failed" == 0 ]]; then
      ROLLBACK_VERIFIED=1
      log "ROLLBACK_VERIFIED restored=$CUR backup=$BK"
    else
      log "ROLLBACK_FAILED backup=$BK stage=$ROLLBACK_STAGE"
      set +e
    fi
  fi
  [[ "$code" != 0 ]] || code=1
  exit "$code"
}
trap - ERR
trap rollback ERR
trap 'ROLLBACK_HUP=1' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
IMAGE="eceasy/cli-proxy-api:$TARGET@$DIGEST"
docker pull "$IMAGE" >>"$LOG" 2>&1
if [[ "${ROLLBACK_HUP:-0}" == 1 ]]; then
  # HUP interrupts an in-flight docker child, but is deferred to a stable
  # transaction boundary. Do not let that child's failure bypass the verified
  # image and compose rollback.
  rollback 129
fi
docker image inspect "$IMAGE" >/dev/null
# Once acquired, existing requests have finished and new admission requests
# receive maintenance_in_progress until this process (including rollback)
# exits. Refuse old admission builds rather than trusting an idle snapshot.
verify_traffic_guard
exec 8<"$TRAFFIC_GUARD_LOCK"
if ! flock -w 60 -x 8; then
  log 'DEFER: active requests did not drain within 60s; image unchanged'
  # Exit 30 defers the restart without stopping the old image. Either behavior
  # is safe only while the operator preserves the held traffic guard: if the
  # old generation is restarted, every request updates admission and healthz
  # before traffic resumes; manually stopping CPA instead avoids restart.
  exit 30
fi
verify_traffic_guard
# Baseline for the post-restart hold check: a durable hold that already exists
# before the update belongs to the doctor's ==lane-hold== gate, not to this
# transaction. Only holds that appear across OUR container restart fail it.
if ! PRE_HOLDS=$(lane_hold_state); then
  log 'DEFER: admission durable state unreadable before update; image unchanged'
  exit 30
fi
if [[ "${ROLLBACK_HUP:-0}" == 1 ]]; then
  rollback 129
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
if [[ "${ROLLBACK_HUP:-0}" == 1 ]]; then
  rollback 129
fi
(cd "$DIR" && docker compose config --quiet && docker compose up -d --pull never >>"$LOG" 2>&1)
if [[ "${ROLLBACK_HUP:-0}" == 1 ]]; then
  rollback 129
fi
RESULT=0
health generation || RESULT=$?
if [[ "$RESULT" == 10 ]]; then
  # A provider-side stop signal (including 403/408/429/5xx) confirms that the
  # route decision is upstream. Confirm local readiness but
  # do not send another generation request from the updater.
  READY_RESULT=0
  health readiness || READY_RESULT=$?
  trap '' ERR INT TERM HUP
  if [[ "$READY_RESULT" == 10 ]]; then
    log "UNVERIFIED: upstream unavailable after update; retained=$TARGET backup=$BK readiness=UPSTREAM_UNAVAILABLE"
    write_pending_verification unknown
    exit 10
  fi
  if [[ "$READY_RESULT" != 0 ]]; then
    log "DEFER: post-update readiness failed result=$READY_RESULT; rolling back retained=$TARGET backup=$BK"
    rollback "$READY_RESULT"
  fi
  log "UNVERIFIED: upstream unavailable after update; retained=$TARGET backup=$BK readiness=HEALTH_OK"
  write_pending_verification unknown
  exit 10
fi
[[ "$RESULT" == 0 ]]
trap - ERR INT TERM HUP
# A hold that appeared across OUR restart means a request straddled the
# container recreation despite the drain (or admission latched something
# else); the image itself is verified, so keep it but leave the resolution to
# the operator instead of reporting a clean success (2026-10-21 canary case).
if ! POST_HOLDS=$(lane_hold_state); then
  log "UNVERIFIED: admission durable state unreadable after update; retained=$TARGET backup=$BK"
  write_pending_verification unknown
  exit 10
fi
NEW_HOLDS=""
if [[ -n "$POST_HOLDS" ]]; then
  NEW_HOLDS=$(comm -13 <(sort <<<"$PRE_HOLDS") <(sort <<<"$POST_HOLDS") || true)
fi
if [[ -n "$NEW_HOLDS" ]]; then
  log "UNVERIFIED: lane hold appeared across the container restart lanes=${NEW_HOLDS//$'\n'/,} retained=$TARGET backup=$BK; recover per cpa-gateway.md manual release"
  NEW_HOLD_LANES=$(while IFS='=' read -r lane_file _; do
    case "$lane_file" in
      chatgpt-risk.json|workbuddy-risk.json|antigravity-risk.json)
        printf '%s\n' "$lane_file"
        ;;
      *)
        printf 'unknown\n'
        ;;
    esac
  done <<< "$NEW_HOLDS" | sort -u | paste -sd, -)
  if [[ -z "$NEW_HOLD_LANES" || "$NEW_HOLD_LANES" == *unknown* ]]; then
    NEW_HOLD_LANES=unknown
  fi
  write_pending_verification "$NEW_HOLD_LANES"
  exit 10
fi
log "OK: updated $CUR -> $TARGET digest=$DIGEST backup=$BK"
# UNVERIFIED and rollback paths never reach these; failure here only logs and
# retries on the next update, never fails the completed update itself. The
# hygiene call must stay non-fatal for that reason: with `set -e` a bare
# `secure_error_dumps` would turn a successful update into exit 1.
prune_backups
prune_manual_backup_families
prune_images
secure_error_dumps || log 'WARN: error-dump hygiene failed after a verified update; non-fatal'
prune_error_dumps

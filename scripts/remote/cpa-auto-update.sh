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
RETENTION_PRESSURE_SECONDARY_KEEP=4
RETENTION_PRESSURE_FREE_KIB=8589934592
RETENTION_SOURCE_ARCHIVE_MIN_AGE_HOURS=168
RETENTION_MIN_FREE_KIB=4194304
DOCKER_BUILD_CACHE_KEEP_HOURS=24
CPA_IMAGE_REPO=eceasy/cli-proxy-api
FORK_IMAGE_REPO=cli-proxy-api-fork
# Families created by manual transactions. The updater's own backups
# ('*-from-v[0-9]*') keep their existing path below; everything else was
# previously unbounded (measured 2026-11-09: 117 dirs, ~800 MiB, on a disk at
# 77%). Each pattern is explicit rather than derived, so a typo cannot silently
# widen a delete.
# Each entry is a find -name pattern. Real directory names start with a UTC
# timestamp (20261108T150211.573447142Z-converge-...), so every pattern is
# anchored with a leading '*'. A bare 'admission-*' matches nothing -- caught
# by outputs/prune-family-drill-20261109.ps1 before it ever ran for real.
RETENTION_BACKUP_PATTERNS=(
  '*-admission-*'
  '*-plugin-*'
  '*-converge-*'
  '*-guardrail-*'
  '*-workbuddy-*'
  '*-fork-image-*'
)
# Nothing younger than this is ever pruned, so a debugging window always
# outlives the shift that created it.
RETENTION_MIN_AGE_HOURS=168

prune_verified_build_source_archives() {
  # Reclaim only the pinned Go rebuild inputs after both their checksummed
  # archives have survived one full week. Missing/corrupt archives retain
  # everything; a real Docker save and an exact path allowlist are mandatory.
  local source_root="${SOURCE_ARCHIVE_ROOT:-$DIR/source-archives}"
  local source_tree="${SOURCE_TREE:-/root/cpa-v8061-gemini-raw-build}"
  local go_cache="${GO_CACHE:-/root/wb-0987-go-cache}"
  local allow_nonproduction="${SOURCE_ARCHIVE_ALLOW_NONPRODUCTION:-0}"
  local go_image=golang:1.26.10-bookworm
  local go_id source_archive toolchain_archive entry path
  local now_epoch cutoff source_epoch toolchain_epoch
  local source_tree_epoch go_cache_epoch
  local source_archive_was_verified toolchain_archive_was_verified
  local source_archive_stage toolchain_archive_stage
  local toolchain_created_at
  local toolchain_archived_now=0
  local -a candidates=()
  if [[ "$allow_nonproduction" != 1 &&
        "$source_root" != /opt/cliproxyapi/source-archives ]]; then
    log 'DISK_HYGIENE scope=source_archive skipped=unsafe_root'
    return 0
  fi
  if [[ "$allow_nonproduction" != 1 &&
        ( "$source_tree" != /root/cpa-v8061-gemini-raw-build ||
          "$go_cache" != /root/wb-0987-go-cache ) ]]; then
    log 'DISK_HYGIENE scope=source_archive skipped=unsafe_candidate'
    return 0
  fi
  if [[ "$source_root$source_tree$go_cache" == *$'\n'* ]]; then
    log 'DISK_HYGIENE scope=source_archive skipped=unsafe_path'
    return 0
  fi
  for entry in "$source_root" "$source_tree" "$go_cache"; do
    if [[ -L "$entry" || ( -e "$entry" && ! -d "$entry" ) ]]; then
      log "DISK_HYGIENE scope=source_archive skipped=unsafe_path path=$entry"
      return 0
    fi
  done
  for entry in "$source_root/cpa-source.sha256" \
    "$source_root/cpa-go-toolchain.sha256"; do
    if [[ -e "$entry" && ( -L "$entry" || ! -f "$entry" ) ]]; then
      log "DISK_HYGIENE scope=source_archive skipped=unsafe_path path=$entry"
      return 0
    fi
  done
  for entry in "$source_root"/cpa-source.*.tar \
    "$source_root"/cpa-go-toolchain.*.oci; do
    if [[ -e "$entry" && ( -L "$entry" || ! -f "$entry" ) ]]; then
      log "DISK_HYGIENE scope=source_archive skipped=unsafe_path path=$entry"
      return 0
    fi
  done
  if ! command -v docker >/dev/null 2>&1 ||
     ! command -v tar >/dev/null 2>&1 ||
     ! command -v sha256sum >/dev/null 2>&1 ||
     ! command -v realpath >/dev/null 2>&1; then
    log 'DISK_HYGIENE scope=source_archive skipped=required_tool_absent'
    return 0
  fi
  if [[ ! -d "$source_tree" ]] ||
     ! go_id=$(docker image inspect --format '{{.Id}}' "$go_image" 2>/dev/null) ||
     [[ ! "$go_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    log 'DISK_HYGIENE scope=source_archive skipped=pinned_inputs_unreadable'
    return 0
  fi
  if [[ "${SOURCE_ARCHIVE_ALLOW_NONPRODUCTION:-0}" == 1 ]] &&
     [[ "$(realpath -e -- "$source_root" 2>/dev/null)" != \
       "$(realpath -m -- "$source_root" 2>/dev/null)" ]]; then
    log 'DISK_HYGIENE scope=source_archive skipped=unsafe_path'
    return 0
  fi
  if [[ ! -d "$source_root" ]]; then
    if [[ "$source_root" != /opt/cliproxyapi/source-archives ]]; then
      log 'DISK_HYGIENE scope=source_archive skipped=private_root_unavailable'
      return 0
    fi
    mkdir -m 700 -- "$source_root" || return 0
  fi
  if [[ -L "$source_root" ]] ||
     [[ "$(stat -c %a -- "$source_root" 2>/dev/null)" != 700 ]]; then
    log 'DISK_HYGIENE scope=source_archive skipped=private_root_unavailable'
    return 0
  fi
  now_epoch=$(date -u +%s) || return 0
  [[ "$now_epoch" =~ ^[0-9]+$ ]] || return 0

  source_archive=$(find "$source_root" -maxdepth 1 -type f \
    -name 'cpa-source.*.tar' -printf '%T@ %p\n' 2>/dev/null |
    sort -rn | head -n 1 | cut -d ' ' -f2-)
  source_epoch=$(sed -n 's/^created_at=//p' \
    "$source_root/cpa-source.created" 2>/dev/null || true)
  if [[ -n "$source_archive" && ! "$source_epoch" =~ ^[0-9]+$ ]]; then
    # Existing archives without an age record are protected. Recreating an
    # attested pair below starts a fresh, explicit retention window.
    source_archive_was_verified=0
  fi
  toolchain_archive=$(find "$source_root" -maxdepth 1 -type f \
    -name 'cpa-go-toolchain.*.oci' -printf '%T@ %p\n' 2>/dev/null |
    sort -rn | head -n 1 | cut -d ' ' -f2-)
  if [[ -n "$source_archive" && -s "$source_archive" &&
        -f "$source_root/cpa-source.sha256" &&
        ! -L "$source_root/cpa-source.sha256" ]] &&
     (cd "$source_root" && sha256sum --check --strict --status cpa-source.sha256); then
    source_archive_was_verified=1
  else
    source_archive_was_verified=0
  fi
  toolchain_archive_was_verified=0
  if [[ -n "$toolchain_archive" && -s "$toolchain_archive" &&
        -f "$toolchain_archive" &&
        -f "$source_root/cpa-go-toolchain.sha256" &&
        ! -L "$source_root/cpa-go-toolchain.sha256" ]] &&
     (cd "$source_root" && sha256sum --check --strict --status \
       cpa-go-toolchain.sha256); then
    toolchain_archive_was_verified=1
  fi
  if [[ -z "$source_archive" ]]; then
    source_archive="$source_root/cpa-source.$now_epoch.tar"
    source_archive_stage="$source_root/cpa-source.$now_epoch.new.sha256"
    if ! (cd "${source_tree%/*}" &&
          tar -cf "$source_archive" -- "${source_tree##*/}" &&
          cd "$source_root" &&
          sha256sum -- "$(basename -- "$source_archive")" > "$source_archive_stage" &&
          sha256sum --check --strict --status "$source_archive_stage" &&
          mv -f -- "$source_archive_stage" cpa-source.sha256 &&
          printf 'created_at=%s\n' "$now_epoch" > cpa-source.created.new &&
          mv -f -- cpa-source.created.new cpa-source.created); then
      rm -f -- "$source_archive" "$source_archive_stage"
      rm -f -- "$source_root/cpa-source.created.new"
      log 'DISK_HYGIENE scope=source_archive result=source_snapshot_failed'
      return 0
    fi
    source_epoch=$now_epoch
    log "DISK_HYGIENE scope=source_archive result=source_snapshot_created"
  elif (( source_archive_was_verified == 0 )); then
    log 'DISK_HYGIENE scope=source_archive result=source_snapshot_unverified'
    return 0
  fi
  source_archive=$(find "$source_root" -maxdepth 1 -type f \
    -name 'cpa-source.*.tar' -printf '%T@ %p\n' 2>/dev/null |
    sort -rn | head -n 1 | cut -d ' ' -f2-)
  if [[ -z "$source_archive" || -L "$source_archive" ||
        ! -s "$source_archive" ]] ||
     ! (cd "$source_root" &&
        sha256sum --check --strict --status cpa-source.sha256); then
    log 'DISK_HYGIENE scope=source_archive result=source_snapshot_unverified'
    return 0
  fi

  if (( toolchain_archive_was_verified == 1 )); then
    :
  else
    # A successful docker save with an empty file is not restorable.
    # Retain the source and replace only this non-symlink file/checksum pair.
    if [[ ( -e "$source_root/cpa-go-toolchain.created" &&
            ( -L "$source_root/cpa-go-toolchain.created" ||
              ! -f "$source_root/cpa-go-toolchain.created" ) ) ||
          ( -e "$source_root/cpa-go-toolchain.created.new" &&
            ( -L "$source_root/cpa-go-toolchain.created.new" ||
              ! -f "$source_root/cpa-go-toolchain.created.new" ) ) ]]; then
      log 'DISK_HYGIENE scope=source_archive retained=unsafe_candidate'
      return 0
    fi
    for entry in "$source_root"/cpa-go-toolchain.*.new.sha256; do
      if [[ -e "$entry" || -L "$entry" ]]; then
        [[ ! -L "$entry" && -f "$entry" ]] || return 0
        rm -f -- "$entry" || return 0
      fi
    done
    if [[ -L "$source_root/cpa-go-toolchain.sha256" ||
          ( -e "$source_root/cpa-go-toolchain.sha256" &&
            ! -f "$source_root/cpa-go-toolchain.sha256" ) ]]; then
      log 'DISK_HYGIENE scope=source_archive retained=unsafe_candidate'
      return 0
    fi
    if [[ ( -e "$source_root/cpa-go-toolchain.$now_epoch.oci" &&
            ! -f "$source_root/cpa-go-toolchain.$now_epoch.oci" ) ||
          -L "$source_root/cpa-go-toolchain.$now_epoch.oci" ]]; then
      log 'DISK_HYGIENE scope=source_archive retained=unsafe_candidate'
      return 0
    fi
    if [[ -e "$source_root/cpa-go-toolchain.$now_epoch.oci" ]]; then
      rm -f -- "$source_root/cpa-go-toolchain.$now_epoch.oci" || return 0
    fi
    toolchain_archive="$source_root/cpa-go-toolchain.$now_epoch.oci"
    toolchain_archive_stage="$source_root/cpa-go-toolchain.$now_epoch.new.sha256"
    # A same-second failed save left an untrusted output at the bounded
    # destination. Only a regular file was removed above; no source bytes are
    # released until the new save succeeds and its checksum is committed.
    if ! docker image save -o "$toolchain_archive" "$go_id"; then
      if [[ -e "$toolchain_archive" && ! -L "$toolchain_archive" &&
            -f "$toolchain_archive" ]]; then
        rm -f -- "$toolchain_archive" || return 0
      fi
      log 'DISK_HYGIENE scope=source_archive result=toolchain_snapshot_failed'
      return 0
    fi
    if [[ ! -s "$toolchain_archive" ]] ||
       ! (cd "$source_root" &&
          sha256sum -- "$(basename -- "$toolchain_archive")" > "$toolchain_archive_stage" &&
          sha256sum --check --strict --status "$toolchain_archive_stage" &&
          mv -f -- "$toolchain_archive_stage" cpa-go-toolchain.sha256 &&
          printf 'created_at=%s\n' "$now_epoch" > cpa-go-toolchain.created.new &&
          mv -f -- cpa-go-toolchain.created.new cpa-go-toolchain.created); then
      rm -f -- "$toolchain_archive_stage"
      rm -f -- "$source_root/cpa-go-toolchain.created.new"
      log 'DISK_HYGIENE scope=source_archive result=toolchain_snapshot_unverified'
      return 0
    fi
    toolchain_archived_now=1
    for entry in "$source_root"/cpa-go-toolchain.*.oci; do
      [[ "$entry" == "$toolchain_archive" ]] ||
        rm -f -- "$entry" || return 0
    done
    log "DISK_HYGIENE scope=source_archive result=toolchain_snapshot_created"
  fi
  toolchain_archive=$(find "$source_root" -maxdepth 1 -type f \
    -name 'cpa-go-toolchain.*.oci' -printf '%T@ %p\n' 2>/dev/null |
    sort -rn | head -n 1 | cut -d ' ' -f2-)
  if [[ -z "$toolchain_archive" || -L "$toolchain_archive" ||
        ! -s "$toolchain_archive" ]] ||
     ! (cd "$source_root" &&
        sha256sum --check --strict --status cpa-go-toolchain.sha256); then
    log 'DISK_HYGIENE scope=source_archive result=toolchain_snapshot_unverified'
    return 0
  fi
  # The source snapshot age starts when it was first archived, not when a
  # later pass rechecks it. The toolchain age likewise starts after a valid
  # Docker save; mtime alone can be touched by a failed/retried operation.
  source_epoch=$(sed -n 's/^created_at=//p' \
    "$source_root/cpa-source.created" 2>/dev/null || true)
  if [[ ! "$source_epoch" =~ ^[0-9]+$ ]]; then
    if [[ -e "$source_root/cpa-source.created" ||
          -L "$source_root/cpa-source.created" ||
          -e "$source_root/cpa-source.created.new" ||
          -L "$source_root/cpa-source.created.new" ]]; then
      log 'DISK_HYGIENE scope=source_archive retained=unsafe_candidate'
      return 0
    fi
    source_epoch=$now_epoch
    printf 'created_at=%s\n' "$source_epoch" > \
      "$source_root/cpa-source.created.new" || return 0
    mv -f -- "$source_root/cpa-source.created.new" \
      "$source_root/cpa-source.created" || return 0
  fi
  toolchain_created_at=$(sed -n 's/^created_at=//p' \
    "$source_root/cpa-go-toolchain.created" 2>/dev/null || true)
  if [[ ! "$toolchain_created_at" =~ ^[0-9]+$ ]]; then
    if [[ -e "$source_root/cpa-go-toolchain.created" ||
          -L "$source_root/cpa-go-toolchain.created" ||
          -e "$source_root/cpa-go-toolchain.created.new" ||
          -L "$source_root/cpa-go-toolchain.created.new" ]]; then
      log 'DISK_HYGIENE scope=source_archive retained=unsafe_candidate'
      return 0
    fi
    log 'DISK_HYGIENE scope=source_archive result=toolchain_snapshot_created'
    toolchain_created_at=$now_epoch
    printf 'created_at=%s\n' "$toolchain_created_at" > \
      "$source_root/cpa-go-toolchain.created.new" || return 0
    mv -f -- "$source_root/cpa-go-toolchain.created.new" \
      "$source_root/cpa-go-toolchain.created" || return 0
    toolchain_archived_now=1
  fi
  toolchain_epoch=$(stat -c %Y -- "$toolchain_archive") || return 0
  source_tree_epoch=$(stat -c %Y -- "$source_tree") || return 0
  go_cache_epoch=$(stat -c %Y -- "$go_cache") || return 0
  if [[ ! "$source_epoch" =~ ^[0-9]+$ ||
        ! "$toolchain_created_at" =~ ^[0-9]+$ ||
        ! "$toolchain_epoch" =~ ^[0-9]+$ ||
        ! "$source_tree_epoch" =~ ^[0-9]+$ ||
        ! "$go_cache_epoch" =~ ^[0-9]+$ ]]; then
    log 'DISK_HYGIENE scope=build_source retained=age_unreadable'
    return 0
  fi
  cutoff=$(( now_epoch - RETENTION_SOURCE_ARCHIVE_MIN_AGE_HOURS * 3600 ))
  if (( toolchain_archived_now == 1 ||
        source_epoch > cutoff || toolchain_created_at > cutoff ||
        toolchain_epoch > cutoff ||
        source_tree_epoch > cutoff || go_cache_epoch > cutoff )); then
    log 'DISK_HYGIENE scope=build_source retained=recent_rebuild_window'
    return 0
  fi
  # Recheck immediately before deletion and again after each directory is
  # released. Inputs changed during cleanup must not leave the image exposed.
  if ! (cd "$source_root" &&
       sha256sum --check --strict --status cpa-source.sha256 &&
       sha256sum --check --strict --status cpa-go-toolchain.sha256); then
    log 'DISK_HYGIENE scope=build_source retained=checksum_changed'
    return 0
  fi
  if [[ "$(realpath -e -- "$source_tree" 2>/dev/null)" != \
        "$(realpath -m -- "$source_tree" 2>/dev/null)" ||
        "$(realpath -e -- "$go_cache" 2>/dev/null)" != \
        "$(realpath -m -- "$go_cache" 2>/dev/null)" ]]; then
    log 'DISK_HYGIENE scope=build_source retained=unsafe_candidate'
    return 0
  fi
  for entry in "$source_tree" "$go_cache"; do
    [[ ! -L "$entry" && -d "$entry" ]] || {
      log "DISK_HYGIENE scope=build_source retained=unsafe_candidate path=$entry"
      return 0
    }
    candidates+=("$entry")
  done
  for entry in "${candidates[@]}"; do
    if ! rm -rf -- "$entry"; then
      log "DISK_HYGIENE scope=build_source retained=delete_failed path=$entry"
      continue
    fi
    if ! (cd "$source_root" &&
         sha256sum --check --strict --status cpa-source.sha256 &&
         sha256sum --check --strict --status cpa-go-toolchain.sha256); then
      log 'DISK_HYGIENE scope=build_source retained=checksum_changed'
      return 0
    fi
    log "DISK_HYGIENE scope=build_source result=source_removed path=$entry"
  done
  if ! docker image rm "$go_id" >> "$LOG" 2>&1; then
    log 'DISK_HYGIENE scope=build_source retained=toolchain_image_in_use'
    return 0
  fi
  log "DISK_HYGIENE scope=build_source result=toolchain_image_archive_retained id=$go_id"
}

retention_keep_count() {
  local free_kib
  free_kib=$(df -Pk -- "$BACKUP_ROOT" | awk 'NR == 2 {print $4}')
  if [[ "$free_kib" =~ ^[0-9]+$ ]] &&
     (( free_kib < RETENTION_PRESSURE_FREE_KIB )); then
    printf '%s\n' "$RETENTION_PRESSURE_SECONDARY_KEEP"
  else
    printf '%s\n' "$RETENTION_KEEP_BACKUPS"
  fi
}

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
  local total removed=0 rejected=0 entry canonical_parent leaf parent stamp
  local backup_root_real
  local -a snapshot=() candidates=()
  backup_root_real=$(realpath -e -- "$BACKUP_ROOT" 2>/dev/null) || {
    log 'PRUNE_FAILED scope=backups stage=root'
    return 0
  }
  total=$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '*-from-v[0-9]*' -printf . | wc -c) || {
    log 'PRUNE_FAILED scope=backups stage=inventory'
    return 0
  }
  # Select by directory mtime, matching retained_backup_paths() rather than
  # lexical names. Each snapshot entry is "mtime<TAB>path" with the path
  # occupying one NUL field, so paths may contain spaces or newlines.
  while IFS= read -r -d '' entry; do
    if [[ -L "$entry" ]]; then
      log "PRUNE_FAILED scope=backups stage=symlink path=$entry"
      return 0
    fi
    stamp=$(stat -c %Y -- "$entry" 2>/dev/null) || {
      log "PRUNE_FAILED scope=backups stage=mtime path=$entry"
      return 0
    }
    [[ "$stamp" =~ ^[0-9]+$ ]] || {
      log "PRUNE_FAILED scope=backups stage=mtime path=$entry"
      return 0
    }
    snapshot+=("$stamp"$'\t'"$entry")
  done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '*-from-v[0-9]*' -print0) || {
    log 'PRUNE_FAILED scope=backups stage=inventory'
    return 0
  }
  # Sort a private copy, then return full entries. Strip the timestamp only
  # when deleting, so path and selection stay framed by the same NUL field.
  while IFS= read -r -d '' path; do
    candidates+=("$path")
  done < <(printf '%s\0' "${snapshot[@]}" | sort -zrn)
  while IFS= read -r -d '' path; do
    [[ -n "$path" ]] || continue
    entry=${path#*$'\t'}
    leaf=${entry##*/}
    canonical_parent=$(dirname -- "$entry")
    parent=$(realpath -e -- "$canonical_parent" 2>/dev/null) || {
      log "PRUNE_FAILED scope=backups stage=path path=$entry"
      continue
    }
    if [[ "$parent" != "$backup_root_real" ||
          -L "$BACKUP_ROOT/$leaf" ||
          "$(realpath -e -- "$canonical_parent/$leaf" 2>/dev/null)" != "$canonical_parent/$leaf" ]]; then
      log "PRUNE_FAILED scope=backups stage=path_escape path=$entry"
      rejected=$((rejected + 1))
      continue
    fi
    if ! rm -rf -- "$entry"; then
      log "PRUNE_FAILED scope=backups path=$entry"
      return 0
    fi
    removed=$((removed + 1))
  done < <(printf '%s\0' "${candidates[@]}" | tail -zn "+$((RETENTION_KEEP_BACKUPS + 1))")
  log "PRUNE scope=backups kept=$((total - removed)) removed=$removed rejected=$rejected policy=keep_$RETENTION_KEEP_BACKUPS"
}

prune_manual_backup_families() {
  # Bound the families created by manual transactions. They were never pruned:
  # the updater only ever matched '*-from-v[0-9]*'. Measured 2026-11-09:
  # 184 backup dirs / 1247 MiB, of which 117 dirs (~800 MiB) could not expire.
  #
  # Per family: keep the newest RETENTION_KEEP_BACKUPS dirs, and never delete
  # one younger than RETENTION_MIN_AGE_HOURS even when over budget. Patterns
  # are listed explicitly rather than derived, so a typo cannot widen a delete.
  local pattern total removed=0 entry kept_here keep_count
  keep_count=$(retention_keep_count) || {
    log 'PRUNE_FAILED scope=backup_family stage=pressure_policy'
    return 0
  }
  for pattern in "${RETENTION_BACKUP_PATTERNS[@]}"; do
    # Same guard as prune_backups: under set -e/pipefail a missing or
    # unreadable BACKUP_ROOT would abort the whole daily pass at the
    # inventory stage before any update decision (backup_health, which
    # creates the root, runs later and treats absence as not_initialized).
    total=$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d ! -name '*-from-v[0-9]*' -name "$pattern" -printf . | wc -c) || {
      log 'PRUNE_FAILED scope=backup_family stage=inventory'
      continue
    }
    [ "$total" -gt "$keep_count" ] || continue
    removed=0
    while IFS= read -r entry; do
      [ -n "$entry" ] || continue
      if ! rm -rf -- "$entry"; then
        log "PRUNE_FAILED scope=backup_family pattern=$pattern path=$entry"
        continue
      fi
      removed=$((removed + 1))
    done < <(
      find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d ! -name '*-from-v[0-9]*' -name "$pattern" -printf '%T@ %p\n' |
        sort -rn |
        awk -v keep="$keep_count" 'NR > keep { sub(/^[^ ]+ /, ""); print }' |
        while IFS= read -r path; do
          age_h=$(( ( $(date +%s) - $(stat -c %Y "$path") ) / 3600 ))
          [ "$age_h" -lt "$RETENTION_MIN_AGE_HOURS" ] && continue
          printf '%s\n' "$path"
        done
    )
    kept_here=$((total - removed))
    log "PRUNE scope=backup_family pattern=$pattern kept=$kept_here removed=$removed keep=$keep_count min_age_h=$RETENTION_MIN_AGE_HOURS"
  done
}

prune_orphaned_containers() {
  # A created/exited CPA build container can retain both the newest build image
  # and its cache even though it no longer runs. Only exact reviewed names and
  # an already missing image qualify. Never remove a running container, any
  # other binary, a volume, an account, or a plugin/config object.
  local name running image id removed=0
  for name in cpa-wb-failover-build cpa-wb-failover-build-v2 cpa-wb-failover-build-v3; do
    if ! running=$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null); then
      continue
    fi
    [[ "$running" == false ]] || continue
    if ! image=$(docker inspect --format '{{.Image}}' "$name" 2>/dev/null) ||
       [[ ! "$image" =~ ^sha256:[0-9a-f]{64}$ ]]; then
      log "DISK_HYGIENE scope=orphan_container skipped=image_unreadable name=$name"
      continue
    fi
    # A stopped build container still references its image. Merely checking
    # that the image exists would never remove the reviewed orphan.
    if docker image inspect --format '{{.Id}}' "$image" >/dev/null 2>&1; then
      log "DISK_HYGIENE scope=orphan_container skipped=image_present name=$name"
      continue
    fi
    if ! id=$(docker ps --all --quiet --no-trunc --filter "name=^/${name}$" 2>/dev/null) ||
       [[ ! "$id" =~ ^[0-9a-f]{64}$ ]]; then
      log "DISK_HYGIENE scope=orphan_container skipped=identity_unreadable name=$name"
      continue
    fi
    if docker rm "$id" >>"$LOG" 2>&1; then
      removed=$((removed + 1))
    else
      log "DISK_HYGIENE scope=orphan_container result=remove_failed name=$name"
    fi
  done
  log "DISK_HYGIENE scope=orphan_container removed=$removed"
}

prune_build_cache() {
  # BuildKit cache is expensive to regenerate during the fork soak, but it is
  # reproducible and previously had no bound at all. Apply pressure only below
  # 4 GiB free; prune merely dangling records, and retain any cache recently
  # used for a rollback rebuild. A concurrent build keeps in-use records and
  # Docker's own atomicity, so this does not stop CPA, remove an image, read
  # credentials, or touch plugin/config state.
  local free_kib before_bytes after_bytes output removed_bytes
  if ! command -v docker >/dev/null 2>&1; then
    log "DISK_HYGIENE scope=build_cache skipped=docker_absent"
    return 0
  fi
  if ! free_kib=$(df -Pk "$DIR" | awk 'NR == 2 {print $4}') ||
     [[ ! "$free_kib" =~ ^[0-9]+$ ]]; then
    log "DISK_HYGIENE scope=build_cache skipped=disk_stat_failed"
    return 0
  fi
  if (( free_kib >= RETENTION_MIN_FREE_KIB )); then
    log "DISK_HYGIENE scope=build_cache skipped=free_space_ok free_kib=$free_kib minimum_free_kib=$RETENTION_MIN_FREE_KIB"
    return 0
  fi
  if ! before_bytes=$(docker system df --format '{{.Type}} {{.Size}}' 2>/dev/null | awk '$1 == "Build Cache" {print $2}'); then
    log "DISK_HYGIENE scope=build_cache skipped=inventory_failed"
    return 0
  fi
  before_bytes=${before_bytes:-unknown}
  if ! output=$(docker builder prune --force --filter "until=${DOCKER_BUILD_CACHE_KEEP_HOURS}h" 2>&1); then
    if [[ "$output" == *'cache is being used by another process'* ||
          "$output" == *'builder is being used by another process'* ]]; then
      log "DISK_HYGIENE scope=build_cache result=prune_busy"
      return 0
    fi
    log "DISK_HYGIENE scope=build_cache result=prune_failed"
    return 0
  fi
  printf '%s\n' "$output" >> "$LOG"
  removed_bytes=$(printf '%s\n' "$output" | sed -n 's/^Total:[[:space:]]*//p' | tail -n 1)
  removed_bytes=${removed_bytes:-unknown}
  if ! after_bytes=$(docker system df --format '{{.Type}} {{.Size}}' 2>/dev/null | awk '$1 == "Build Cache" {print $2}'); then
    after_bytes=unknown
  fi
  after_bytes=${after_bytes:-unknown}
  log "DISK_HYGIENE scope=build_cache result=pruned before=$before_bytes after=$after_bytes reclaimed=$removed_bytes free_kib_before=$free_kib keep_hours=$DOCKER_BUILD_CACHE_KEEP_HOURS"
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

backup_image_id() {
  local backup_dir="$1" image_id_file="$1/image-id" image_ref image_id
  if [[ -L "$backup_dir" || -L "$image_id_file" ||
        ( -e "$image_id_file" && ! -f "$image_id_file" ) ]]; then
    # Never follow a symlinked backup entry or identity file.
    return 1
  fi
  if [[ -f "$image_id_file" && ! -L "$image_id_file" ]]; then
    image_id=$(cat -- "$image_id_file")
    if [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
      printf '%s\n' "$image_id"
      return 0
    fi
  fi
  [[ -f "$backup_dir/compose.yml" ]] || return 1
  image_ref=$(compose_image_ref "$backup_dir/compose.yml") || return 1
  image_id=$(docker image inspect --format '{{.Id}}' "$image_ref" 2>/dev/null) || return 1
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "$image_id"
}

resolve_backup_image_id() {
  local backup_dir="$1" image_id
  if ! image_id=$(backup_image_id "$backup_dir"); then
    return 1
  fi
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
  # Normalize the recorded reference through Docker. The returned full id,
  # rather than untrusted text from the backup, is what prune_images protects.
  image_id=$(docker image inspect --format '{{.Id}}' "$image_id" 2>/dev/null) ||
    return 2
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || return 2
  printf '%s\n' "$image_id"
}

retained_backup_paths() {
  # Keep no-backup as a successful empty set: Docker may still own stale
  # generations that are safe to remove. A failed file inventory remains
  # distinct and fail-closed at the caller. Directories are NUL-delimited so a
  # newline in a path cannot create a second retention entry.
  local count=0 path mtime
  local -a ordered=()
  while IFS= read -r -d '' path; do
    count=$((count + 1))
    mtime=$(stat -c %Y -- "$path" 2>/dev/null) || return 1
    if [[ "$mtime" =~ ^[0-9]+$ && "$path" != *'|'* ]]; then
      ordered+=("$mtime|$path")
    fi
  done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -print0) || return 1
  if [[ "$count" -eq 0 ]]; then
    return 0
  fi
  printf '%s\0' "${ordered[@]}" |
    sort -zrn |
    head -zn "$RETENTION_KEEP_BACKUPS" |
    cut -z -d '|' -f2-
}

image_candidates() {
  # Repository history is not a backup set, so generated aliases are harmless.
  # Every generation is verified before it may become an rmi target.
  # Diagnostics go to stderr plus the log file: this function's stdout is a
  # command-substituted candidate list, so a log line on stdout would corrupt
  # it. Field names verified against live docker 2026-11-11: image inspect has
  # no .Repository field (affiliation lives in RepoTags/RepoDigests) and
  # .Created is an RFC3339 string, not a time object carrying .Unix.
  local repo="$1" id refs rootfs created count
  local rows
  ilog() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG" >&2; }
  if ! rows=$(docker image ls --all --no-trunc --format '{{.ID}}|{{.Repository}}:{{.Tag}}' "$repo" |
    awk -F'|' -v repo="$repo" '{
      repository=$2
      sub(/:<none>$/, "", repository)
      if (repository == repo) print $1 "\t" $2
    }' |
    sort -u); then
    return 1
  fi
  while IFS=$'\t' read -r id _; do
    # --no-trunc must return the same immutable identity used by rmi and the
    # running container. A short or malformed ID is never a deletion candidate.
    [[ "$id" =~ ^sha256:[0-9a-f]{64}$ ]] || continue
    # Re-read the repo affiliation from RepoTags/RepoDigests: an ID surfaced
    # under this repository must carry a matching tag or digest ref, or a
    # cross-repository alias of the same ID could enter the deletion set.
    refs=$(docker image inspect --format '{{json .RepoTags}} {{json .RepoDigests}}' "$id" 2>/dev/null) || {
      ilog "IMAGE_RETENTION_DEFERRED scope=$repo reason=repository_unreadable id=$id"
      return 1
    }
    [[ "$refs" == *"$repo:"* || "$refs" == *"$repo@"* ]] || {
      ilog "IMAGE_RETENTION_SKIP scope=$repo reason=repository_mismatch id=$id"
      continue
    }
    rootfs=$(docker image inspect --format '{{.RootFS.Layers}}' "$id" 2>/dev/null) || return 1
    created=$(docker image inspect --format '{{.Created}}' "$id" 2>/dev/null) || return 1
    created=$(date -u -d "$created" +%s 2>/dev/null) || return 1
    [[ "$created" =~ ^[0-9]+$ ]] || continue
    # Layer digests carry the sha256: prefix in live inspect output.
    [[ "$rootfs" =~ ^\[sha256:[0-9a-f]{64}( sha256:[0-9a-f]{64})*\]$ ]] || {
      ilog "IMAGE_RETENTION_SKIP scope=$repo reason=incomplete_or_unknown_rootfs id=$id"
      continue
    }
    count=$(printf '%s\n' "$rootfs" | grep -Eo '[0-9a-f]{64}' | wc -l)
    printf '%s\t%s\t%s\n' "$id" "$created" "$count"
  done <<< "$rows" | sort -u
}

prune_images() {
  # Digest-pinned pulls leave untagged repo images. Protect the running image
  # and the image ids recorded by the newest retained backup directories. The
  # projection builds its candidates from the same verified helper, so it can
  # inspect and remove only complete immutable IDs in either repository.
  local running_image="" protected_ids="" removed=0 freed=0 size id
  local image_id backup_result size_before size_after candidate_count
  local current_candidates="" forked_candidates="" candidates running_count=0 inventory=""
  local backup_dir encoded_inventory
  running_image=$(docker inspect --format '{{.Image}}' cli-proxy-api 2>/dev/null || true)
  if [[ ! "$running_image" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    log 'IMAGE_RETENTION_DEFERRED scope=upstream reason=running_image_unreadable'
    return 0
  fi
  # Count separately so a missing container is deferred rather than becoming
  # an arithmetic failure under set -e.
  if ! running_count=$(docker ps --all --filter name='^/cli-proxy-api$' --format '{{.ID}}' 2>/dev/null |
    awk 'NF {count++} END {print count + 0}'); then
    log 'IMAGE_RETENTION_DEFERRED scope=upstream reason=running_container_count_unreadable'
    return 0
  fi
  if [[ ! "$running_count" =~ ^[0-9]+$ || "$running_count" -gt 1 ]]; then
    log "IMAGE_RETENTION_DEFERRED scope=upstream reason=running_container_ambiguous count=${running_count:-unknown}"
    return 0
  fi
  # Only the newest retained directories are part of prune_backups' rollback
  # set. Older directories are not an unbounded image-retention policy.
  # Retain only the newest real top-level directories; ordinary files and
  # symlinks are outside the rollback set.
  if ! retained_backup_paths "" |
    while IFS= read -r -d '' backup_dir; do
      [[ "$backup_dir" != *$'\n'* ]] || return 1
      inventory="$inventory$backup_dir"$'\n'
    done; then
    log 'IMAGE_RETENTION_DEFERRED scope=upstream reason=backup_inventory_failed'
    return 0
  fi
  # The final pathname may not end in newline (for example "backup-<hash>").
  # Re-add it so the newest rollback image cannot be silently dropped.
  encoded_inventory=$(printf '%s' "$inventory" | base64 | tr -d '\n')
  while IFS= read -r -d '' backup_dir; do
    [[ -n "$backup_dir" ]] || continue
    if image_id=$(resolve_backup_image_id "$backup_dir"); then
      :
    else
      backup_result=$?
      if (( backup_result == 2 )); then
        log "PRUNE_WARN scope=images stage=recorded_id_missing backup=$(basename "$backup_dir")"
        continue
      fi
      # An unidentifiable old directory is not evidence that another saved
      # image must be retained. Never infer identity from a shared mutable tag.
      log "PRUNE_WARN scope=images stage=backup_identity_unreadable backup=$(basename "$backup_dir")"
      continue
    fi
    case " $protected_ids " in
      *" $image_id "*) : ;;
      *) protected_ids="$protected_ids $image_id" ;;
    esac
  done < <(printf '%s' "$encoded_inventory" | base64 -d)
  current_candidates=$(image_candidates "$CPA_IMAGE_REPO" | cut -f1 | sort -u) || {
    log 'IMAGE_RETENTION_DEFERRED scope=upstream reason=inventory_failed'
    return 0
  }
  if [[ "$FORK_IMAGE_REPO" != "$CPA_IMAGE_REPO" ]]; then
    forked_candidates=$(image_candidates "$FORK_IMAGE_REPO" | cut -f1 | sort -u) || {
      log 'IMAGE_RETENTION_DEFERRED scope=fork reason=inventory_failed'
      return 0
    }
  fi
  candidates=$(printf '%s\n%s\n' "$current_candidates" "$forked_candidates" | sed '/^$/d' | sort -u)
  candidate_count=$(grep -c . <<< "$candidates" || true)
  size_before=$(docker system df --format '{{.Type}} {{.Size}}' 2>/dev/null | awk '$1 == "Images" {print $2}' || true)
  while IFS= read -r id; do
    [[ "$id" =~ ^sha256:[0-9a-f]{64}$ ]] || continue
    [[ "$id" != "$running_image" ]] || continue
    case " $protected_ids " in
      *" $id "*) continue ;;
    esac
    # image_candidates has just revalidated repository ownership, creation
    # time, full ID and a complete rootfs layer for this immutable target.
    size=$(docker image inspect --format '{{.Size}}' "$id" 2>/dev/null) || {
      log "PRUNE_WARN scope=images stage=size_unreadable id=$id"
      continue
    }
    if docker rmi "$id" >>"$LOG" 2>&1; then
      removed=$((removed + 1))
      freed=$((freed + size))
    else
      log "PRUNE_FAILED scope=images reason=docker_refused id=$id"
    fi
  done < <(printf '%s\n' "$candidates")
  size_after=$(docker system df --format '{{.Type}} {{.Size}}' 2>/dev/null | awk '$1 == "Images" {print $2}' || true)
  log "PRUNE scope=images candidates=$candidate_count retained=$((candidate_count - removed)) removed=$removed freed_bytes=$freed policy=running_plus_8_retained_backups before=${size_before:-unknown} after=${size_after:-unknown}"
}
if ! secure_error_dumps; then
  log 'DEFER: error-dump permissions unavailable; provider probe/update blocked'
  exit 76
fi
prune_error_dumps
prune_orphaned_containers
# Local-only cleanup runs on EVERY pass, including the no-update path.
#
# Defect found 2026-11-09: everything below exited 0 when CUR == TARGET ("no
# newer mature release"), and all backup pruning sat after that exit. Cleanup
# therefore only happened on days when upstream had also cleared the 72h soak,
# while the families that actually grow (admission-*, plugin-*, converge-* from
# manual transactions) are unrelated to releases -- 117 dirs / ~800 MiB
# accumulated with no policy touching them.
#
# These two only delete local directories: no provider call, no generation, no
# config change, no restart. The 2026-09-21 rule ("the no-update path must not
# consume the subscription account") is about upstream traffic and still holds.
# Image retention is deterministic as well: keep the running image and the
# images recorded by the newest eight retained backup directories. Older
# generations, including fork ones, stay reclaimable candidates. It never
# derives an identity from a mutable tag or deletes a volume/config/auth
# object.
prune_backups
prune_manual_backup_families
prune_verified_build_source_archives
prune_build_cache
prune_images
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
import os, re, stat, sys, uuid
from pathlib import Path
p = Path(sys.argv[1])
original = p.read_bytes()
text = original.decode("utf-8")
matches = list(re.finditer(
    r'(?m)^[ \t]*image:[ \t]*eceasy/cli-proxy-api:[^\s#]+[ \t]*(?:#.*)?$',
    text,
))
if len(matches) != 1:
    raise SystemExit('REFUSE image replacement count')
match = matches[0]
line = match.group(0)
new_line = re.sub(
    r'eceasy/cli-proxy-api:[^\s#]+',
    lambda _: sys.argv[2],
    line,
    count=1,
)
updated = text[: match.start()] + new_line + text[match.end() :]
if updated == text:
    raise SystemExit(0)
metadata = p.stat(follow_symlinks=False)
if not stat.S_ISREG(metadata.st_mode) or p.is_symlink():
    raise SystemExit('REFUSE compose symlink or non-file')
tmp = p.parent / f".{p.name}.{os.getpid()}.{uuid.uuid4().hex}.new"
fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, stat.S_IMODE(metadata.st_mode))
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(updated)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, stat.S_IMODE(metadata.st_mode))
    current = p.stat(follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise SystemExit('REFUSE compose changed during replacement')
    os.replace(tmp, p)
    if hasattr(os, "O_DIRECTORY"):
        dir_fd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
except BaseException:
    try:
        os.close(fd)
    except OSError:
        pass
    tmp.unlink(missing_ok=True)
    raise
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
# UNVERIFIED and rollback paths never reach these; any failure here happens
# after the completed update and is therefore explicitly non-fatal.
secure_error_dumps || log 'WARN: error-dump hygiene failed after a verified update; non-fatal'
prune_error_dumps || log 'WARN: error-dump cleanup check failed after a verified update; non-fatal'

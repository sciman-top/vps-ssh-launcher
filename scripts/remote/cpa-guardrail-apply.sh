set -Eeuo pipefail

GUARDRAIL_PREFLIGHT_TMP=""
MUTATED=0
cleanup_guardrail_preflight() {
  local status=$?
  trap '' HUP INT TERM
  trap - EXIT
  if [ -n "$GUARDRAIL_PREFLIGHT_TMP" ]; then
    rm -rf -- "$GUARDRAIL_PREFLIGHT_TMP"
  fi
  exit "$status"
}
# Install the signal handlers before creating any private preflight material.
# A dropped transport must not leave a credential-bearing payload copy behind.
trap cleanup_guardrail_preflight EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving backups, restarts, and rollbacks.
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE cpa_busy vps-ssh-launcher-maintenance.lock held"; exit 1; }

DIR=/opt/cliproxyapi
RISK_STATE_DIR=/var/lib/cpa-admission
ADMISSION_INTEGRITY_CHECK=/usr/local/libexec/cpa-admission-integrity-check
ADMISSION_LAUNCHER=$DIR/cpa-admission-launch.sh
ADMISSION_BOOTSTRAP=/usr/local/libexec/cpa_admission_bootstrap.py
ADMISSION_REQUEST_GUARD=/usr/local/libexec/cpa_request_guard.py

ADMISSION_INTEGRITY_DROPIN=/etc/systemd/system/cpa-admission.service.d/10-integrity.conf
ADMISSION_INTEGRITY_PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
UPDATER_TIMEOUT_DROPIN=/etc/systemd/system/cliproxyapi-update.service.d/cpa-update-timeout.conf
# 2026-11-20 slot split. Every scheduled transaction on this host shares
# /run/vps-ssh-launcher-maintenance.lock and the updater never waits for it
# (`flock -n`), so its daily 04:00 UTC start decided which job actually ran:
# 2026-11-19T04:13:29Z "DEFER: no-update readiness failed" was the lock, not
# the upstream. The unit now starts at 04:00 and the other three jobs own
# 04:10/04:20/04:30.
UPDATER_TIMER_UNIT=/etc/systemd/system/cliproxyapi-update.timer
EXPECTED_ADMISSION_SHA256="__CPA_ADMISSION_SHA256__"
# -Apply recomputes the OAuth exclusion list from the route manifest, which
# would silently re-expose a quarantined lane. Refuse instead of undoing an
# explicit risk-control decision; the operator restores the lane first.
if [ -f "$DIR/oauth-quarantine.json" ] || [ -f "$DIR/antigravity-quarantine.json" ]; then
  echo "REFUSE OAuth lane quarantine is active; restore its provider explicitly before -Apply"
  exit 1
fi
# The deployed pin names the admission generation this host already runs. A
# full apply only re-projects that same generation: a pinned hash that is not
# the code this run would write means either a stale source tree about to roll
# the host back, or a new generation whose pin must rotate in one reviewed
# transaction (outputs/deploy-admission-*.ps1). Refuse instead of deciding
# that inside a bulk re-projection.
if [ ! -f "$ADMISSION_INTEGRITY_PIN" ]; then
  echo "REFUSE admission_integrity_pin_missing integrity guard not installed; use the reviewed admission deploy transaction"
  exit 1
fi
# A pending pin is the first half of the reviewed source/pin switch. Never let
# a bulk re-projection pair the new launcher with the old admission generation.
if [ "$(tr -d '\r\n' < "$ADMISSION_INTEGRITY_PIN")" != "$EXPECTED_ADMISSION_SHA256" ]; then
  echo "REFUSE admission_pin_transition_pending"
  exit 1
fi
if [ -e "$ADMISSION_INTEGRITY_PIN.new" ] || [ -e "$DIR/.cpa-admission.py.new" ]; then
  echo "REFUSE admission_pin_transition_pending"
  exit 1
fi
CURRENT_ADMISSION_PIN=$(awk 'NF {print $1; exit}' "$ADMISSION_INTEGRITY_PIN")
if [ "$CURRENT_ADMISSION_PIN" != "$EXPECTED_ADMISSION_SHA256" ]; then
  echo "REFUSE admission_integrity_pin_mismatch want=$EXPECTED_ADMISSION_SHA256 got=$CURRENT_ADMISSION_PIN; rotate admission code with a reviewed deploy transaction, not with -Apply"
  exit 1
fi
NGINX_CONF=/etc/nginx/conf.d/cpa-gateway.conf
FAIL2BAN_FILTER=/etc/fail2ban/filter.d/cpa-gateway.conf
FAIL2BAN_JAIL=/etc/fail2ban/jail.d/cpa-gateway.conf
ADMISSION_SCRIPT="$DIR/cpa-admission.py"
ADMISSION_CONFIG="$DIR/cpa-admission.json"
ADMISSION_UNIT=/etc/systemd/system/cpa-admission.service
LEGACY_ADMISSION_SCRIPT="$DIR/cpa-luna-admission.py"
LEGACY_ADMISSION_CONFIG="$DIR/cpa-luna-admission.json"
LEGACY_ADMISSION_UNIT=/etc/systemd/system/cpa-luna-admission.service
PROJECTION_COMMIT="__CPA_PROJECTION_COMMIT__"
BK=/root/cpa-guardrails-backup-$(date -u +%Y%m%dT%H%M%S.%NZ)
# 2026-11-20 CN-lane protection: every INTL WorkBuddy credential stays disabled
# while its plugin is closed dark. count_workbuddy_intl_disabled is the same
# counter the strict doctor uses, so the precondition here and the expectation
# there cannot drift apart. Nothing may tighten an auth file by rewriting it
# (refresh rotation owns those bytes), so the projection only ever lowers
# disabled to true.
# Count from THIS payload's policy module, not the deployed one: a transaction
# always runs one generation behind its own projection (the first rollout of
# this control is exactly that case), and asking the previous generation for a
# verb it never had refuses the transaction that would introduce it. The count
# only reads auth files, so the payload copy is authoritative for the decision.
INTL_COUNTER_DIR=$(mktemp -d /tmp/cpa-intl-counter.XXXXXXXXXX)
GUARDRAIL_PREFLIGHT_TMP=$INTL_COUNTER_DIR
python3 - "$INTL_COUNTER_DIR" <<'PY' || { echo "REFUSE intl_counter_payload_unwritable"; exit 1; }
import base64
import sys
from pathlib import Path
Path(sys.argv[1], "cpa_policy.py").write_bytes(base64.b64decode("__CPA_POLICY_B64__"))
PY
INTL_EXPECTED_DISABLED=$(python3 "$INTL_COUNTER_DIR/cpa_policy.py" count-intl-disabled "$DIR/auth" 2>/dev/null || true)
case "$INTL_EXPECTED_DISABLED" in
  ''|*[!0-9]*)
    rm -rf -- "$INTL_COUNTER_DIR"
    echo "REFUSE intl_disabled_counter_unavailable payload cpa_policy could not count INTL credentials; re-projection cannot prove which of them must stay disabled"
    exit 1
    ;;
esac
rm -rf -- "$INTL_COUNTER_DIR"
GUARDRAIL_PREFLIGHT_TMP=""
# A count between zero and the file total is NOT evidence of a partial
# projection: six of the seven INTL credentials were already disabled by the
# 2026-11-17 cutover, so the reviewed expectation starts at six and the
# projection raises it to seven. What must hold is that every INTL file is
# disabled after this transaction, which the post-projection readback checks
# against the same counter; refusing intermediate states here would have
# blocked the very rollout that completes the set.
INTL_AUTH_TOTAL=$(find "$DIR/auth" -maxdepth 1 -type f -name 'workbuddyai-*.json' 2>/dev/null | wc -l)
echo "INTL_AUTH_BASELINE files=$INTL_AUTH_TOTAL disabled_before=$INTL_EXPECTED_DISABLED"

prune_backup_history() {
  local keep=8 entry removed=0
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    if rm -rf -- "$entry"; then
      removed=$((removed + 1))
    else
      echo "PRUNE_FAILED scope=cpa_guardrails_backups path=$entry"
    fi
  done < <(
    find /root -mindepth 1 -maxdepth 1 -type d \
      -name 'cpa-guardrails-backup-*' -printf '%T@ %p\n' 2>/dev/null |
      sort -rn | tail -n +$((keep + 1)) | cut -d' ' -f2-
  )
  echo "PRUNE scope=cpa_guardrails_backups removed=$removed policy=keep_$keep"
}
ADMISSION_WAS_ENABLED=0
if systemctl is-enabled --quiet cpa-admission.service 2>/dev/null; then
  ADMISSION_WAS_ENABLED=1
fi
ADMISSION_WAS_ACTIVE=0
if systemctl is-active --quiet cpa-admission.service 2>/dev/null; then
  ADMISSION_WAS_ACTIVE=1
fi
LEGACY_ADMISSION_WAS_ENABLED=0
if systemctl is-enabled --quiet cpa-luna-admission.service 2>/dev/null; then
  LEGACY_ADMISSION_WAS_ENABLED=1
fi
LEGACY_ADMISSION_WAS_ACTIVE=0
if systemctl is-active --quiet cpa-luna-admission.service 2>/dev/null; then
  LEGACY_ADMISSION_WAS_ACTIVE=1
fi
NGINX_ADMISSION_ROUTE_BEFORE=0
if grep -Fq 'proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;' "$NGINX_CONF"; then
  NGINX_ADMISSION_ROUTE_BEFORE=1
fi

if ! mkdir -m 700 "$BK"; then
  echo "REFUSE backup_exists_or_create_failed path=$BK"
  exit 1
fi
# Every refusal below leaves this directory behind. An empty backup dir is
# newer than every real one, so it would occupy a slot in the keep-N window and
# evict a backup that actually holds rollback material. Remove it on the way
# out; the EXIT trap only runs after the directory has real content.
discard_empty_backup() {
  if [ -d "$BK" ] && [ -z "$(find "$BK" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    rmdir -- "$BK" 2>/dev/null || true
  fi
}
# Restarting CPA while a lane-owned stream is still in flight leaves the
# durable write-ahead marker on disk. Admission then applies its bounded
# verification window on the next start, even though the projection itself
# was healthy. Check the restart preconditions before the first mutation.
# This is a precondition only; the exclusive requests.lock below remains the
# authoritative drain gate.
if ! python3 - <<'PY_PREFLIGHT'
import json
import time
import urllib.request

deadline = time.monotonic() + 45
while True:
    with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
        idle = json.load(response)
    if idle.get('status') != 'ok':
        raise SystemExit('Apply requires an idle lane snapshot')
    lanes = idle.get('lanes')
    if isinstance(lanes, dict) and not any(
        not isinstance(lane, dict)
        or not isinstance(lane.get('state'), dict)
        or lane['state'].get('inflight')
        or lane['state'].get('pending')
        for lane in lanes.values()
    ):
        break
    if time.monotonic() >= deadline:
        raise SystemExit('Apply requires an idle lane snapshot')
    time.sleep(1)
PY_PREFLIGHT
then
  discard_empty_backup
  echo "REFUSE cpa_apply_requires_idle_lanes"
  exit 75
fi
cp -a "$DIR/config.yaml" "$BK/config.yaml"
cp -a "$DIR/compose.yml" "$BK/compose.yml"
cp -a "$DIR/auto-update.sh" "$BK/auto-update.sh"
if [ -f "$DIR/cpa_provider_routes.json" ]; then
  cp -a "$DIR/cpa_provider_routes.json" "$BK/cpa_provider_routes.json"
else
  : > "$BK/cpa_provider_routes.json.missing"
fi
if [ -f "$DIR/cpa-health.py" ]; then
  cp -a "$DIR/cpa-health.py" "$BK/cpa-health.py"
else
  : > "$BK/cpa-health.py.missing"
fi
if [ -f "$DIR/cpa_health_runtime.py" ]; then
  cp -a "$DIR/cpa_health_runtime.py" "$BK/cpa_health_runtime.py"
else
  : > "$BK/cpa_health_runtime.py.missing"
fi
if [ -f "$DIR/cpa_policy.py" ]; then
  cp -a "$DIR/cpa_policy.py" "$BK/cpa_policy.py"
else
  : > "$BK/cpa_policy.py.missing"
fi
if [ -f "$ADMISSION_SCRIPT" ]; then
  cp -a "$ADMISSION_SCRIPT" "$BK/cpa-admission.py"
else
  : > "$BK/cpa-admission.py.missing"
fi
if [ -f "$ADMISSION_CONFIG" ]; then
  cp -a "$ADMISSION_CONFIG" "$BK/cpa-admission.json"
else
  : > "$BK/cpa-admission.json.missing"
fi
# The INTL WorkBuddy lane has its own durable breaker bucket. Older hosts
# predate this file, so capture both its absence and any existing regular file
# before the first mutation; rollback must restore the exact pre-transaction
# state instead of leaving a new lane with an orphaned or stale hold marker.
if [ -e "$RISK_STATE_DIR/workbuddy-intl-risk.json" ]; then
  if [ -L "$RISK_STATE_DIR/workbuddy-intl-risk.json" ] ||
     [ ! -f "$RISK_STATE_DIR/workbuddy-intl-risk.json" ]; then
    echo "REFUSE workbuddy-intl-risk-state is not a regular file"
    discard_empty_backup
    exit 1
  fi
  cp -a "$RISK_STATE_DIR/workbuddy-intl-risk.json" "$BK/workbuddy-intl-risk.json"
else
  : > "$BK/workbuddy-intl-risk.json.missing"
fi
if [ "$INTL_EXPECTED_DISABLED" -gt 0 ]; then
  mkdir -m 700 "$BK/intl-auth"
  intl_backup_index=0
  for intl_auth in "$DIR"/auth/workbuddyai-*.json; do
    [ -f "$intl_auth" ] || continue
    intl_backup_index=$((intl_backup_index + 1))
    cp -a "$intl_auth" "$BK/intl-auth/$intl_backup_index.json"
    printf '%s\n' "$(basename "$intl_auth")" > "$BK/intl-auth/$intl_backup_index.name"
  done
  printf '%s\n' "$intl_backup_index" > "$BK/intl-auth/count"
  echo "BACKUP scope=intl_auth files=$intl_backup_index"
fi
if [ -f "$ADMISSION_UNIT" ]; then
  cp -a "$ADMISSION_UNIT" "$BK/cpa-admission.service"
else
  : > "$BK/cpa-admission.service.missing"
fi
if [ -f "$ADMISSION_LAUNCHER" ]; then
  cp -a "$ADMISSION_LAUNCHER" "$BK/cpa-admission-launch.sh"
else
  : > "$BK/cpa-admission-launch.sh.missing"
fi
if [ -f "$ADMISSION_BOOTSTRAP" ]; then
  cp -a "$ADMISSION_BOOTSTRAP" "$BK/cpa_admission_bootstrap.py"
else
  : > "$BK/cpa_admission_bootstrap.py.missing"
fi
if [ -f "$ADMISSION_REQUEST_GUARD" ]; then
  cp -a "$ADMISSION_REQUEST_GUARD" "$BK/cpa_request_guard.py"
else
  : > "$BK/cpa_request_guard.py.missing"
fi
if [ -f "$ADMISSION_INTEGRITY_CHECK" ]; then
  cp -a "$ADMISSION_INTEGRITY_CHECK" "$BK/cpa-admission-integrity-check"
else
  : > "$BK/cpa-admission-integrity-check.missing"
fi
if [ -f "$ADMISSION_INTEGRITY_DROPIN" ]; then
  cp -a "$ADMISSION_INTEGRITY_DROPIN" "$BK/cpa-admission-integrity.conf"
else
  : > "$BK/cpa-admission-integrity.conf.missing"
fi
if [ -f "$ADMISSION_INTEGRITY_PIN" ]; then
  cp -a "$ADMISSION_INTEGRITY_PIN" "$BK/cpa-admission-integrity-pin.txt"
else
  : > "$BK/cpa-admission-integrity-pin.txt.missing"
fi
if [ -f "$UPDATER_TIMEOUT_DROPIN" ]; then
  cp -a "$UPDATER_TIMEOUT_DROPIN" "$BK/cpa-update-timeout.conf"
else
  : > "$BK/cpa-update-timeout.conf.missing"
fi
# Optional on older hosts: a missing timer means nothing to project, not a
# refusal, and the doctor's ==updater-schedule== gate reports the absence.
if [ -f "$UPDATER_TIMER_UNIT" ]; then
  cp -a "$UPDATER_TIMER_UNIT" "$BK/cliproxyapi-update.timer"
else
  : > "$BK/cliproxyapi-update.timer.missing"
fi
if [ -f "$LEGACY_ADMISSION_SCRIPT" ]; then
  cp -a "$LEGACY_ADMISSION_SCRIPT" "$BK/cpa-luna-admission.py"
else
  : > "$BK/cpa-luna-admission.py.missing"
fi
if [ -f "$LEGACY_ADMISSION_CONFIG" ]; then
  cp -a "$LEGACY_ADMISSION_CONFIG" "$BK/cpa-luna-admission.json"
else
  : > "$BK/cpa-luna-admission.json.missing"
fi
if [ -f "$LEGACY_ADMISSION_UNIT" ]; then
  cp -a "$LEGACY_ADMISSION_UNIT" "$BK/cpa-luna-admission.service"
else
  : > "$BK/cpa-luna-admission.service.missing"
fi
cp -a "$NGINX_CONF" "$BK/cpa-gateway.conf"
if [ -f "$FAIL2BAN_FILTER" ]; then cp -a "$FAIL2BAN_FILTER" "$BK/cpa-gateway-filter.conf"; fi
if [ -f "$FAIL2BAN_JAIL" ]; then cp -a "$FAIL2BAN_JAIL" "$BK/cpa-gateway-jail.conf"; fi
chmod 700 "$BK"
# Keep the exact sources supplied to this transaction, not only the prior
# host state. If a validation fails after a write, a later manual rollback can
# restore the projected files to this snapshot without guessing which local
# generation was applied. These are non-secret code/config payloads; auth and
# provider API keys remain only in their dedicated mode-600 backups.
printf '%s\n' "source_commit=$PROJECTION_COMMIT" >"$BK/projection-sources.txt"
printf '%s\n' "source_tree=clean_payload_source_set" >>"$BK/projection-sources.txt"
mkdir -m 700 "$BK/projected-sources"
printf '%s' '__CPA_UPDATER_B64__' | base64 -d >"$BK/projected-sources/cpa-auto-update.sh"
printf '%s' '__CPA_HEALTH_B64__' | base64 -d >"$BK/projected-sources/cpa-health.py"
printf '%s' '__CPA_HEALTH_RUNTIME_B64__' | base64 -d >"$BK/projected-sources/cpa_health_runtime.py"
printf '%s' '__CPA_POLICY_B64__' | base64 -d >"$BK/projected-sources/cpa_policy.py"
printf '%s' '__CPA_PROVIDER_ROUTES_B64__' | base64 -d >"$BK/projected-sources/cpa_provider_routes.json"
printf '%s' '__CPA_ADMISSION_B64__' | base64 -d >"$BK/projected-sources/cpa-admission.py"
printf '%s' '__CPA_ADMISSION_CONFIG_B64__' | base64 -d >"$BK/projected-sources/cpa-admission.json"
printf '%s' '__CPA_ADMISSION_UNIT_B64__' | base64 -d >"$BK/projected-sources/cpa-admission.service"
printf '%s' '__CPA_ADMISSION_LAUNCHER_B64__' | base64 -d >"$BK/projected-sources/cpa-admission-launch.sh"
printf '%s' '__CPA_ADMISSION_BOOTSTRAP_B64__' | base64 -d >"$BK/projected-sources/cpa_admission_bootstrap.py"
printf '%s' '__CPA_REQUEST_GUARD_B64__' | base64 -d >"$BK/projected-sources/cpa_request_guard.py"
printf '%s' '__CPA_ADMISSION_INTEGRITY_CHECK_B64__' | base64 -d >"$BK/projected-sources/cpa-admission-integrity-check.sh"
printf '%s' '__CPA_ADMISSION_INTEGRITY_DROPIN_B64__' | base64 -d >"$BK/projected-sources/cpa-admission-integrity.conf"
printf '%s' '__CPA_ADMISSION_INTEGRITY_PIN_B64__' | base64 -d >"$BK/projected-sources/cpa-admission-integrity-pin.txt"
printf '%s' '__CPA_UPDATER_TIMEOUT_CONF_B64__' | base64 -d >"$BK/projected-sources/cpa-update-timeout.conf"
printf '%s' '__CPA_FAIL2BAN_FILTER_B64__' | base64 -d >"$BK/projected-sources/cpa-gateway-filter.conf"
printf '%s' '__CPA_FAIL2BAN_JAIL_B64__' | base64 -d >"$BK/projected-sources/cpa-gateway-jail.conf"
chmod 600 "$BK/projected-sources/"*
(
  cd "$BK/projected-sources"
  sha256sum -- cpa-gateway-filter.conf cpa-gateway-jail.conf
) >"$BK/projected-sources/nginx-fail2ban.sha256"
(
  cd "$BK/projected-sources"
  sha256sum -- cpa-auto-update.sh cpa-health.py cpa_health_runtime.py \
    cpa_policy.py cpa_provider_routes.json cpa-admission.py \
    cpa-admission.json cpa-admission.service cpa-admission-launch.sh \
    cpa_admission_bootstrap.py cpa_request_guard.py \
    cpa-admission-integrity-check.sh cpa-admission-integrity.conf \
    cpa-admission-integrity-pin.txt cpa-update-timeout.conf \
    cpa-gateway-filter.conf cpa-gateway-jail.conf
) >"$BK/projected-sources/SHA256SUMS"
echo "BACKUP scope=projected_sources files=17 nginx_fail2ban=3"
# Source snapshots exist to reproduce a projection, not to retain credentials.
# Fail closed if an unexpected payload contains an API-key-shaped field; the
# known host auth files and provider-key backups remain outside this directory.
projection_source_scan_dir="$BK/projected-sources"
projection_source_scan="$projection_source_scan_dir/SHA256SUMS"
if [ -L "$projection_source_scan_dir" ] || [ -L "$projection_source_scan" ]; then
  rm -f -- "$BK/projection-sources.txt"
  rm -rf -- "$projection_source_scan_dir"
  echo "REFUSE projected source snapshot is a symlink" >&2
  exit 4
fi
if [ ! -f "$projection_source_scan" ]; then
  rm -f -- "$BK/projection-sources.txt"
  rm -rf -- "$projection_source_scan_dir"
  echo "REFUSE projected source checksum receipt is missing" >&2
  exit 4
fi
scan_status=0
(
  cd -P -- "$projection_source_scan_dir" &&
  [ "$(pwd -P)" = "$(cd -P -- "$BK" && pwd -P)/projected-sources" ] &&
  PATH= && export PATH &&
  /usr/bin/grep -Eqi "api[-_]?key" SHA256SUMS
) || scan_status=$?
if [ "$scan_status" = 0 ]; then
  rm -f -- "$BK/projection-sources.txt"
  rm -rf -- "$projection_source_scan_dir"
  echo "REFUSE projected source snapshot contains API-key material" >&2
  exit 4
fi
if [ "$scan_status" != 1 ]; then
  rm -f -- "$BK/projection-sources.txt"
  rm -rf -- "$projection_source_scan_dir"
  echo "REFUSE projected source credential scan could not complete" >&2
  exit 4
fi

ROLLBACK_DONE=0
restore_all() {
  if [ "$ROLLBACK_DONE" -eq 1 ]; then return 0; fi
  ROLLBACK_DONE=1
  trap - EXIT HUP INT TERM
  set +e
  rollback_failed=0
  ROLLBACK_FIRST_FAILED_STEP=
  mark_rollback_failed() {
    rollback_failed=1
    if [ -z "$ROLLBACK_FIRST_FAILED_STEP" ]; then
      ROLLBACK_FIRST_FAILED_STEP="restore_all:${BASH_LINENO[0]:-unknown}"
    fi
  }
  # Both generations bind 8318. Stop the current process before restoring the
  # next file set, then recreate exactly the pre-transaction enable/active state.
  systemctl stop cpa-admission.service >/dev/null 2>&1 || true
  systemctl stop cpa-luna-admission.service >/dev/null 2>&1 || true
  cp -a "$BK/config.yaml" "$DIR/config.yaml" || mark_rollback_failed
  cp -a "$BK/compose.yml" "$DIR/compose.yml" || mark_rollback_failed
  cp -a "$BK/auto-update.sh" "$DIR/auto-update.sh" || mark_rollback_failed
  if [ -f "$BK/cpa_provider_routes.json" ]; then
    cp -a "$BK/cpa_provider_routes.json" "$DIR/cpa_provider_routes.json" || mark_rollback_failed
  else
    rm -f "$DIR/cpa_provider_routes.json" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-health.py" ]; then
    cp -a "$BK/cpa-health.py" "$DIR/cpa-health.py" || mark_rollback_failed
  else
    rm -f "$DIR/cpa-health.py" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa_health_runtime.py" ]; then
    cp -a "$BK/cpa_health_runtime.py" "$DIR/cpa_health_runtime.py" || mark_rollback_failed
  else
    rm -f "$DIR/cpa_health_runtime.py" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa_policy.py" ]; then
    cp -a "$BK/cpa_policy.py" "$DIR/cpa_policy.py" || mark_rollback_failed
  else
    rm -f "$DIR/cpa_policy.py" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-admission.py" ]; then
    cp -a "$BK/cpa-admission.py" "$ADMISSION_SCRIPT" || mark_rollback_failed
  else
    rm -f "$ADMISSION_SCRIPT" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-admission.json" ]; then
    cp -a "$BK/cpa-admission.json" "$ADMISSION_CONFIG" || mark_rollback_failed
  else
    rm -f "$ADMISSION_CONFIG" || mark_rollback_failed
  fi
  if [ -f "$BK/workbuddy-intl-risk.json" ]; then
    mkdir -p "$RISK_STATE_DIR" || mark_rollback_failed
    cp -a "$BK/workbuddy-intl-risk.json" "$RISK_STATE_DIR/workbuddy-intl-risk.json" || mark_rollback_failed
  elif [ -f "$BK/workbuddy-intl-risk.json.missing" ]; then
    rm -f "$RISK_STATE_DIR/workbuddy-intl-risk.json" || mark_rollback_failed
  fi
  # Restore each snapshot to its original filename. The snapshot copy exists
  # because the projection may rewrite an auth file's bytes (disabled flag);
  # a file the project did not touch is written back byte-for-byte anyway.
  if [ -f "$BK/intl-auth/count" ]; then
    intl_restore_index=0
    intl_restore_total=$(cat "$BK/intl-auth/count")
    while [ "$intl_restore_index" -lt "$intl_restore_total" ]; do
      intl_restore_index=$((intl_restore_index + 1))
      intl_restore_name=$(cat "$BK/intl-auth/$intl_restore_index.name")
      case "$intl_restore_name" in
        workbuddyai-*.json) ;;
        *)
          echo "ROLLBACK_FAILED intl_auth_name_unexpected"
          mark_rollback_failed
          continue
          ;;
      esac
      cp -a "$BK/intl-auth/$intl_restore_index.json" "$DIR/auth/$intl_restore_name" || mark_rollback_failed
    done
  fi
  if [ -f "$BK/cpa-admission.service" ]; then
    cp -a "$BK/cpa-admission.service" "$ADMISSION_UNIT" || mark_rollback_failed
  else
    rm -f "$ADMISSION_UNIT" || mark_rollback_failed
  fi
  if [ -f "$BK/cliproxyapi-update.timer" ]; then
    cp -a "$BK/cliproxyapi-update.timer" "$UPDATER_TIMER_UNIT" || mark_rollback_failed
  elif [ -f "$BK/cliproxyapi-update.timer.missing" ]; then
    rm -f "$UPDATER_TIMER_UNIT" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-admission-launch.sh" ]; then
    cp -a "$BK/cpa-admission-launch.sh" "$ADMISSION_LAUNCHER" || mark_rollback_failed
  elif [ -f "$BK/cpa-admission-launch.sh.missing" ]; then
    rm -f "$ADMISSION_LAUNCHER" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa_admission_bootstrap.py" ]; then
    cp -a "$BK/cpa_admission_bootstrap.py" "$ADMISSION_BOOTSTRAP" || mark_rollback_failed
  elif [ -f "$BK/cpa_admission_bootstrap.py.missing" ]; then
    rm -f "$ADMISSION_BOOTSTRAP" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa_request_guard.py" ]; then
    cp -a "$BK/cpa_request_guard.py" "$ADMISSION_REQUEST_GUARD" || mark_rollback_failed
  elif [ -f "$BK/cpa_request_guard.py.missing" ]; then
    rm -f "$ADMISSION_REQUEST_GUARD" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-admission-integrity-check" ]; then
    cp -a "$BK/cpa-admission-integrity-check" "$ADMISSION_INTEGRITY_CHECK" || mark_rollback_failed
  else
    rm -f "$ADMISSION_INTEGRITY_CHECK" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-admission-integrity.conf" ]; then
    mkdir -p "$(dirname "$ADMISSION_INTEGRITY_DROPIN")" || mark_rollback_failed
    cp -a "$BK/cpa-admission-integrity.conf" "$ADMISSION_INTEGRITY_DROPIN" || mark_rollback_failed
  else
    rm -f "$ADMISSION_INTEGRITY_DROPIN" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-admission-integrity-pin.txt" ]; then
    mkdir -p "$(dirname "$ADMISSION_INTEGRITY_PIN")" || mark_rollback_failed
    cp -a "$BK/cpa-admission-integrity-pin.txt" "$ADMISSION_INTEGRITY_PIN" || mark_rollback_failed
  else
    rm -f "$ADMISSION_INTEGRITY_PIN" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-update-timeout.conf" ]; then
    mkdir -p "$(dirname "$UPDATER_TIMEOUT_DROPIN")" || mark_rollback_failed
    cp -a "$BK/cpa-update-timeout.conf" "$UPDATER_TIMEOUT_DROPIN" || mark_rollback_failed
  else
    rm -f "$UPDATER_TIMEOUT_DROPIN" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-luna-admission.py" ]; then
    cp -a "$BK/cpa-luna-admission.py" "$LEGACY_ADMISSION_SCRIPT" || mark_rollback_failed
  else
    rm -f "$LEGACY_ADMISSION_SCRIPT" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-luna-admission.json" ]; then
    cp -a "$BK/cpa-luna-admission.json" "$LEGACY_ADMISSION_CONFIG" || mark_rollback_failed
  else
    rm -f "$LEGACY_ADMISSION_CONFIG" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-luna-admission.service" ]; then
    cp -a "$BK/cpa-luna-admission.service" "$LEGACY_ADMISSION_UNIT" || mark_rollback_failed
  else
    rm -f "$LEGACY_ADMISSION_UNIT" || mark_rollback_failed
  fi
  cp -a "$BK/cpa-gateway.conf" "$NGINX_CONF" || mark_rollback_failed
  if [ -f "$BK/cpa-gateway-filter.conf" ]; then
    cp -a "$BK/cpa-gateway-filter.conf" "$FAIL2BAN_FILTER" || mark_rollback_failed
  else
    rm -f "$FAIL2BAN_FILTER" || mark_rollback_failed
  fi
  if [ -f "$BK/cpa-gateway-jail.conf" ]; then
    cp -a "$BK/cpa-gateway-jail.conf" "$FAIL2BAN_JAIL" || mark_rollback_failed
  else
    rm -f "$FAIL2BAN_JAIL" || mark_rollback_failed
  fi
  chmod 600 "$DIR/config.yaml" || mark_rollback_failed
  chmod 700 "$DIR/auto-update.sh" || mark_rollback_failed
  if [ -f "$ADMISSION_SCRIPT" ]; then chmod 755 "$ADMISSION_SCRIPT" || mark_rollback_failed; fi
  if [ -f "$ADMISSION_CONFIG" ]; then chmod 644 "$ADMISSION_CONFIG" || mark_rollback_failed; fi
  if [ -f "$ADMISSION_UNIT" ]; then chmod 644 "$ADMISSION_UNIT" || mark_rollback_failed; fi
  if [ -f "$ADMISSION_INTEGRITY_CHECK" ]; then chmod 755 "$ADMISSION_INTEGRITY_CHECK" || mark_rollback_failed; fi
  if [ -f "$ADMISSION_INTEGRITY_DROPIN" ]; then chmod 644 "$ADMISSION_INTEGRITY_DROPIN" || mark_rollback_failed; fi
  if [ -f "$ADMISSION_INTEGRITY_PIN" ]; then chmod 644 "$ADMISSION_INTEGRITY_PIN" || mark_rollback_failed; fi
  if [ -f "$UPDATER_TIMEOUT_DROPIN" ]; then chmod 644 "$UPDATER_TIMEOUT_DROPIN" || mark_rollback_failed; fi
  if [ -f "$LEGACY_ADMISSION_SCRIPT" ]; then chmod 755 "$LEGACY_ADMISSION_SCRIPT" || mark_rollback_failed; fi
  if [ -f "$LEGACY_ADMISSION_CONFIG" ]; then chmod 644 "$LEGACY_ADMISSION_CONFIG" || mark_rollback_failed; fi
  if [ -f "$LEGACY_ADMISSION_UNIT" ]; then chmod 644 "$LEGACY_ADMISSION_UNIT" || mark_rollback_failed; fi
  systemctl daemon-reload >/dev/null 2>&1 || mark_rollback_failed
  docker compose -f "$DIR/compose.yml" config --quiet || mark_rollback_failed
  docker restart cli-proxy-api >/dev/null 2>&1 || mark_rollback_failed
  if [ "$ADMISSION_WAS_ENABLED" -eq 1 ]; then
    systemctl enable cpa-admission.service >/dev/null 2>&1 || mark_rollback_failed
  else
    systemctl disable cpa-admission.service >/dev/null 2>&1 || true
  fi
  if [ "$ADMISSION_WAS_ACTIVE" -eq 1 ]; then
    systemctl start cpa-admission.service >/dev/null 2>&1 || mark_rollback_failed
  else
    systemctl stop cpa-admission.service >/dev/null 2>&1 || true
  fi
  # The admission generation is a three-file set (script, pin, request guard).
  # A restore that mixes generations boots into the admission bootstrap's
  # mismatch crash loop, so verify the restored set before declaring rollback
  # complete (2026-11-21 incident evidence).
  if [ -f "$ADMISSION_SCRIPT" ] && [ -f "$ADMISSION_INTEGRITY_PIN" ] &&
    [ -f "$ADMISSION_REQUEST_GUARD" ] &&
    [ "$(sha256sum "$ADMISSION_SCRIPT" | awk '{print $1}')" = \
      "$(awk 'NF {print $1; exit}' "$ADMISSION_INTEGRITY_PIN")" ] &&
    grep -Fq "SOURCE_SHA256 = \"$(awk 'NF {print $1; exit}' "$ADMISSION_INTEGRITY_PIN")\"" \
      "$ADMISSION_REQUEST_GUARD" 2>/dev/null; then
    :
  else
    echo "ROLLBACK_FAILED admission_generation_set_mismatch"
    mark_rollback_failed
  fi
  if [ "$LEGACY_ADMISSION_WAS_ENABLED" -eq 1 ]; then
    systemctl enable cpa-luna-admission.service >/dev/null 2>&1 || mark_rollback_failed
  else
    systemctl disable cpa-luna-admission.service >/dev/null 2>&1 || true
  fi
  if [ "$LEGACY_ADMISSION_WAS_ACTIVE" -eq 1 ]; then
    systemctl start cpa-luna-admission.service >/dev/null 2>&1 || mark_rollback_failed
  else
    systemctl stop cpa-luna-admission.service >/dev/null 2>&1 || true
  fi
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || mark_rollback_failed
  else
    mark_rollback_failed
  fi
  fail2ban-client -t >/dev/null 2>&1 || mark_rollback_failed
  fail2ban-client reload --restart cpa-gateway >/dev/null 2>&1 || mark_rollback_failed
  if [ -f "$DIR/cpa-health.py" ]; then
    python3 "$DIR/cpa-health.py" readiness >/dev/null 2>&1 || mark_rollback_failed
  else
    mark_rollback_failed
  fi
  if [ -f "$DIR/cpa_policy.py" ]; then
    python3 "$DIR/cpa_policy.py" "$DIR/config.yaml" >/dev/null 2>&1 || mark_rollback_failed
  else
    mark_rollback_failed
  fi
  assert_merged_nginx_route_contract || mark_rollback_failed
  if [ "$NGINX_ADMISSION_ROUTE_BEFORE" -eq 1 ]; then
    assert_admission_nginx_route_contract || mark_rollback_failed
  elif assert_admission_nginx_route_contract; then
    mark_rollback_failed
  fi
  assert_public_route_contract || mark_rollback_failed
  ss -ltn | grep -Eq '127\.0\.0\.1:8317[[:space:]]' || mark_rollback_failed
  rm -rf -- "$projection_source_scan_dir"
  if [ "$ADMISSION_WAS_ACTIVE" -eq 1 ] || [ "$LEGACY_ADMISSION_WAS_ACTIVE" -eq 1 ]; then
    ss -ltn | grep -Eq '127\.0\.0\.1:8318[[:space:]]' || mark_rollback_failed
  fi
  if ss -ltn | grep -Eq '\[::\]:8317[[:space:]]|:::8317[[:space:]]'; then
    mark_rollback_failed
  fi
  if { [ "$ADMISSION_WAS_ACTIVE" -eq 1 ] || [ "$LEGACY_ADMISSION_WAS_ACTIVE" -eq 1 ]; } &&
     ss -ltn | grep -Eq '\[::\]:8318[[:space:]]|:::8318[[:space:]]'; then
    mark_rollback_failed
  fi
  if [ "$rollback_failed" -eq 0 ]; then
    echo "ROLLBACK_VERIFIED"
  else
    echo "ROLLBACK_FAILED first_step=${ROLLBACK_FIRST_FAILED_STEP:-unknown}"
  fi
  set -e
  return 0
}

migrate_legacy_admission() {
  if systemctl is-active --quiet cpa-luna-admission.service 2>/dev/null; then
    systemctl stop cpa-luna-admission.service >/dev/null 2>&1 || return 1
  fi
  if systemctl is-enabled --quiet cpa-luna-admission.service 2>/dev/null; then
    systemctl disable cpa-luna-admission.service >/dev/null 2>&1 || return 1
  fi
  rm -f "$LEGACY_ADMISSION_UNIT" "$LEGACY_ADMISSION_SCRIPT" "$LEGACY_ADMISSION_CONFIG" || return 1
}

rollback_on_exit() {
  rc=$?
  trap - EXIT HUP INT TERM
  # A signal that reaches the EXIT trap without a handler leaves $? at 0
  # (SIGHUP did exactly that: the remote half-applied state was kept and the
  # transaction reported success). The rollback decision therefore keys off the
  # transaction phase, never off $? alone.
  if [ "$rc" -ne 0 ] || [ "$MUTATED" -eq 1 ]; then
    restore_all
    echo "ROLLBACK transaction_failed rc=$rc"
  fi
  exit "$rc"
}

if ! grep -Eq '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  echo "REFUSE unexpected Nginx public listener"
  exit 1
fi
# || true keeps this pre-mutation refusal reachable: under pipefail a
# no-match grep fails the assignment before the REFUSE message.
RANDOM_PATH_BEFORE=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2 || true)
if [ -z "$RANDOM_PATH_BEFORE" ]; then
  echo "REFUSE missing Nginx random capability path"
  exit 1
fi
if ! grep -Eq 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/' "$NGINX_CONF"; then
  echo "REFUSE unexpected Nginx route shape"
  exit 1
fi
if ! test -f /etc/logrotate.d/nginx || ! grep -Fq '/var/log/nginx/*.log' /etc/logrotate.d/nginx; then
  echo "REFUSE existing Nginx logrotate does not cover gateway logs"
  exit 1
fi
if test -e /etc/logrotate.d/cpa-gateway; then
  echo "REFUSE duplicate cpa-gateway logrotate file exists"
  exit 1
fi
# || true keeps both this pass-through and the weak-key REFUSE below
# reachable: under pipefail a key-less config.yaml (e.g. after a partial
# panel write) fails the substitution before either branch runs.
MGMT_APPLY_ALLOW=$(grep -E '^[[:space:]]*allow-remote:' "$DIR/config.yaml" | head -1 | sed 's/.*:[[:space:]]*//' || true)
MGMT_APPLY_KEY=$(grep -A3 '^management:' "$DIR/config.yaml" | grep 'secret-key:' | sed 's/.*secret-key:[[:space:]]*//;s/"//g' || true)
if [ "$MGMT_APPLY_ALLOW" = "true" ] && [ "${#MGMT_APPLY_KEY}" -lt 32 ]; then
  echo "REFUSE remote management enabled without a strong (>=32 char) key"
  exit 1
fi
if python3 - "$DIR/config.yaml" "$DIR/auth" <<'PY'
import json
import sys
from pathlib import Path
import yaml

def contains_enabled(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).replace('_', '-') == 'identity-confuse' and child is True:
                return True
            if contains_enabled(child):
                return True
    elif isinstance(value, list):
        return any(contains_enabled(child) for child in value)
    return False

config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding='utf-8'))
if contains_enabled(config):
    raise SystemExit(1)
# The scan must not rewrite what it reads: run it unreachable, then require
# every byte to be unchanged (2026-11-20: a mangled lambda emptied
# config.yaml here and the guard still reported success because the parse saw
# fewer bytes).
snapshot = {}
for path in sorted(Path(sys.argv[2]).glob('*.json')):
    snapshot[path] = path.read_bytes()
for path, before in snapshot.items():
    if contains_enabled(json.loads(before.decode('utf-8'))):
        raise SystemExit(1)
    if path.read_bytes() != before:
        raise SystemExit(2)
PY
then
  :
else
  echo "REFUSE identity-confuse is enabled"
  exit 1
fi
if ! python3 - "$DIR/auth" <<'PY'
import stat
import sys
from pathlib import Path

auth = Path(sys.argv[1])
if not auth.is_dir() or stat.S_IMODE(auth.stat().st_mode) != 0o700:
    raise SystemExit(1)
for path in auth.iterdir():
    if path.is_file() and path.suffix in {'.json', '.cds'}:
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise SystemExit(1)
PY
then
  echo "REFUSE auth permissions"
  exit 1
fi
PORT_JSON=$(docker inspect --format '{{json .HostConfig.PortBindings}}' cli-proxy-api 2>/dev/null || true)
if [ -z "$PORT_JSON" ] || ! python3 - "$PORT_JSON" <<'PY'
import json
import sys

try:
    bindings = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    raise SystemExit(1)
expected = {"8317/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8317"}]}
if bindings != expected:
    raise SystemExit(1)
PY
then
  echo "REFUSE CPA host port is not loopback-only"
  exit 1
fi

assert_merged_nginx_route_contract() {
  dump_file=$(mktemp)
  if ! nginx -T >"$dump_file" 2>&1; then
    rm -f "$dump_file"
    return 1
  fi
  # Lane-aware counts: the 2026-09-30 443-fallback lane mirrors the canonical
  # shapes once, signalled by its private log_format marker.
  lane_count=$(grep -Ec 'log_format[[:space:]]+cpa_safe443' "$dump_file")
  expected_routes=$((1 + lane_count))
  listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$dump_file")
  ipv6_listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+\[::\]:8443[[:space:]]+ssl;' "$dump_file")
  route_count=$(grep -Ec 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/\(\.\*\)\$' "$dump_file")
  proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:831[78]/v1/\$1\$is_args\$args;' "$dump_file")
  auth_proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8317/v1/models\$is_args\$args;' "$dump_file")
  fallback_count=$(grep -Ec 'location[[:space:]]*/[[:space:]]*\{|return[[:space:]]+404;' "$dump_file")
  merged_path=$(grep -oE '/[0-9a-f]{16}/v1/' "$dump_file" | head -n 1 | cut -d/ -f2)
  rm -f "$dump_file"
  if [ "$listen_count" -eq 1 ] &&
     [ "$ipv6_listen_count" -eq 0 ] &&
     [ "$route_count" -eq "$expected_routes" ] &&
     [ "$proxy_count" -eq "$expected_routes" ] &&
     [ "$auth_proxy_count" -eq "$expected_routes" ] &&
     [ "$fallback_count" -ge 2 ] &&
     [ "$merged_path" = "$RANDOM_PATH_BEFORE" ]; then
    return 0
  fi
  echo "NGINX_CONTRACT_COUNTS listen=$listen_count ipv6=$ipv6_listen_count lane=$lane_count route=$route_count expected=$expected_routes proxy=$proxy_count auth_proxy=$auth_proxy_count fallback=$fallback_count path_match=$([ "$merged_path" = "$RANDOM_PATH_BEFORE" ] && echo yes || echo no)"
  return 1
}

assert_admission_nginx_route_contract() {
  dump_file=$(mktemp)
  if ! nginx -T >"$dump_file" 2>&1; then
    rm -f "$dump_file"
    return 1
  fi
  lane_count=$(grep -Ec 'log_format[[:space:]]+cpa_safe443' "$dump_file")
  expected_routes=$((1 + lane_count))
  admission_proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8318/v1/\$1\$is_args\$args;' "$dump_file")
  auth_proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8317/v1/models\$is_args\$args;' "$dump_file")
  rm -f "$dump_file"
  [ "$admission_proxy_count" -eq "$expected_routes" ] && [ "$auth_proxy_count" -eq "$expected_routes" ]
}

if ! assert_merged_nginx_route_contract; then
  echo "REFUSE unexpected merged Nginx route contract"
  exit 1
fi

DIAGNOSTIC_DIR=$(mktemp -d /tmp/cpa-apply.XXXXXXXXXX) || {
  echo "REFUSE private diagnostic directory"
  exit 1
}
GUARDRAIL_PREFLIGHT_TMP=$DIAGNOSTIC_DIR
cleanup_preflight_and_mutated() {
  local status=$?
  trap '' HUP INT TERM
  trap - EXIT
  if [ "${MUTATED:-0}" = 1 ] && [ "$status" -ne 0 ]; then
    MUTATED=0
    set +e
    restore_all
    echo "ROLLBACK transaction_failed rc=$status"
    set -e
  fi
  if [ -n "$DIAGNOSTIC_DIR" ]; then
    rm -rf -- "$DIAGNOSTIC_DIR"
  elif [ -n "$GUARDRAIL_PREFLIGHT_TMP" ]; then
    rm -rf -- "$GUARDRAIL_PREFLIGHT_TMP"
  fi
  GUARDRAIL_PREFLIGHT_TMP=""
  exit "$status"
}
chmod 700 -- "$DIAGNOSTIC_DIR" || {
  echo "REFUSE diagnostic directory permissions"
  exit 1
}
VERIFY_TLS="$DIAGNOSTIC_DIR/verify_tls.py"
cat >"$VERIFY_TLS" <<'PY'
__VERIFIED_TLS_HELPER__
PY
chmod 600 -- "$VERIFY_TLS" || {
  echo "REFUSE TLS helper permissions"
  exit 1
}
tls_curl_args() {
  python3 - "$VERIFY_TLS" "$DIAGNOSTIC_DIR/tls" "$1" "$2" <<'PY'
import importlib.util
import os
import sys

helper_path, verify_root, host, port = sys.argv[1:]
spec = importlib.util.spec_from_file_location("cpa_apply_verify_tls", helper_path)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load local TLS verification helper")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
os.makedirs(verify_root, mode=0o700, exist_ok=True)
os.chmod(verify_root, 0o700)
os.environ["VPS_LOCAL_TLS_VERIFY_DIR"] = verify_root
curl_args = module.verified_live_tls_curl_args(host, port)
if (
    len(curl_args) != 2
    or curl_args[0] != "--cacert"
    or not isinstance(curl_args[1], str)
    or not os.path.isabs(curl_args[1])
    or any(char in curl_args[1] for char in "\r\n")
):
    raise RuntimeError("unexpected local TLS curl arguments")
print("\n".join(curl_args))
PY
}

assert_public_route_contract() {
  server_name=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' "$NGINX_CONF")
  prefix=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
  if [ -z "$server_name" ] || [ -z "$prefix" ]; then
    return 1
  fi
  public_base="https://$server_name:8443"
  if ! tls_verify_output="$(tls_curl_args "$server_name" 8443 2>"$DIAGNOSTIC_DIR/tls-8443.err")"; then
    return 1
  fi
  mapfile -t tls_args <<<"$tls_verify_output"
  if [ "${#tls_args[@]}" -ne 2 ] || [ "${tls_args[0]}" != "--cacert" ]; then
    return 1
  fi
  valid_status=$(curl --noproxy '*' -sS "${tls_args[@]}" --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$prefix/v1/models" 2>/dev/null || echo 000)
  authenticated_status=$(curl --noproxy '*' -sS "${tls_args[@]}" --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -H "Authorization: Bearer $KEY" \
    -o /dev/null -w '%{http_code}' "$public_base/$prefix/v1/models" 2>/dev/null || echo 000)
  bare_status=$(curl --noproxy '*' -sS "${tls_args[@]}" --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/v1/models" 2>/dev/null || echo 000)
  wrong_prefix=0000000000000000
  if [ "$wrong_prefix" = "$prefix" ]; then wrong_prefix=ffffffffffffffff; fi
  wrong_status=$(curl --noproxy '*' -sS "${tls_args[@]}" --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$wrong_prefix/v1/models" 2>/dev/null || echo 000)
  [ "$valid_status" = "401" ] &&
    [ "$authenticated_status" = "200" ] &&
    [ "$bare_status" = "404" ] &&
    [ "$wrong_status" = "404" ] &&
    ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]' &&
    ! ss -ltn | grep -Eq '\[::\]:8443[[:space:]]|:::8443[[:space:]]'
}

# || true keeps this pre-mutation refusal reachable: under set -e a failing
# substitution aborts before the REFUSE message. The trap is armed after
# this block, so nothing has been mutated either way.
KEY=$(python3 - "$DIR/config.yaml" <<'PY'
import sys
import yaml

config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
access = config.get("access") if isinstance(config, dict) else None
keys = access.get("api-keys") if isinstance(access, dict) else None
if not isinstance(keys, list) or not keys or not isinstance(keys[0], str) or not keys[0]:
    raise SystemExit(1)
print(keys[0])
PY
) || true
if [ -z "$KEY" ]; then
  echo "REFUSE missing_client_key"
  exit 1
fi
# Arm before the first mutation. EXIT also covers unguarded set -e failures.
# HUP must be trapped explicitly: an untrapped SIGHUP still runs the EXIT trap,
# but with $? == 0, which is why the rollback decision cannot depend on it
# alone. MUTATED tracks the phase from the first backup of live state onwards;
# it is initialized before the first EXIT handler can run.
trap cleanup_preflight_and_mutated EXIT
# The backup directory is armed earlier, but nothing on the host has changed
# yet. From here the transaction starts rewriting live state; restore_all only
# becomes valid after the backup set holds every path it rewrites.

MUTATED=1

if ! python3 - <<'PY'
from base64 import b64decode
from copy import deepcopy
import json
from pathlib import Path
import os
import re
import tempfile
import yaml
from fnmatch import fnmatchcase
from urllib.parse import urlparse


def atomic_write(path: Path, text: str, mode: int) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


config_path = Path("/opt/cliproxyapi/config.yaml")
config_text = config_path.read_text(encoding="utf-8")
# Detect a damaged file BEFORE yaml parses it away. On 2026-11-06 the live file
# ended with two lines:
#       scheduler_priority_reclaim: true
#   ity_reclaim: true
# yaml folded the second into the first as a multi-line scalar, so the config
# still parsed and every semantic policy check passed; the damage was only
# visible as a key-shaped line at zero indent whose name is a partial repeat of
# the key immediately above it (a truncated re-write of the same line).
# safe_dump -- the only writer this projection allows -- can never emit that,
# so refuse instead of rebuilding from a damaged input: that is how a stray
# key becomes permanent.
tail_start = max(0, len(config_text.splitlines()) - 12)
tail_defects: list[str] = []
previous_key = ""
for number, raw in enumerate(config_text.splitlines()[tail_start:], start=tail_start + 1):
    if not raw.strip() or raw.lstrip().startswith(("---", "...", "#")):
        continue
    key = raw.split(":", 1)[0].strip() if ":" in raw else ""
    if raw[0].isspace():
        previous_key = key
        continue
    # A zero-indent line that is a proper suffix of the key just above it is a
    # truncated duplicate, not a valid top-level key.
    if key and previous_key and len(key) < len(previous_key) and previous_key.endswith(key):
        tail_defects.append(f"line {number}: {raw.rstrip()!r}")
    previous_key = key
if tail_defects:
    raise SystemExit(
        "REFUSE config.yaml tail carries a truncated duplicate key "
        "(foreign-written file): " + "; ".join(tail_defects)
    )
config_before = yaml.safe_load(config_text)
if not isinstance(config_before, dict):
    raise SystemExit("config.yaml is not a mapping")

# The file layout is CPA v8 (config-version: 8) since the 2026-10-11
# management-API write migrated it; canonical paths per
# internal/config/config_v8.go buildV8Paths in the v8.0.23 source.
if config_before.get("config-version") != 8:
    raise SystemExit("config-version must be 8; run the CPA v8 migration before -Apply")
routing_before = config_before.get("routing")
if not isinstance(routing_before, dict):
    raise SystemExit("routing must be a mapping")
retry_before = routing_before.get("retry")
if not isinstance(retry_before, dict) or retry_before.get("request-retry") not in (0, 1):
    raise SystemExit("unexpected routing.retry.request-retry value")
if retry_before.get("max-retry-credentials") not in (0, 1, 3):
    raise SystemExit("unexpected routing.retry.max-retry-credentials value")
if routing_before.get("force-model-prefix") is not True:
    raise SystemExit("routing.force-model-prefix must remain true")
cooldown_before = routing_before.get("cooldown")
if not isinstance(cooldown_before, dict) or cooldown_before.get("save-cooldown-status") is not False:
    # false since 2026-09-16: persisted cooldowns (.cds) turned transient upstream
    # capacity cooldowns into stuck catalog absences (upstream #5639/#5770); see
    # docs/runbooks/cpa-stale-cooldown-recovery.md.
    raise SystemExit("routing.cooldown.save-cooldown-status must remain false")
upstream_before = config_before.get("upstream")
if not isinstance(upstream_before, dict) or not isinstance(upstream_before.get("codex"), dict):
    raise SystemExit("upstream.codex must be a mapping")
api_keys_before = config_before.get("api-keys")
if not isinstance(api_keys_before, dict):
    raise SystemExit("api-keys must be a mapping (v8 file layout)")
compatibility = api_keys_before.get("openai-compatibility")
if not isinstance(compatibility, list):
    raise SystemExit("api-keys.openai-compatibility must be a list")


def parse_env(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\\\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


env_values = parse_env(b64decode("__CPA_PROVIDER_ENV_B64__").decode("utf-8-sig"))
route_manifest = json.loads(b64decode("__CPA_PROVIDER_ROUTES_B64__").decode("utf-8"))
provider_slots = route_manifest.get("providers")
if not isinstance(provider_slots, list) or not provider_slots:
    raise SystemExit("REFUSE provider route manifest is empty")
for route in provider_slots:
    if not isinstance(route, dict):
        raise SystemExit("REFUSE provider route manifest entry is invalid")
    priority = route.get("priority")
    if priority is not None and (type(priority) is not int or priority < -100 or priority > 100):
        raise SystemExit("REFUSE provider route priority must be an integer between -100 and 100")
forbidden_provider_keys = {
    "proxy",
    "proxy-url",
    "http-proxy",
    "https-proxy",
    "socks-proxy",
    "headers",
    "header",
    "custom-headers",
    "transport",
    "tls",
    "insecure-skip-verify",
    "skip-tls-verify",
}


def parse_required_slot(route):
    slot = int(route["slot"])
    expected_host = str(route["host"])
    default_path = str(route["path"])
    scheme = str(route.get("scheme", "https"))
    expected_port = route.get("port")
    allow_insecure_http = route.get("allow_insecure_http", False)
    if scheme == "http" and not (
        (
            slot == 3
            and expected_host == "35.213.82.91"
            and expected_port == 8003
            and allow_insecure_http is True
        )
        or (
            slot == 6
            and expected_host == "172.17.0.1"
            and expected_port == 18082
            and allow_insecure_http is True
        )
    ):
        raise SystemExit("REFUSE HTTP is allowed only for explicitly authorized local/pinned slots")
    if scheme not in ("https", "http") or (
        scheme == "https" and (expected_port is not None or allow_insecure_http is not False)
    ):
        raise SystemExit(f"REFUSE provider route slot {slot} has invalid transport policy")
    base_url = env_values.get(f"BASE_URL_{slot}", "")
    api_key = env_values.get(f"API_KEY_{slot}", "")
    try:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != scheme
            or parsed.hostname != expected_host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port != expected_port
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        path = parsed.path.rstrip("/")
    except (TypeError, ValueError):
        raise SystemExit(
            f"REFUSE env BASE_URL_{slot} must match the approved slot {slot} route"
        )
    if not api_key:
        raise SystemExit(f"REFUSE env API_KEY_{slot} is empty")
    expected_path = default_path.rstrip("/")
    if path != expected_path:
        raise SystemExit(
            f"REFUSE env BASE_URL_{slot} must match the approved slot {slot} route"
        )
    authority = f"{expected_host}:{expected_port}" if expected_port is not None else expected_host
    return f"{scheme}://{authority}{path}", api_key


def provider_host(entry):
    if not isinstance(entry, dict):
        return None
    return urlparse(str(entry.get("base-url", ""))).hostname


def model_entry(name):
    allowed = {
        "name",
        "alias",
        "input-modalities",
        "max-context-length",
        "thinking-levels",
    }
    if (
        not isinstance(name, dict)
        or not {"name", "alias"} <= set(name)
        or not set(name) <= allowed
    ):
        raise SystemExit("REFUSE provider route model entry is invalid")
    entry = {"name": name["name"], "alias": name["alias"]}
    modalities = name.get("input-modalities")
    if modalities is not None:
        if (
            not isinstance(modalities, list)
            or not modalities
            or any(not isinstance(value, str) for value in modalities)
            or any(value not in {"text", "image"} for value in modalities)
            or len(modalities) != len(set(modalities))
        ):
            raise SystemExit("REFUSE provider route input-modalities is invalid")
        entry["input-modalities"] = modalities
    max_context_length = name.get("max-context-length")
    if max_context_length is not None:
        if type(max_context_length) is not int or max_context_length <= 0:
            raise SystemExit(
                "REFUSE provider route max-context-length must be a positive integer"
            )
        entry["max-context-length"] = max_context_length
    thinking_levels = name.get("thinking-levels")
    if thinking_levels is not None:
        # 2026-11-20: the thinking vocabulary the runtime understands
        # (internal/thinking ParseLevelSuffix). Declaring it on the model makes
        # the capability explicit instead of inheriting the codex-family
        # default, which is what let an xhigh turn reach an upstream that
        # silently substituted another model.
        effort_levels = {"minimal", "low", "medium", "high", "xhigh", "max"}
        if (
            not isinstance(thinking_levels, list)
            or not thinking_levels
            or any(not isinstance(value, str) for value in thinking_levels)
            or any(value not in effort_levels for value in thinking_levels)
            or len(thinking_levels) != len(set(thinking_levels))
        ):
            raise SystemExit("REFUSE provider route thinking-levels is invalid")
        entry["thinking"] = {"levels": thinking_levels}
    return entry


def oauth_model_setting(entry):
    allowed = {"name", "alias", "max-context-length", "fork"}
    if (
        not isinstance(entry, dict)
        or "name" not in entry
        or "max-context-length" not in entry
        or not set(entry) <= allowed
    ):
        raise SystemExit("REFUSE OAuth model setting entry is invalid")
    name = entry.get("name")
    alias = entry.get("alias")
    fork = entry.get("fork")
    max_context_length = entry.get("max-context-length")
    if not isinstance(name, str) or not name.strip():
        raise SystemExit("REFUSE OAuth model setting name must be non-empty")
    if alias is not None and (not isinstance(alias, str) or not alias.strip()):
        raise SystemExit("REFUSE OAuth model setting alias must be non-empty")
    if fork is not None and type(fork) is not bool:
        raise SystemExit("REFUSE OAuth model setting fork must be boolean")
    if fork is not None and alias is None:
        raise SystemExit("REFUSE OAuth model setting fork requires an alias")
    if type(max_context_length) is not int or max_context_length <= 0:
        raise SystemExit(
            "REFUSE OAuth model setting max-context-length must be a positive integer"
        )
    setting = {"name": name.strip(), "max-context-length": max_context_length}
    if alias is not None:
        setting["alias"] = alias.strip()
    if fork:
        setting["fork"] = True
    return setting


def oauth_model_alias(entry):
    allowed = {"name", "alias", "fork", "display-name", "force-mapping"}
    if (
        not isinstance(entry, dict)
        or "name" not in entry
        or "alias" not in entry
        or not set(entry) <= allowed
    ):
        raise SystemExit("REFUSE OAuth model alias entry is invalid")
    name = entry.get("name")
    alias = entry.get("alias")
    if not isinstance(name, str) or not name.strip():
        raise SystemExit("REFUSE OAuth model alias name must be non-empty")
    if not isinstance(alias, str) or not alias.strip():
        raise SystemExit("REFUSE OAuth model alias alias must be non-empty")
    if name.strip().lower() == alias.strip().lower():
        raise SystemExit("REFUSE OAuth model alias must change the visible name")
    normalized = {"name": name.strip(), "alias": alias.strip()}
    if "fork" in entry:
        if type(entry["fork"]) is not bool:
            raise SystemExit("REFUSE OAuth model alias fork must be boolean")
        if entry["fork"]:
            normalized["fork"] = True
    if "display-name" in entry:
        display_name = entry["display-name"]
        if not isinstance(display_name, str) or not display_name.strip():
            raise SystemExit("REFUSE OAuth model alias display-name must be non-empty")
        normalized["display-name"] = display_name.strip()
    if "force-mapping" in entry:
        if type(entry["force-mapping"]) is not bool:
            raise SystemExit(
                "REFUSE OAuth model alias force-mapping must be boolean"
            )
        if entry["force-mapping"]:
            normalized["force-mapping"] = True
    return normalized


def build_provider(
    existing,
    name,
    base_url,
    api_key,
    models,
    route_headers,
    route_request_scoped_errors=None,
):
    provider = deepcopy(existing) if isinstance(existing, dict) else {}
    if "api-key" in provider:
        raise SystemExit(f"REFUSE {name} uses legacy api-key field")
    if "api-key-entries" in provider:
        raise SystemExit(f"REFUSE {name} uses legacy api-key-entries field")
    for raw_key in provider:
        normalized_key = str(raw_key).strip().replace("_", "-")
        if normalized_key not in forbidden_provider_keys:
            continue
        # A manifest-pinned static header is the only reviewed transport
        # carve-out: the exact key set and values come from the route
        # manifest. Allow an existing exact match or strict subset of the
        # manifest's pinned headers so adding a new reviewed header (e.g.
        # user-agent to prevent Cloudflare 1010) upgrades cleanly while any
        # undeclared or tampered header still fails closed.
        if normalized_key == "headers" and route_headers:
            existing_headers = provider.get("headers")
            if (
                isinstance(existing_headers, dict)
                and set(existing_headers) <= set(route_headers)
                and all(route_headers[k] == v for k, v in existing_headers.items())
            ):
                continue
        raise SystemExit(f"REFUSE {name} has forbidden transport override")
    entries = provider.get("keys")
    if entries is not None and (
        not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(entries[0], dict)
        or set(entries[0]) != {"api-key"}
    ):
        raise SystemExit(f"REFUSE {name} must have exactly one plain api-key entry")
    provider["name"] = name
    provider["base-url"] = base_url
    provider.pop("prefix", None)
    provider.pop("disabled", None)
    provider.pop("api-key", None)
    provider["keys"] = [{"api-key": api_key}]
    provider["models"] = [model_entry(model) for model in models]
    provider["request-retry"] = 0
    provider["disable-cooling"] = False
    provider["support-prompt-cache-key"] = False
    if not isinstance(provider.get("connection-pool"), dict) or provider["connection-pool"].get("enabled") is False:
        provider["connection-pool"] = {}
    if route_headers:
        provider["headers"] = deepcopy(route_headers)
    else:
        provider.pop("headers", None)
    if route_request_scoped_errors:
        provider["request-scoped-errors"] = deepcopy(route_request_scoped_errors)
    return provider


target_hosts = {route["host"] for route in provider_slots}
legacy_hosts = set(route_manifest.get("retired_hosts", []))
target_providers = []
remaining_compatibility = []
for item in compatibility:
    host = provider_host(item)
    name = item.get("name") if isinstance(item, dict) else None
    if host in target_hosts or host in legacy_hosts or name == "relay-8003":
        continue
    remaining_compatibility.append(item)

for route in provider_slots:
    slot = int(route["slot"])
    host = str(route["host"])
    name = str(route["name"])
    models = route["models"]
    default_path = str(route["path"])
    base_url, api_key = parse_required_slot(route)
    if str(route.get("scheme", "https")) == "http":
        print(
            f"INSECURE_HTTP_PROVIDER slot={slot} host={host} "
            "api_key_transport=cleartext"
        )
    existing = next(
        (item for item in compatibility if provider_host(item) == host),
        None,
    )
    projected = build_provider(
        existing,
        name,
        base_url,
        api_key,
        models,
        route.get("headers") or {},
        route.get("request-scoped-errors"),
    )
    # Provider priority is part of the route contract when declared.  Keep
    # omitted priorities untouched for legacy slots, while making the reviewed
    # OpenCode fallback priority reproducible instead of relying on a hand edit
    # surviving management-API reserialization.
    if "priority" in route:
        projected["priority"] = route["priority"]
    target_providers.append(projected)

config_after = deepcopy(config_before)
# v8-canonical location (config_v8.go maps the legacy top-level
# claude-header-defaults here); management-UI reserialization writes this form.
upstream_after = config_after.get("upstream")
if not isinstance(upstream_after, dict):
    upstream_after = {}
    config_after["upstream"] = upstream_after
claude_upstream_after = upstream_after.get("claude")
if not isinstance(claude_upstream_after, dict):
    claude_upstream_after = {}
    upstream_after["claude"] = claude_upstream_after
claude_header_defaults_after = claude_upstream_after.get("header-defaults")
if not isinstance(claude_header_defaults_after, dict):
    claude_header_defaults_after = {}
    claude_upstream_after["header-defaults"] = claude_header_defaults_after
claude_header_defaults_after["stabilize-device-profile"] = True
routing_after = config_after["routing"]
retry_after = routing_after.setdefault("retry", {})
if not isinstance(retry_after, dict):
    raise SystemExit("REFUSE routing.retry must be a mapping")
retry_after["request-retry"] = 0
# 2026-11-21 user-approved hardening: one credential per request. A failed
# account is not replayed across sibling consumer accounts; callers or an
# explicitly selected API-key route own later attempts.
retry_after["max-retry-credentials"] = 1
observability_after = config_after.setdefault("observability", {})
if not isinstance(observability_after, dict):
    raise SystemExit("REFUSE observability must be a mapping")
logs_after = observability_after.setdefault("logs", {})
if not isinstance(logs_after, dict):
    raise SystemExit("REFUSE observability.logs must be a mapping")
logs_after["error-logs-max-files"] = 5
logs_after["logs-max-total-size-mb"] = 32
usage_after = observability_after.get("usage")
if usage_after is None:
    usage_after = observability_after["usage"] = {}
if not isinstance(usage_after, dict):
    raise SystemExit("REFUSE observability.usage must be a mapping")
# Both values are CPA's defaults, pinned so the declared posture is
# machine-visible: nothing on this host consumes a Redis usage queue, and an
# unpinned absent key silently returns to the default after any management
# reserialization. No behavior change on a config that never set them.
usage_after["usage-statistics-enabled"] = True
usage_after["redis-usage-queue-retention-seconds"] = 3600
routing_after.update({
    # round-robin, not fill-first (2026-11-06): fill-first kept every WorkBuddy
    # turn on the first ready credential of an eight-account pool. Round-robin
    # spreads them; session affinity below still pins one conversation to one
    # credential, so prompt-cache locality is unaffected.
    "strategy": "round-robin",
    "session-affinity": True,
    # 2026-11-18: 1h -> 6h to preserve credential locality and prompt-cache
    # warmth across >1h workday pauses without altering new-session dispersal.
    "session-affinity-ttl": "6h",
    # CPA v8.0.45+ defaults an omitted value to "any".  Preserve an
    # established session across transient failures and rebind only for
    # quota/auth/model-support/permanent-unavailability signals.
    "session-affinity-rebind-on": "quota",
    # Keep child tasks on the parent's credential so a shared prefix/model
    # stays cache-local and a fork does not fan one burst across more accounts.
    "session-affinity-subagents": True,
})
# 2026-10-31: 60 -> 30. The single Codex OAuth credential turned one upstream
# server_is_overloaded into a whole-window zero-egress 503 auth_unavailable; an
# explicit upstream Retry-After still takes precedence over this floor. Blast
# radius knob only -- never clears quota cooldowns or rotates credentials.
cooldown_after = routing_after.setdefault("cooldown", {})
if not isinstance(cooldown_after, dict):
    raise SystemExit("REFUSE routing.cooldown must be a mapping")
cooldown_after["transient-error-cooldown-seconds"] = 30
config_after.setdefault("upstream", {}).setdefault("codex", {}).update({
    # Disabled on 2026-09-28. Holding the bootstrap keeps the downstream
    # response headers uncommitted until the upstream starts generating, which
    # measured 9.4-10.3s of dead air per Luna turn before the handshake was
    # released as a single burst. The classification it bought is redundant on
    # this host: the Codex pool holds one account (no credential to fail over
    # to) and cpa-admission classifies a capacity marker inside an HTTP 200
    # body by itself. The ceiling stays explicit so a re-enable is one edit.
    "stream-bootstrap-buffering": False,
    "stream-bootstrap-timeout": "0",
    # Scope usage_limit_reached quota cooldowns to the requested model so one
    # sibling (sol/luna/terra share one Plus account) hitting quota
    # does not black out the rest of the credential. Capacity blast-radius only.
    "model-level-cooling": True,
    # zstd request-body compression for the ChatGPT-Codex backend. Retained
    # Codex/Responses request bodies measure 1.09-2.78 MB here and the slow tail
    # of first-byte latency (p99 6.9s over 2419 Responses turns) is paid before
    # generation starts, so compressing the upload attacks the slow-first-token
    # symptom directly. 1024-byte floor keeps health/canary probes plain.
    "upstream-request-compression": True,
    "upstream-request-compression-min-bytes": 1024,
})
api_keys_after = config_after["api-keys"]
api_keys_after["openai-compatibility"] = remaining_compatibility + target_providers

# Codex-family routes (native /responses upstreams) are projected from the
# manifest the same way as openai-compatibility slots; each route borrows the
# API key of the provider slot it names (same upstream contract).
codex_routes = route_manifest.get("codex_api_key_routes")
if not isinstance(codex_routes, list):
    raise SystemExit("REFUSE codex_api_key_routes must be a list")
managed_codex_hosts = set()
managed_codex_urls = set()
codex_routes_projected = []
for route in codex_routes:
    if not isinstance(route, dict):
        raise SystemExit("REFUSE codex_api_key_routes entries must be mappings")
    route_name = str(route.get("name", "")).strip()
    route_base_url = str(route.get("base-url", "")).strip()
    route_slot = route.get("api_key_slot")
    route_models = route.get("models")
    if (
        not route_name
        or not route_base_url
        or type(route_slot) is not int
        or not isinstance(route_models, list)
        or not route_models
    ):
        raise SystemExit(
            f"REFUSE codex route {route_name!r} needs name/base-url/api_key_slot/models"
        )
    parsed_codex = urlparse(route_base_url)
    if (
        parsed_codex.scheme != "https"
        or not parsed_codex.hostname
        or parsed_codex.username is not None
        or parsed_codex.password is not None
        or parsed_codex.params
        or parsed_codex.query
        or parsed_codex.fragment
        or not parsed_codex.path
        or parsed_codex.path == "/"
    ):
        raise SystemExit(
            f"REFUSE codex route {route_name!r} base-url must be an exact https URL with a path"
        )
    route_headers = route.get("headers") or {}
    if not isinstance(route_headers, dict) or not all(
        isinstance(header_name, str)
        and header_name
        and header_name == header_name.lower()
        and isinstance(header_value, str)
        and header_value.strip()
        for header_name, header_value in route_headers.items()
    ):
        raise SystemExit(
            f"REFUSE codex route {route_name!r} headers must be a lowercase mapping"
        )
    codex_api_key = env_values.get(f"API_KEY_{route_slot}", "")
    if not codex_api_key:
        raise SystemExit(
            f"REFUSE codex route {route_name!r} api_key_slot {route_slot} has no env API_KEY_{route_slot}"
        )
    codex_host = parsed_codex.hostname
    if codex_host in managed_codex_hosts or route_base_url.rstrip("/") in managed_codex_urls:
        raise SystemExit(f"REFUSE codex route {route_name!r} duplicates a managed host/base-url")
    managed_codex_hosts.add(codex_host)
    managed_codex_urls.add(route_base_url.rstrip("/"))
    codex_group = {
        "base-url": route_base_url.rstrip("/"),
        "keys": [{"api-key": codex_api_key}],
        "models": [model_entry(model) for model in route_models],
    }
    if route_headers:
        codex_group["headers"] = dict(route_headers)
    codex_routes_projected.append(codex_group)

existing_codex_groups = api_keys_after.get("codex")
if existing_codex_groups is not None and not isinstance(existing_codex_groups, list):
    raise SystemExit("REFUSE api-keys.codex must be a list when present")
kept_codex_groups = []
if isinstance(existing_codex_groups, list):
    for item in existing_codex_groups:
        item_host = provider_host(item)
        item_url = str((item or {}).get("base-url", "")).rstrip("/")
        # Managed codex routes replace their own duplicates first; legacy
        # relays pointing at openai-compat target/retired hosts are dropped.
        if item_host in managed_codex_hosts or item_url in managed_codex_urls:
            continue
        if item_host in target_hosts or item_host in legacy_hosts:
            continue
        kept_codex_groups.append(item)
api_keys_after["codex"] = kept_codex_groups + codex_routes_projected

# A claude-family group authenticates upstream with ``x-api-key``, but CPA's
# ClaudeExecutor only sends that header shape to api.anthropic.com; every custom
# gateway gets ``Authorization: Bearer`` instead (measured 2026-10-16 on the zen
# go endpoint: Bearer alone is 401 "Go requires a credential", the same key in
# x-api-key is 200, and both together are still 200 because CPA applies the
# group headers after it sets its own auth header). The manifest therefore marks
# the credential's header with this placeholder instead of committing the
# secret, and the projector substitutes the named slot's key.
SLOT_API_KEY_HEADER_TOKEN = "<slot-api-key>"

# Claude-family routes (native Anthropic Messages upstreams) are projected the
# same way as the codex family: each route borrows the API key of the provider
# slot it names and pins the manifest headers, because the zen go endpoint
# rejects generation without the session header. CPA's ClaudeExecutor appends
# /v1/messages?beta=true to this base-url, so the base-url stops before /v1.
claude_routes = route_manifest.get("claude_api_key_routes")
if not isinstance(claude_routes, list):
    raise SystemExit("REFUSE claude_api_key_routes must be a list")
managed_claude_hosts = set()
managed_claude_urls = set()
claude_routes_projected = []
for route in claude_routes:
    if not isinstance(route, dict):
        raise SystemExit("REFUSE claude_api_key_routes entries must be mappings")
    route_name = str(route.get("name", "")).strip()
    route_base_url = str(route.get("base-url", "")).strip()
    route_slot = route.get("api_key_slot")
    route_models = route.get("models")
    if (
        not route_name
        or not route_base_url
        or type(route_slot) is not int
        or not isinstance(route_models, list)
        or not route_models
    ):
        raise SystemExit(
            f"REFUSE claude route {route_name!r} needs name/base-url/api_key_slot/models"
        )
    parsed_claude = urlparse(route_base_url)
    if (
        parsed_claude.scheme != "https"
        or not parsed_claude.hostname
        or parsed_claude.username is not None
        or parsed_claude.password is not None
        or parsed_claude.params
        or parsed_claude.query
        or parsed_claude.fragment
        or not parsed_claude.path
        or parsed_claude.path == "/"
    ):
        raise SystemExit(
            f"REFUSE claude route {route_name!r} base-url must be an exact https URL with a path"
        )
    route_headers = route.get("headers") or {}
    if not isinstance(route_headers, dict) or not all(
        isinstance(header_name, str)
        and header_name
        and header_name == header_name.lower()
        and isinstance(header_value, str)
        and header_value.strip()
        for header_name, header_value in route_headers.items()
    ):
        raise SystemExit(
            f"REFUSE claude route {route_name!r} headers must be a lowercase mapping"
        )
    claude_api_key = env_values.get(f"API_KEY_{route_slot}", "")
    if not claude_api_key:
        raise SystemExit(
            f"REFUSE claude route {route_name!r} api_key_slot {route_slot} has no env API_KEY_{route_slot}"
        )
    claude_host = parsed_claude.hostname
    if claude_host in managed_claude_hosts or route_base_url.rstrip("/") in managed_claude_urls:
        raise SystemExit(f"REFUSE claude route {route_name!r} duplicates a managed host/base-url")
    managed_claude_hosts.add(claude_host)
    managed_claude_urls.add(route_base_url.rstrip("/"))
    claude_group = {
        "base-url": route_base_url.rstrip("/"),
        "keys": [{"api-key": claude_api_key}],
        "models": [model_entry(model) for model in route_models],
    }
    if route_headers:
        claude_group["headers"] = {
            header_name: (
                claude_api_key
                if header_value == SLOT_API_KEY_HEADER_TOKEN
                else header_value
            )
            for header_name, header_value in route_headers.items()
        }
    claude_routes_projected.append(claude_group)

existing_claude_groups = api_keys_after.get("claude")
if existing_claude_groups is not None and not isinstance(existing_claude_groups, list):
    raise SystemExit("REFUSE api-keys.claude must be a list when present")
kept_claude_groups = []
if isinstance(existing_claude_groups, list):
    for item in existing_claude_groups:
        item_host = provider_host(item)
        item_url = str((item or {}).get("base-url", "")).rstrip("/")
        # Managed claude routes replace their own duplicates first; legacy
        # relays pointing at openai-compat target/retired hosts are dropped.
        if item_host in managed_claude_hosts or item_url in managed_claude_urls:
            continue
        if item_host in target_hosts or item_host in legacy_hosts:
            continue
        kept_claude_groups.append(item)
api_keys_after["claude"] = kept_claude_groups + claude_routes_projected

# Keep all surviving Codex API-key lanes from competing with active provider
# aliases or retired names. The native Codex OAuth lane owns Luna; API-key
# providers own their bare routes. Preserve every other exclusion already present.
codex_api_keys = api_keys_after.get("codex")
if codex_api_keys is not None and not isinstance(codex_api_keys, list):
    raise SystemExit("REFUSE api-keys.codex must be a list when present")
if isinstance(codex_api_keys, list):
    for index, provider in enumerate(codex_api_keys):
        if not isinstance(provider, dict):
            raise SystemExit(f"REFUSE api-keys.codex[{index}] must be a mapping")
        excluded_models = provider.get("excluded-models", [])
        if not isinstance(excluded_models, list) or not all(
            isinstance(model, str) and model.strip() for model in excluded_models
        ):
            raise SystemExit(
                f"REFUSE api-keys.codex[{index}].excluded-models must be a list"
            )
        # The .get default is detached from the provider: a projected group
        # without the key would silently drop every append below.
        if provider.get("excluded-models") is not excluded_models:
            provider["excluded-models"] = excluded_models
        excluded_normalized = {model.strip().lower() for model in excluded_models}
        for model in route_manifest.get("codex_api_key_exclusions", []):
            if model not in excluded_normalized:
                excluded_models.append(model)
                excluded_normalized.add(model)

# Keep GPT-6 Luna on the existing Codex OAuth lane while pinning API-key
# aliases and retired names out of the OAuth lane. Replace only the old family
# wildcard; preserve all unrelated OAuth exclusions and refuse broader rules
# that would need an unsafe expansion to make Luna visible. Exact-name
# exclusions for aliases the manifest currently declares as OAuth routes are
# stale retirements and are dropped so the alias is served again.
oauth_route_aliases = {
    str(model["alias"]).strip().lower()
    for route in route_manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
oauth_route_aliases.update(
    str(model["alias"]).strip().lower()
    for provider in route_manifest.get("plugin_routes", [])
    if isinstance(provider, dict)
    for model in provider.get("models", [])
    if isinstance(model, dict)
    and isinstance(model.get("alias"), str)
    and model["alias"].strip()
)
oauth_route_aliases.update(
    model.strip().lower()
    for route in route_manifest.get("plugin_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, str) and model.strip()
)
oauth_route_aliases.update(
    entry["alias"].strip().lower()
    for entries in (route_manifest.get("oauth_model_aliases") or {}).values()
    if isinstance(entries, list)
    for entry in entries
    if isinstance(entry, dict)
    and isinstance(entry.get("alias"), str)
    and entry["alias"].strip()
)
oauth_alias_sources = {
    str(model["name"]).strip().lower()
    for provider in route_manifest.get("providers", [])
    if isinstance(provider, dict)
    for model in provider.get("models", [])
    if isinstance(model, dict)
    and isinstance(model.get("name"), str)
}
oauth_alias_sources.update(
    str(model["name"]).strip().lower()
    for route in route_manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict)
    and isinstance(model.get("name"), str)
)
oauth_alias_sources.update(
    model.strip().lower()
    for route in route_manifest.get("plugin_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, str) and model.strip()
)
oauth_block_after = config_after.setdefault("oauth", {})
if not isinstance(oauth_block_after, dict):
    raise SystemExit("REFUSE oauth must be a mapping")
# Serialize OAuth background auto-refresh workers so sibling credentials on the
# same BWG IP do not fire concurrent token refreshes in the same second
# (upstream defaults to 16 workers in conductor_refresh.go).
oauth_block_after["auth-auto-refresh-workers"] = 1
# Preserve the system-instruction compatibility fix when regenerating config.
oauth_providers_after = oauth_block_after.setdefault("providers", {})
if not isinstance(oauth_providers_after, dict):
    raise SystemExit("REFUSE oauth.providers must be a mapping")
antigravity_provider_after = oauth_providers_after.setdefault("antigravity", {})
if not isinstance(antigravity_provider_after, dict):
    raise SystemExit("REFUSE oauth.providers.antigravity must be a mapping")
antigravity_provider_after["sensitive-words"] = ["gpt", "openai", "codex"]
# v8.0.72+ ToS pool pause (upstream #7515). CPA treats an absent key as this
# default, so writing it changes no runtime behaviour; it makes the declared
# ban-response posture explicit in the file cpa_policy.py pins and the doctor
# reads, instead of depending on a CPA default that a later version could move.
antigravity_provider_after["tos-pause-window"] = "2h"
# Fork overlay daily-cap (deployed 2026-11-22): bound per-credential UTC daily
# attempts so heavy days spread across the pool instead of concentrating on one
# account. Pinned in cpa_policy.py so -Apply preserves it and doctor catches drift.
antigravity_provider_after["per-credential-daily-cap"] = 150
# Per-credential isolated HTTP/1.1 connection pool for Antigravity
# (v8.0.79 antigravity_executor.go scopes transports by auth.ID). Enabling
# avoids paying a fresh cross-ocean TCP+TLS handshake on every turn without
# sharing sockets across sibling credentials; 30s stays well under GFE's 240s
# idle cutoff.
antigravity_connection_pool_after = antigravity_provider_after.get("connection-pool")
if antigravity_connection_pool_after is None:
    antigravity_connection_pool_after = {}
if not isinstance(antigravity_connection_pool_after, dict):
    raise SystemExit(
        "REFUSE oauth.providers.antigravity.connection-pool must be a mapping"
    )
antigravity_connection_pool_after["enabled"] = True
antigravity_connection_pool_after["idle-conn-timeout"] = "30s"
antigravity_connection_pool_after["max-idle-conns-per-host"] = 2
antigravity_provider_after["connection-pool"] = antigravity_connection_pool_after
# Retain exact replacement only for the System Instruction projection. Natural
# user text is not rewritten below; this mapping's account-risk benefit remains
# unproven and needs separate review before any further use.
antigravity_provider_after["sensitive-words-replace"] = {
    "chatgpt": "chatservice",
    "openai": "vendor",
    "codex": "agentx",
    "gpt": "agentx",
}
oauth_exclusions = oauth_block_after.get("excluded-models")
if oauth_exclusions is None:
    oauth_exclusions = {}
if not isinstance(oauth_exclusions, dict):
    raise SystemExit("REFUSE oauth.excluded-models must be a mapping")
codex_exclusions = oauth_exclusions.get("codex", [])
if not isinstance(codex_exclusions, list) or not all(
    isinstance(pattern, str) and pattern.strip() for pattern in codex_exclusions
):
    raise SystemExit("REFUSE oauth.excluded-models.codex must be a list of strings")
codex_exclusions_after = []
legacy_gpt6_wildcards = {"gpt-6*", "gpt-6-*"}
for pattern in codex_exclusions:
    normalized_pattern = pattern.strip().lower()
    if fnmatchcase("gpt-6-luna", normalized_pattern):
        if normalized_pattern in legacy_gpt6_wildcards:
            continue
        raise SystemExit(
            "REFUSE unexpected Codex OAuth exclusion blocks gpt-6-luna"
        )
    if normalized_pattern in oauth_route_aliases:
        continue
    codex_exclusions_after.append(pattern)
for model in route_manifest.get("oauth_exclusions", []):
    if model not in {pattern.strip().lower() for pattern in codex_exclusions_after}:
        codex_exclusions_after.append(model)
oauth_exclusions["codex"] = codex_exclusions_after

# Claude subscription OAuth lane: every model the credential exposes beyond
# the three declared aliases is pinned into the provider-level exclusion list.
# Preserve a reviewed quarantine marker across -Apply so a bulk projection
# cannot silently re-expose the subscription route. This exclusion is OAuth-
# scoped and does not affect a separately configured Claude API-key group.
claude_exclusions = oauth_exclusions.get("claude", [])
if not isinstance(claude_exclusions, list) or not all(
    isinstance(pattern, str) and pattern.strip() for pattern in claude_exclusions
):
    raise SystemExit("REFUSE oauth.excluded-models.claude must be a list of strings")
manifest_claude_exclusions = route_manifest.get("claude_oauth_exclusions")
if not isinstance(manifest_claude_exclusions, list) or not all(
    isinstance(pattern, str) and pattern.strip()
    for pattern in manifest_claude_exclusions
):
    raise SystemExit(
        "REFUSE claude_oauth_exclusions must be a list of non-empty strings"
    )
oauth_exclusions["claude"] = list(manifest_claude_exclusions)
claude_quarantine_path = config_path.with_name("claude-oauth-quarantine.json")
if claude_quarantine_path.exists():
    try:
        claude_marker = json.loads(claude_quarantine_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(
            "REFUSE unreadable Claude OAuth quarantine marker: "
            + type(exc).__name__
        )
    claude_route_aliases = sorted(
        {
            model["alias"].strip().lower()
            for route in route_manifest.get("oauth_routes", [])
            if isinstance(route, dict) and route.get("name") == "claude-oauth"
            for model in route.get("models", [])
            if isinstance(model, dict)
            and isinstance(model.get("alias"), str)
            and model["alias"].strip()
        }
    )
    marker_aliases = claude_marker.get("aliases") if isinstance(claude_marker, dict) else None
    previous = (
        claude_marker.get("previous_claude_exclusions")
        if isinstance(claude_marker, dict)
        else None
    )
    applied = (
        claude_marker.get("applied_claude_exclusions")
        if isinstance(claude_marker, dict)
        else None
    )
    expected_previous = {str(value).strip().lower() for value in manifest_claude_exclusions}
    expected_applied = expected_previous | {"claude-*"}
    if (
        not isinstance(claude_marker, dict)
        or claude_marker.get("version") != 1
        or claude_marker.get("state") != "quarantined"
        or claude_marker.get("provider") != "claude"
        or not isinstance(marker_aliases, list)
        or sorted({str(value).strip().lower() for value in marker_aliases}) != claude_route_aliases
        or not isinstance(previous, list)
        or not all(isinstance(value, str) and value.strip() for value in previous)
        or {value.strip().lower() for value in previous} != expected_previous
        or not isinstance(applied, list)
        or not all(isinstance(value, str) and value.strip() for value in applied)
        or {value.strip().lower() for value in applied} != expected_applied
        or len(applied) != len(expected_applied)
    ):
        raise SystemExit("REFUSE invalid Claude OAuth quarantine marker")
    oauth_exclusions["claude"] = list(applied)

# Restrict the Antigravity (Google AI Pro) OAuth lane to the aliases declared
# in its route: every other model the credential exposes is pinned into the
# provider-level exclusion list, so an upstream catalog widening surfaces as
# a fail-closed unknown-ID breach instead of a silently wider shared lane.
antigravity_oauth_aliases = {
    str(model["alias"]).strip().lower()
    for route in route_manifest.get("oauth_routes", [])
    if isinstance(route, dict) and route.get("name") == "antigravity-oauth"
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
antigravity_exclusions = oauth_exclusions.get("antigravity", [])
if not isinstance(antigravity_exclusions, list) or not all(
    isinstance(pattern, str) and pattern.strip() for pattern in antigravity_exclusions
):
    raise SystemExit(
        "REFUSE oauth.excluded-models.antigravity must be a list of strings"
    )
if config_path.with_name("antigravity-quarantine.json").exists():
    raise SystemExit(
        "REFUSE OAuth lane quarantine is active; restore its provider explicitly before -Apply"
    )
manifest_antigravity_exclusions = route_manifest.get("antigravity_oauth_exclusions")
if not isinstance(manifest_antigravity_exclusions, list) or not all(
    isinstance(model, str) and model.strip()
    for model in manifest_antigravity_exclusions
):
    raise SystemExit("REFUSE antigravity_oauth_exclusions must be non-empty strings")
antigravity_exclusions_after = list(manifest_antigravity_exclusions)
if antigravity_oauth_aliases & {p.strip().lower() for p in antigravity_exclusions_after}:
    raise SystemExit("REFUSE antigravity exclusion blocks a declared antigravity alias")
oauth_exclusions["antigravity"] = antigravity_exclusions_after
oauth_block_after["excluded-models"] = oauth_exclusions

# Context-window metadata is pinned in the same manifest as the routes. The
# compatibility providers carry it on each model entry; OAuth and plugin
# channels carry it under oauth.settings. Managed channel lists are replaced
# wholesale so a retired override cannot survive, while unrelated channels
# already present in the config remain untouched.
oauth_settings_manifest = route_manifest.get("oauth_settings")
if (
    not isinstance(oauth_settings_manifest, dict)
    or not oauth_settings_manifest
):
    raise SystemExit("REFUSE oauth_settings must be a non-empty mapping")
oauth_settings_after = oauth_block_after.get("settings")
if oauth_settings_after is None:
    oauth_settings_after = {}
if not isinstance(oauth_settings_after, dict):
    raise SystemExit("REFUSE oauth.settings must be a mapping")
for channel, entries in oauth_settings_manifest.items():
    if not isinstance(channel, str) or not channel.strip():
        raise SystemExit("REFUSE oauth_settings channel must be non-empty")
    if not isinstance(entries, list) or not entries:
        raise SystemExit(
            f"REFUSE oauth_settings.{channel} must be a non-empty list"
        )
    normalized_entries = [oauth_model_setting(entry) for entry in entries]
    setting_keys = [
        (entry["name"], entry.get("alias")) for entry in normalized_entries
    ]
    if len(setting_keys) != len(set(setting_keys)):
        raise SystemExit(
            f"REFUSE oauth_settings.{channel} contains duplicate model settings"
        )
    oauth_settings_after[channel.strip()] = normalized_entries
oauth_block_after["settings"] = oauth_settings_after

# OAuth model aliases rename an existing upstream model to the client-visible
# route name, so a client-only catalog slug can be served without inventing a
# synthetic provider or silently forwarding a different model. Renaming drops
# the source's exact exclusion in the same channel and refuses a broader
# wildcard; aliases must already be declared OAuth route aliases so admission
# coverage cannot drift. An empty manifest retires every alias (see below).
oauth_aliases_manifest = route_manifest.get("oauth_model_aliases")
if not isinstance(oauth_aliases_manifest, dict):
    raise SystemExit("REFUSE oauth_model_aliases must be a mapping")
oauth_aliases_after = oauth_block_after.get("model-alias")
if oauth_aliases_after is None:
    oauth_aliases_after = {}
if not isinstance(oauth_aliases_after, dict):
    raise SystemExit("REFUSE oauth.model-alias must be a mapping")
if not oauth_aliases_manifest:
    # An empty manifest is a deliberate full retirement (gpt-reserve retired
    # 2026-11-04 by user directive). Drop every rename the previous projection
    # left behind instead of preserving it as a stale orphan, mirroring the
    # requests.payload wholesale-replacement rule below.
    oauth_aliases_after = {}
for channel, entries in oauth_aliases_manifest.items():
    if not isinstance(channel, str) or not channel.strip():
        raise SystemExit("REFUSE oauth_model_aliases channel must be non-empty")
    if not isinstance(entries, list) or not entries:
        raise SystemExit(
            f"REFUSE oauth_model_aliases.{channel} must be a non-empty list"
        )
    normalized_aliases = [oauth_model_alias(entry) for entry in entries]
    alias_keys = [entry["alias"].lower() for entry in normalized_aliases]
    if len(alias_keys) != len(set(alias_keys)):
        raise SystemExit(
            f"REFUSE oauth_model_aliases.{channel} contains duplicate aliases"
        )
    if not set(alias_keys) <= oauth_route_aliases:
        raise SystemExit(
            f"REFUSE oauth_model_aliases.{channel} alias is not an OAuth route alias"
        )
    # 2026-11-17 luode0320 cutover: on a plugin channel an alias may omit
    # fork — it then REPLACES the upstream id in the client surface (the
    # interim hy4free alias rode on hy4flash-free that way until the
    # 2026-11-17 rename retired it) and there is no dual-name
    # coexistence to pin. Every other channel still must fork every alias.
    plugin_channels = {
        route.get("provider")
        for route in route_manifest.get("plugin_routes", [])
        if isinstance(route, dict) and isinstance(route.get("provider"), str)
    }
    if channel not in plugin_channels and not all(
        entry["fork"] for entry in normalized_aliases
    ):
        raise SystemExit(
            f"REFUSE oauth_model_aliases.{channel} must fork every alias"
        )
    # A rename must take its upstream source out of the same channel's
    # exclusion list. Drop only an exact-name exclusion; a wildcard that still
    # matches the source is an unsafe broader rule and is refused.
    for entry in normalized_aliases:
        source = entry["name"].lower()
        if source not in oauth_alias_sources:
            raise SystemExit(
                f"REFUSE oauth_model_aliases.{channel} source is not an upstream model"
            )
        remaining_exclusions = []
        for pattern in oauth_exclusions.get(channel, []):
            if not isinstance(pattern, str):
                raise SystemExit(
                    f"REFUSE oauth.excluded-models.{channel} must contain strings"
                )
            normalized_pattern = pattern.strip().lower()
            if normalized_pattern == source:
                continue
            if fnmatchcase(source, normalized_pattern):
                raise SystemExit(
                    f"REFUSE oauth_model_aliases.{channel} source is wildcard "
                    f"excluded: {entry['name']}"
                )
            remaining_exclusions.append(pattern)
        oauth_exclusions[channel] = remaining_exclusions
    oauth_aliases_after[channel.strip()] = normalized_aliases
oauth_block_after["model-alias"] = oauth_aliases_after

# OAuth request-scoped error rules (v8.0.79, oauth.request-scoped-errors):
# stop bare Antigravity WAF 429s (no `details`) on credential #1 so flagged
# payloads do not fan out across sibling accounts (CLIProxyAPI #7614/#7768),
# and stop bare Codex `{"detail":"Rate limit exceeded"}` 429s from triggering
# exponential quota cooldown on the single-account lane (CLIProxyAPI #7691).
# Managed channels are pinned while unrelated channels stay preserved.
oauth_request_scoped_after = oauth_block_after.get("request-scoped-errors")
if oauth_request_scoped_after is None:
    oauth_request_scoped_after = {}
if not isinstance(oauth_request_scoped_after, dict):
    raise SystemExit("REFUSE oauth.request-scoped-errors must be a mapping")
oauth_request_scoped_after["antigravity"] = [
    {
        "status": 429,
        "match-regexr": [r'(?s)"status"\s*:\s*"RESOURCE_EXHAUSTED"\s*\}\s*\}\s*$'],
        "action": "stop-and-cooldown",
    },
    {
        "status": 503,
        "match": ["antigravity pool paused after TOS_VIOLATION"],
        "action": "stop",
    },
]
oauth_request_scoped_after["codex"] = [
    {
        "status": 429,
        "match-regexr": [r'^\s*\{\s*"detail"\s*:\s*"Rate limit exceeded"\s*\}\s*$'],
        "action": "stop",
    }
]
oauth_block_after["request-scoped-errors"] = oauth_request_scoped_after

# The requests.payload block is owned by cpa_policy: replace it wholesale
# from the same manifest derivation, so rules from retired lanes cannot
# survive as stale orphans (the claude-oauth retirement 2026-10-20 proved an
# append-only shape leaves orphans that exact-match policy validation then
# refuses). Drift between this derivation and cpa_policy is caught by the
# semantic_policy gate below, which compares against the policy itself.
codex_models = sorted({
    model["name"]
    for route in route_manifest.get("codex_api_key_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("name"), str)
})
claude_api_models = sorted({
    model["name"]
    for route in route_manifest.get("claude_api_key_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("name"), str)
})
claude_oauth_aliases = sorted({
    model["alias"]
    for route in route_manifest.get("oauth_routes", [])
    if isinstance(route, dict) and route.get("name") == "claude-oauth"
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
})
apply_patch_filter_models = route_manifest.get("apply_patch_filter_models")
if (
    not isinstance(apply_patch_filter_models, list)
    or not apply_patch_filter_models
    or any(not isinstance(name, str) or not name for name in apply_patch_filter_models)
    or len(apply_patch_filter_models) != len(set(apply_patch_filter_models))
):
    raise SystemExit("REFUSE apply_patch_filter_models must be unique non-empty strings")
# Zhipu rejects the OpenAI `developer` role with 400 code 1214; v8.0.51
# (00b76771) began preserving desktop developer items through the
# responses->chat translation and upstream #7202 declined a code revert,
# sanctioning the payload.override rewrite instead. Mirror cpa_policy's
# derivation exactly: the semantic_policy gate compares this block against
# the policy, so any drift between the two derivations fails the apply.
zhipu_developer_override = route_manifest.get("zhipu_developer_role_override")
if (
    not isinstance(zhipu_developer_override, dict)
    or zhipu_developer_override.get("from_protocol") != "responses"
    or not isinstance(zhipu_developer_override.get("models"), list)
    or not zhipu_developer_override["models"]
    or any(
        not isinstance(name, str) or not name
        for name in zhipu_developer_override["models"]
    )
    or len(zhipu_developer_override["models"])
    != len(set(zhipu_developer_override["models"]))
):
    raise SystemExit(
        "REFUSE zhipu_developer_role_override must carry from_protocol='responses' "
        "and unique non-empty model names"
    )
# WorkBuddy's gateway (copilot.tencent.com v2/chat/completions) began
# rejecting the OpenAI `developer` role with 11128 "Illegal API invocation
# from an unapproved channel" on 2026-11-17 ~20:50+08; Codex Desktop
# 0.169.0-alpha carries the desktop system prompt as developer-role input
# items, so every desktop deepseek turn 500s through the responses->chat
# translation. Mirror cpa_policy's derivation exactly: the semantic_policy
# gate compares this block against the policy, so any drift between the two
# derivations fails the apply.
workbuddy_developer_override = route_manifest.get("workbuddy_developer_role_override")
if (
    not isinstance(workbuddy_developer_override, dict)
    or workbuddy_developer_override.get("from_protocol") != "responses"
    or not isinstance(workbuddy_developer_override.get("models"), list)
    or not workbuddy_developer_override["models"]
    or any(
        not isinstance(name, str) or not name
        for name in workbuddy_developer_override["models"]
    )
    or len(workbuddy_developer_override["models"])
    != len(set(workbuddy_developer_override["models"]))
):
    raise SystemExit(
        "REFUSE workbuddy_developer_role_override must carry from_protocol='responses' "
        "and unique non-empty model names"
    )
# Natural user prompts and code identifiers are forwarded verbatim. The former
# fingerprint swap rewrote those strings without a durable provider contract
# and could break byte-stable cache prefixes. Older deployments may retain the
# rule outside this managed payload; that needs a separately reviewed purge.
requests_after = config_after.setdefault("requests", {})
if not isinstance(requests_after, dict):
    raise SystemExit("REFUSE requests must be a mapping")
server_after = config_after.setdefault("server", {})
if not isinstance(server_after, dict):
    raise SystemExit("REFUSE server must be a mapping")
server_after["shutdown-timeout"] = 15
# Safe bootstrap retry for streaming turns. Upstream defaults this to 0, so a
# stream that failed before its first byte reached the client surfaced as a dead
# turn and the client re-issued it -- a second upstream spend against a shared
# account for work the gateway could have replayed itself. 1 is the value
# upstream documents; it never fires after a byte is on the wire, and
# routing.retry.request-retry stays 0 so this cannot become a retry loop.
requests_streaming_after = requests_after.setdefault("streaming", {})
if not isinstance(requests_streaming_after, dict):
    raise SystemExit("REFUSE requests.streaming must be a mapping")
requests_streaming_after["keepalive-seconds"] = 15
requests_streaming_after["send-keepalive-before-ft"] = True
requests_streaming_after["bootstrap-retries"] = 1
# Bound upstream uTLS ClientHello handshakes so a stalled cross-ocean TLS
# handshake cannot hold an OAuth max_inflight slot for 300s.
requests_after["tls-handshake-timeout-seconds"] = 15
requests_after["payload"] = {
    "override": [
        {
            "models": [{"name": name}],
            "params": {
                'tools.#(type=="tool_search").parameters.required': [
                    "query",
                    "limit",
                ]
            },
        }
        for name in codex_models
    ]
    + [
        {
            "models": [{"name": name, "from-protocol": "responses"}],
            "params": {
                'messages.#(role=="developer")#.role': "system",
            },
        }
        for name in sorted(set(zhipu_developer_override["models"]))
    ]
    + [
        {
            "models": [{"name": name, "from-protocol": "responses"}],
            "params": {
                'messages.#(role=="developer")#.role': "system",
            },
        }
        for name in sorted(set(workbuddy_developer_override["models"]))
    ],
    "filter": [
        {
            "models": [{"name": name}],
            "params": [
                'tools.#(type=="web_search").search_content_types',
                'tools.#(type=="custom")',
            ],
        }
        for name in codex_models
    ]
    + [
        {
            "models": [{"name": name}],
            "params": [
                "safeguards",
                "context_management",
                "messages.#(output_config)#.output_config",
            ],
        }
        for name in claude_api_models
    ]
    + [
        {
            "models": [{"name": name, "protocol": "claude"}],
            "params": ["fallbacks"],
        }
        for name in claude_oauth_aliases
    ]
    + [
        {
            "models": [{"name": name}],
            "params": [
                'tools.#(type=="custom")',
                'input.#(type=="additional_tools")#.tools.#(name=="functions")#.tools.#(name=="apply_patch")#',
            ],
        }
        for name in apply_patch_filter_models
    ],
    "replace": [],
}

# The plugin owns its executor retry loop. CPA's max-retry-credentials only
# bounds the host scheduler and cannot prevent that loop replaying a request.
plugins_before = config_before.get("plugins")
plugins_after = config_after.get("plugins")
if plugins_after != plugins_before:
    raise SystemExit("REFUSE plugins changed before WorkBuddy policy projection")
if plugins_after is not None:
    if not isinstance(plugins_after, dict):
        raise SystemExit("REFUSE plugins must be a mapping")
    plugin_configs_after = plugins_after.get("configs")
    if plugin_configs_after is not None and not isinstance(plugin_configs_after, dict):
        raise SystemExit("REFUSE plugins.configs must be a mapping")
    if isinstance(plugin_configs_after, dict):
        for plugin, field, value in (
            ("workbuddy", "scheduler_priority_reclaim", True),
            ("workbuddy-provider", "retry_on_4xx", 0),
        ):
            section = plugin_configs_after.get(plugin)
            if section is None:
                continue
            if not isinstance(section, dict):
                raise SystemExit(f"REFUSE plugins.configs.{plugin} must be a mapping")
            if section.get("enabled"):
                section[field] = value

# Only the blocks this transaction owns may differ from the deployed file;
# everything else must round-trip untouched (v8 root sections).
allowed_changed_blocks = {
    "routing",
    "upstream",
    "observability",
    "oauth",
    "api-keys",
    "claude-header-defaults",
    "requests",
    "plugins",
}
before_unapproved = deepcopy(config_before)
after_unapproved = deepcopy(config_after)
for key in allowed_changed_blocks:
    before_unapproved.pop(key, None)
    after_unapproved.pop(key, None)
# Within server, only server.shutdown-timeout is owned by this transaction;
# server.host, server.port, server.tls, and remote-management must stay untouched.
for mapping in (before_unapproved, after_unapproved):
    server_block = mapping.get("server")
    if isinstance(server_block, dict):
        server_block.pop("shutdown-timeout", None)
        if not server_block:
            mapping.pop("server", None)
if before_unapproved != after_unapproved:
    raise SystemExit("config change exceeded the approved field/block set")

candidate = yaml.safe_dump(
    config_after,
    allow_unicode=True,
    default_flow_style=False,
    sort_keys=False,
)
# safe_dump rewrites the whole file; hand-written comments in config.yaml do
# not survive, so config content stays tool-owned (no hand edits).
parsed_candidate = yaml.safe_load(candidate)
if parsed_candidate != config_after:
    raise SystemExit("candidate YAML semantic round-trip failed")
atomic_write(config_path, candidate, 0o600)

# CN-lane protection (2026-11-20). Lower disabled to true on every
# workbuddyai-* auth file: the INTL plugin is closed dark, so those credentials
# cannot be routed, but the plugin's periodic credit sweep still reads them and
# a sweep over an exhausted balance answers a bare 429 (measured six times in
# the 24h window). Only the "disabled" key is set, in place, so JSON key order
# and byte layout stay intact; refresh material is never read or rewritten.
# Snapshotting is the backup step's job, not this one.
import glob

intl_changed = 0
intl_already = 0
for intl_path_str in sorted(glob.glob("/opt/cliproxyapi/auth/workbuddyai-*.json")):
    intl_path = Path(intl_path_str)
    intl_payload = json.loads(intl_path.read_text(encoding="utf-8"))
    if not isinstance(intl_payload, dict):
        raise SystemExit("REFUSE intl_auth_not_a_mapping name=" + intl_path.name)
    if intl_payload.get("disabled") is True:
        intl_already += 1
        continue
    intl_text = intl_path.read_text(encoding="utf-8")
    intl_new, intl_subs = re.subn(
        r'("disabled"\s*:\s*)(false|true|null|[0-9]+)',
        r"\g<1>true",
        intl_text,
        count=1,
    )
    if intl_subs == 0:
        # Absent key: insert it as the final member so every existing byte
        # keeps its position and the file stays valid JSON.
        intl_body = intl_text.rstrip()
        if not intl_body.endswith("}"):
            raise SystemExit("REFUSE intl_auth_not_an_object name=" + intl_path.name)
        intl_head = intl_body[:-1].rstrip()
        intl_separator = "" if intl_head.endswith("{") else ","
        intl_new = intl_head + intl_separator + '"disabled":true}'
        if intl_text.endswith("\n"):
            intl_new += "\n"
    intl_recheck = json.loads(intl_new)
    if not isinstance(intl_recheck, dict) or intl_recheck.get("disabled") is not True:
        raise SystemExit("REFUSE intl_auth_rewrite_unverified name=" + intl_path.name)
    atomic_write(intl_path, intl_new, 0o600)
    intl_changed += 1
print(
    "INTL_AUTH_DISABLED changed=%d already=%d"
    % (intl_changed, intl_already)
)

updater_path = Path("/opt/cliproxyapi/auto-update.sh")
updater = updater_path.read_text(encoding="utf-8")
old_key_line = (
    "KEY=$(sed -n '/^api-keys:/{n;s/.*\\\"\\([0-9a-f]*\\)\\\".*/\\1/p}' "
    '\"$DIR/config.yaml\")'
)
new_key_line = (
    'KEY=$(grep -A1 "^api-keys:" "$DIR/config.yaml" | '
    'tail -n 1 | sed -E "s/[^0-9a-f]//g")'
)
if old_key_line in updater:
    updater = updater.replace(old_key_line, new_key_line, 1)
elif new_key_line not in updater and not (
    'KEY=$(awk \'/^api-keys:/{getline; line=$0; '
    'gsub(/[^0-9a-f]/, "", line); print line; exit}\' '
    '"$DIR/config.yaml")' in updater
    or "key = yaml.safe_load(open(sys.argv[1]))['api-keys'][0]" in updater
    or 'health() { python3 "$DIR/cpa-health.py" "$1"; }' in updater
):
    raise SystemExit("updater key extraction anchor not found")
atomic_write(updater_path, updater, 0o700)

nginx_path = Path("/etc/nginx/conf.d/cpa-gateway.conf")
nginx = nginx_path.read_text(encoding="utf-8")
admission_proxy = "proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;"
legacy_proxy = "proxy_pass http://127.0.0.1:8317/v1/$1$is_args$args;"
if admission_proxy not in nginx:
    if legacy_proxy not in nginx:
        raise SystemExit("expected CPA public route proxy anchor missing")
    nginx = nginx.replace(legacy_proxy, admission_proxy, 1)
if nginx.count(admission_proxy) != 1:
    raise SystemExit("expected exactly one shared-account admission proxy route")
auth_proxy = "proxy_pass http://127.0.0.1:8317/v1/models$is_args$args;"
if nginx.count(auth_proxy) != 1:
    raise SystemExit("expected exactly one direct CPA auth proxy route")
new_limit_conn = "limit_conn cpa_cc 20;"
legacy_limit_conn = "limit_conn cpa_cc 6;"
previous_limit_conn = "limit_conn cpa_cc 12;"
if new_limit_conn not in nginx:
    if previous_limit_conn in nginx:
        nginx = nginx.replace(previous_limit_conn, new_limit_conn, 1)
    elif legacy_limit_conn not in nginx:
        raise SystemExit("expected Nginx per-IP connection budget missing")
    # Accept previously deployed budgets (6 or 12) as input states
    # as an input state; the required-anchor check below then validates the
    # migrated value before nginx -t runs.
    nginx = nginx.replace(legacy_limit_conn, new_limit_conn, 1)

new_body_buffer = "client_body_buffer_size 2m;"
if new_body_buffer not in nginx:
    for legacy_body_buffer in (
        "client_body_buffer_size 128k;",
        "client_body_buffer_size 1m;",
    ):
        if legacy_body_buffer in nginx:
            # Accept previously deployed request-body buffer sizes as input
            # states and migrate to 2m so 200K-320K context requests stay in
            # memory instead of spilling to /var/cache/nginx temporary files.
            nginx = nginx.replace(legacy_body_buffer, new_body_buffer, 1)
            break

required = [
    "limit_req_zone $binary_remote_addr zone=cpa_rl:1m rate=10r/s;",
    "limit_conn_zone $binary_remote_addr zone=cpa_cc:1m;",
    "limit_conn cpa_cc 20;",
    "client_max_body_size 32m;",
    "client_body_buffer_size 2m;",
    "proxy_buffering off;",
    "proxy_read_timeout 300s;",
    "proxy_send_timeout 300s;",
]
for anchor in required:
    if anchor not in nginx:
        raise SystemExit(f"expected Nginx guardrail missing: {anchor}")
new_limit_req = "limit_req zone=cpa_rl burst=10;"
legacy_limit_req = "limit_req zone=cpa_rl burst=20 nodelay;"
if new_limit_req not in nginx:
    if legacy_limit_req not in nginx:
        raise SystemExit("expected Nginx request limiter missing")
    # Accept the previously deployed limiter as an input state. The same
    # transaction below rewrites it to the queued burst policy before nginx -t.
    nginx = nginx.replace(legacy_limit_req, new_limit_req, 1)


def ensure_nginx_directive(text, directive, anchor):
    # nginx defaults both throttle statuses to 503, which re-conflates local
    # rate limiting with upstream overload and gives clients no back-off
    # signal. The 2026-09-09 hardening set them to 429, but no versioned anchor
    # asserted them, so a regeneration could silently drop them. Insert next to
    # the matching limiter when absent; never reorder an existing directive.
    if directive in text:
        return text
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if anchor in line:
            indent = line[: len(line) - len(line.lstrip())]
            lines.insert(index + 1, indent + directive + "\n")
            return "".join(lines)
    raise SystemExit("expected Nginx limiter anchor missing for " + directive)


nginx = ensure_nginx_directive(nginx, "limit_req_status 429;", new_limit_req)
nginx = ensure_nginx_directive(nginx, "limit_conn_status 429;", "limit_conn cpa_cc 20;")

# Local throttle rejections must carry an explicit back-off signal. nginx emits
# no Retry-After on its own 429, so a client that wants to behave cannot tell how
# long to wait and falls back to its own cadence - the ~1.0s-median retry storm
# the doctor observes. The header is added at server scope, which is safe here
# because the deployed file declares no other add_header (an inner add_header
# would replace the inherited set). The map is non-empty ONLY for requests the
# local limiters actually rejected, so upstream 429s keep passing through
# untouched and 200/401/404/5xx responses never grow a forged header. The value
# is a floor, not a prediction: with burst=10 at 10r/s a rejected slot refills
# within ~1s, and a concurrency rejection clears as soon as one request ends.
throttle_retry_after_map = (
    "map \"$limit_req_status:$limit_conn_status\" $cpa_throttle_retry_after {\n"
    "    default \"\";\n"
    "    \"~REJECTED\" 1;\n"
    "}\n"
)
if 'map "$limit_req_status:$limit_conn_status" $cpa_throttle_retry_after {' not in nginx:
    nginx = throttle_retry_after_map + nginx
elif throttle_retry_after_map not in nginx:
    raise SystemExit(
        "existing cpa_throttle_retry_after map differs from approved throttle map"
    )
nginx = ensure_nginx_directive(
    nginx,
    "add_header Retry-After $cpa_throttle_retry_after always;",
    "limit_conn_status 429;",
)

route_class_map = (
    "map $uri $cpa_route_class {\n"
    "    \"~^/[0-9a-f]{16}/v1/models$\" models;\n"
    "    \"~^/[0-9a-f]{16}/v1/chat/completions$\" chat;\n"
    "    \"~^/[0-9a-f]{16}/v1/responses$\" responses;\n"
    "    default other;\n"
    "}\n"
)
if "map $uri $cpa_route_class {" not in nginx:
    nginx = route_class_map + nginx
elif route_class_map not in nginx:
    raise SystemExit("existing cpa_route_class map differs from approved redacted map")

retry_after_map = (
    "map $upstream_http_retry_after $cpa_retry_after_class {\n"
    "    \"\" absent;\n"
    "    \"~^[0-9]{1,5}$\" seconds;\n"
    "    default other;\n"
    "}\n"
)
if "map $upstream_http_retry_after $cpa_retry_after_class {" not in nginx:
    nginx = retry_after_map + nginx
elif retry_after_map not in nginx:
    raise SystemExit("existing cpa_retry_after_class map differs from approved redacted map")

log_format = (
    "log_format cpa_safe '$remote_addr method=$request_method "
    "route=$cpa_route_class "
    "status=$status request_time=$request_time "
    "upstream_status=$upstream_status "
    "upstream_time=$upstream_response_time bytes=$body_bytes_sent "
    "limit_req=$limit_req_status limit_conn=$limit_conn_status "
    "retry_after=$cpa_retry_after_class "
    "time=[$time_local] auth_status=$cpa_auth_status "
    # Appended last on purpose: every parser in this file matches the fields
    # before it positionally or with a trailing `.*`, so a new field at the end
    # cannot shift `bytes=`/`limit_req=` out from under them.
    # $upstream_header_time is the only field that exposes the client-visible
    # time-to-first-byte of a stream; $upstream_response_time only reports the
    # completed response, which for SSE is the whole turn. Without it a
    # 10s-of-dead-air regression is indistinguishable from a slow generation.
    "upstream_header_time=$upstream_header_time';\n"
)
legacy_log_format = log_format.replace(" auth_status=$cpa_auth_status", "")
# The format projected before TTFB was observable. Kept as a migration source
# so -Apply can upgrade an already-deployed host instead of refusing on drift.
pre_ttfb_log_format = log_format.replace(
    " upstream_header_time=$upstream_header_time", ""
)
without_retry_log_format = log_format.replace(
    " retry_after=$cpa_retry_after_class", ""
)
previous_log_format = without_retry_log_format.replace(
    " limit_req=$limit_req_status limit_conn=$limit_conn_status", ""
)
previous_legacy_log_format = previous_log_format.replace(
    " auth_status=$cpa_auth_status", ""
)
# Older deployed logs did not include route classification. Accept them during
# the read/replace transition, but every new projected config must use the
# redacted route class field rather than the random public path.
legacy_route_log_format = without_retry_log_format.replace(" auth_status=$cpa_auth_status", "")
previous_route_log_format = legacy_route_log_format.replace(
    " route=$cpa_route_class", ""
)
previous_unclassified_log_format = without_retry_log_format.replace(
    " route=$cpa_route_class", ""
)
# Adding auth_status requires the separately verified auth_request location.
if 'auth_request /_cpa_auth;' not in nginx:
    log_format = legacy_log_format
if "log_format cpa_safe " not in nginx:
  nginx = log_format + nginx
elif log_format not in nginx:
    old_formats = [
        pre_ttfb_log_format,
        without_retry_log_format,
        legacy_route_log_format,
        previous_unclassified_log_format,
        previous_route_log_format,
        previous_legacy_log_format,
        previous_log_format,
    ] if 'auth_request /_cpa_auth;' not in nginx else [
        pre_ttfb_log_format,
        without_retry_log_format,
        legacy_route_log_format,
        previous_unclassified_log_format,
        previous_route_log_format,
        previous_log_format,
        previous_legacy_log_format,
    ]
    for old_format in old_formats:
        if old_format in nginx:
            nginx = nginx.replace(old_format, log_format, 1)
            break
    else:
        raise SystemExit("existing cpa_safe log format differs from approved redacted format")
nginx = nginx.replace(
    "access_log /var/log/nginx/cpa_gateway.access.log;",
    "access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;",
    1,
)
if "access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;" not in nginx:
    raise SystemExit("safe access log anchor not installed")
atomic_write(nginx_path, nginx, 0o644)

nginx_logrotate_path = Path("/etc/logrotate.d/nginx")
if not nginx_logrotate_path.exists():
    raise SystemExit("existing Nginx logrotate file is missing")
nginx_logrotate = nginx_logrotate_path.read_text(encoding="utf-8")
if "/var/log/nginx/*.log" not in nginx_logrotate:
    raise SystemExit("existing Nginx logrotate does not cover gateway logs")

print(
    "CONFIG_POLICY_READY "
    f"request_retry={config_after['routing']['retry']['request-retry']}"
)
print("CODEX_OAUTH_ROUTES_READY gpt-6-luna/gpt-6.1-sol/gpt-6.1-terra=allowed gpt-5.6-luna=retired gpt-6-astra=excluded retired=gpt-6.1-sol-input")
print(
    "CODEX_OAUTH_ALIASES_READY "
    + "/".join(
        f"{entry['name']}->{entry['alias']}"
        for entry in oauth_aliases_manifest.get("codex", [])
    )
)
print("ANTIGRAVITY_OAUTH_ROUTES_READY claude-only=allowed gemini=retired-and-excluded")
print(
    "ANTIGRAVITY_OAUTH_EXCLUSIONS_READY "
    + "/".join(sorted(oauth_exclusions["antigravity"]))
    + "=excluded"
)
PY
then
  restore_all
  echo "ROLLBACK config_or_updater"
  exit 1
fi

write_base64_file() {
  encoded=$1
  path=$2
  mode=$3
  expected_sha=$4
  temp=$(mktemp "${path}.XXXXXX")
  if ! printf '%s' "$encoded" | base64 -d >"$temp"; then
    rm -f "$temp"
    return 1
  fi
  chmod "$mode" "$temp" || { rm -f "$temp"; return 1; }
  if ! mv -f "$temp" "$path"; then
    rm -f "$temp"
    return 1
  fi
  # Write-then-verify: the projected bytes must equal the repo-side payload
  # exactly; a mismatch rolls back through the caller instead of surviving as
  # silent drift for a human to catch in a later sha listing.
  if [ -n "$expected_sha" ]; then
    written_sha=$(sha256sum "$path" | awk '{print $1}')
    if [ "$written_sha" != "$expected_sha" ]; then
      echo "PROJECTION_HASH_MISMATCH path=$path want=$expected_sha got=$written_sha"
      return 1
    fi
    echo "PROJECTION_HASH_VERIFIED path=$path"
  fi
}
write_base64_file "__CPA_FAIL2BAN_FILTER_B64__" "$FAIL2BAN_FILTER" 644 "__CPA_FAIL2BAN_FILTER_SHA256__" || {
  restore_all
  echo "ROLLBACK fail2ban_filter_write"
  exit 1
}
write_base64_file "__CPA_FAIL2BAN_JAIL_B64__" "$FAIL2BAN_JAIL" 644 "__CPA_FAIL2BAN_JAIL_SHA256__" || {
  restore_all
  echo "ROLLBACK fail2ban_jail_write"
  exit 1
}
write_base64_file "__CPA_UPDATER_B64__" "$DIR/auto-update.sh" 700 "__CPA_UPDATER_SHA256__" || {
  restore_all
  echo "ROLLBACK updater_projection"
  exit 1
}
write_base64_file "__CPA_HEALTH_B64__" "$DIR/cpa-health.py" 644 "__CPA_HEALTH_SHA256__" || {
  restore_all
  echo "ROLLBACK health_projection"
  exit 1
}
write_base64_file "__CPA_HEALTH_RUNTIME_B64__" "$DIR/cpa_health_runtime.py" 644 "__CPA_HEALTH_RUNTIME_SHA256__" || {
  restore_all
  echo "ROLLBACK health_runtime_projection"
  exit 1
}
write_base64_file "__CPA_PROVIDER_ROUTES_B64__" "$DIR/cpa_provider_routes.json" 644 "__CPA_PROVIDER_ROUTES_SHA256__" || {
  restore_all
  echo "ROLLBACK provider_route_projection"
  exit 1
}
write_base64_file "__CPA_POLICY_B64__" "$DIR/cpa_policy.py" 644 "__CPA_POLICY_SHA256__" || {
  restore_all
  echo "ROLLBACK policy_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_B64__" "$ADMISSION_SCRIPT" 755 "__CPA_ADMISSION_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_LAUNCHER_B64__" "$ADMISSION_LAUNCHER" 755 "__CPA_ADMISSION_LAUNCHER_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_launcher_projection"
  exit 1
}
mkdir -p /usr/local/libexec
write_base64_file "__CPA_ADMISSION_BOOTSTRAP_B64__" "$ADMISSION_BOOTSTRAP" 644 "__CPA_ADMISSION_BOOTSTRAP_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_bootstrap_projection"
  exit 1
}
write_base64_file "__CPA_REQUEST_GUARD_B64__" "$ADMISSION_REQUEST_GUARD" 644 "__CPA_REQUEST_GUARD_SHA256__" || {
  restore_all
  echo "ROLLBACK request_guard_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_CONFIG_B64__" "$ADMISSION_CONFIG" 644 "__CPA_ADMISSION_CONFIG_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_config_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_UNIT_B64__" "$ADMISSION_UNIT" 644 "__CPA_ADMISSION_UNIT_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_unit_projection"
  exit 1
}
mkdir -p "$(dirname "$ADMISSION_INTEGRITY_CHECK")" "$(dirname "$ADMISSION_INTEGRITY_DROPIN")" "$(dirname "$ADMISSION_INTEGRITY_PIN")"
write_base64_file "__CPA_ADMISSION_INTEGRITY_CHECK_B64__" "$ADMISSION_INTEGRITY_CHECK" 755 "__CPA_ADMISSION_INTEGRITY_CHECK_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_integrity_check_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_INTEGRITY_DROPIN_B64__" "$ADMISSION_INTEGRITY_DROPIN" 644 "__CPA_ADMISSION_INTEGRITY_DROPIN_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_integrity_dropin_projection"
  exit 1
}
write_base64_file "__CPA_ADMISSION_INTEGRITY_PIN_B64__" "$ADMISSION_INTEGRITY_PIN" 644 "__CPA_ADMISSION_INTEGRITY_PIN_SHA256__" || {
  restore_all
  echo "ROLLBACK admission_integrity_pin_projection"
  exit 1
}
# The updater's systemd budgets live in this drop-in; the main daemon-reload
# later in this transaction picks the new values up for the running timer.
mkdir -p "$(dirname "$UPDATER_TIMEOUT_DROPIN")"
write_base64_file "__CPA_UPDATER_TIMEOUT_CONF_B64__" "$UPDATER_TIMEOUT_DROPIN" 644 "__CPA_UPDATER_TIMEOUT_CONF_SHA256__" || {
  restore_all
  echo "ROLLBACK updater_timeout_projection"
  exit 1
}
# Slot split (2026-11-20): the updater starts at 04:00 UTC and the monthly and
# two kernel wrappers own 04:10/04:20/04:30, so the four background jobs stop
# racing for the one maintenance lock. Only Directory/Timer keys are rewritten;
# every other line of the unit stays where the host operator left it.
if [ -f "$UPDATER_TIMER_UNIT" ]; then
  python3 - "$UPDATER_TIMER_UNIT" '__UPDATER_TIMER_ONCALENDAR__' '__UPDATER_TIMER_RANDOM_DELAY__' <<'PY' || {
import sys
from pathlib import Path

path = Path(sys.argv[1])
target_oncalendar = sys.argv[2]
target_randomized = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
out = []
in_timer = False
seen_timer = False
seen_oncalendar = False
seen_randomized = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        in_timer = stripped == "[Timer]"
        seen_timer = seen_timer or in_timer
        out.append(line)
        continue
    if in_timer and stripped.startswith("OnCalendar="):
        if not seen_oncalendar:
            out.append("OnCalendar=" + target_oncalendar)
            seen_oncalendar = True
        continue
    if in_timer and stripped.startswith("RandomizedDelaySec="):
        if not seen_randomized:
            out.append("RandomizedDelaySec=" + target_randomized)
            seen_randomized = True
        continue
    out.append(line)
if not seen_timer:
    raise SystemExit("updater timer unit has no [Timer] section")
if not seen_oncalendar:
    out.append("OnCalendar=" + target_oncalendar)
if not seen_randomized:
    # Right after the schedule line, so the pair reads as one decision.
    for index, line in enumerate(out):
        if line.strip() == "OnCalendar=" + target_oncalendar:
            out.insert(index + 1, "RandomizedDelaySec=" + target_randomized)
            break
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
    restore_all
    echo "ROLLBACK updater_timer_slot_split"
    exit 1
  }
  # Read the projection back before anything activates it. 2026-11-20: the
  # first version of this block lost the leading '*' on OnCalendar, daemon-
  # reload accepted the file, and systemd quietly stopped scheduling the
  # updater ("Failed to parse calendar specification ... Timer unit lacks
  # value setting. Refusing."). A unit file that loads is not a schedule that
  # exists, so the exact schedule text is asserted here, and the loaded unit
  # is asked to echo the same value after the reload later in this transaction.
  UPDATER_TIMER_ONCALENDAR=$(awk -F= '/^OnCalendar=/ {print $2; exit}' "$UPDATER_TIMER_UNIT")
  UPDATER_TIMER_DELAY=$(awk -F= '/^RandomizedDelaySec=/ {print $2; exit}' "$UPDATER_TIMER_UNIT")
  if [ "$UPDATER_TIMER_ONCALENDAR" != '__UPDATER_TIMER_ONCALENDAR__' ] ||
     [ "$UPDATER_TIMER_DELAY" != '__UPDATER_TIMER_RANDOM_DELAY__' ]; then
    restore_all
    echo "ROLLBACK updater_timer_readback want=__UPDATER_TIMER_ONCALENDAR__/__UPDATER_TIMER_RANDOM_DELAY__ got=$UPDATER_TIMER_ONCALENDAR/$UPDATER_TIMER_DELAY"
    exit 1
  fi
  echo "UPDATER_TIMER_READBACK on_calendar=$UPDATER_TIMER_ONCALENDAR randomized_delay=$UPDATER_TIMER_DELAY"
fi
if ! "$ADMISSION_INTEGRITY_CHECK" >"$BK/cpa-admission-integrity.log" 2>&1; then
  restore_all
  echo "ROLLBACK admission_integrity_check"
  cat "$BK/cpa-admission-integrity.log"
  exit 1
fi
rm -f "$BK/cpa-admission-integrity.log"
if ! python3 -m py_compile "$ADMISSION_SCRIPT"; then
  restore_all
  echo "ROLLBACK admission_syntax"
  exit 1
fi
if ! python3 - "$ADMISSION_CONFIG" <<'PY'
import json
import sys
from pathlib import Path
config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if config.get("version") != 1 or config.get("listen_host") != "127.0.0.1":
    raise SystemExit(1)
if config.get("listen_port") != 8318 or config.get("upstream_port") != 8317:
    raise SystemExit(1)
if config.get("max_body_bytes") != 33554432 or config.get("probe_bytes") != 262144:
    raise SystemExit(1)
if config.get("stream_probe_bytes") != 2097152:
    raise SystemExit(1)
if config.get("retry_after_max_seconds") != 86400:
    raise SystemExit(1)
expected = {
    "chatgpt-oauth": [
        "gpt-6-luna",
        "gpt-6.1-sol",
        "gpt-6.1-terra",
        "gpt-6.2-sol",
    ],
    "zhipu-coding-plan": ["glm-5.3", "glm-5.3-flash"],
    "zcode-start-plan": ["glm-5.3-flash-start"],
    "antigravity-oauth": [
        "claude-opus-5-5-high",
        "claude-sonnet-5-5-high",
    ],
    "workbuddy-oauth": [
        "deepseek-v4.1-flash",
    ],
    "workbuddy-intl-oauth": [
        "deepseek-v4.1-flash-intl",
        "deepseek-v4.1-flash-sg",
    ],
}
expected_max_inflight = {
    "chatgpt-oauth": 2,
    "zhipu-coding-plan": 3,
    "zcode-start-plan": 1,
    "antigravity-oauth": 2,
    "workbuddy-oauth": 1,
    "workbuddy-intl-oauth": 1,
}
lanes = config.get("lanes")
if not isinstance(lanes, list) or len(lanes) != len(expected):
    raise SystemExit(1)
for lane in lanes:
    if not isinstance(lane, dict) or lane.get("name") not in expected:
        raise SystemExit(1)
    if lane.get("models") != expected[lane["name"]]:
        raise SystemExit(1)
    if (
        lane.get("max_inflight") != expected_max_inflight.get(lane.get("name"))
        or lane.get("max_pending") != 4
    ):
        raise SystemExit(1)
    if lane.get("queue_timeout_seconds") != 120:
        raise SystemExit(1)
    if lane.get("cooldown_schedule_seconds") != [60, 120, 240, 480, 900]:
        raise SystemExit(1)
    if lane.get("cooldown_cap_seconds") != 900:
        raise SystemExit(1)
    if lane.get("cooldown_cap_seconds") > config["retry_after_max_seconds"]:
        raise SystemExit(1)
    expected_rpm = {"chatgpt-oauth": 24, "zhipu-coding-plan": 30, "zcode-start-plan": 20,
                    "antigravity-oauth": 12, "workbuddy-oauth": 12,
                    "workbuddy-intl-oauth": 12}
    if lane.get("requests_per_minute") != expected_rpm.get(lane.get("name")):
        raise SystemExit(1)
    if not ({429, 503} <= set(lane.get("capacity_statuses", []))):
        raise SystemExit(1)
if {lane.get("name") for lane in lanes} != set(expected):
    raise SystemExit(1)
PY
then
  restore_all
  echo "ROLLBACK admission_config_contract"
  exit 1
fi
if ! python3 - "$RISK_STATE_DIR/workbuddy-intl-risk.json" <<'PY'
import json
import os
import pwd
import grp
import stat
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
directory = path.parent
if directory.is_symlink() or not directory.is_dir():
    raise SystemExit("workbuddy-intl risk state directory is not a directory")
owner = pwd.getpwnam("nobody").pw_uid
group = grp.getgrnam("nogroup").gr_gid
directory_stat = directory.stat()
if (
    directory_stat.st_uid != owner
    or directory_stat.st_gid != group
    or stat.S_IMODE(directory_stat.st_mode) != 0o700
):
    raise SystemExit("workbuddy-intl risk state directory permissions drift")

if path.exists() or path.is_symlink():
    if path.is_symlink() or not path.is_file():
        raise SystemExit("workbuddy-intl risk state is not a regular file")
    file_stat = path.stat()
    if (
        file_stat.st_uid != owner
        or file_stat.st_gid != group
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise SystemExit("workbuddy-intl risk state permissions drift")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit("workbuddy-intl risk state is invalid JSON") from None
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or type(value.get("not_before")) not in (int, float)
        or (
            "hold_reason" in value
            and value["hold_reason"] is not None
            and not isinstance(value["hold_reason"], str)
        )
    ):
        raise SystemExit("workbuddy-intl risk state schema drift")
    print("workbuddy-intl-risk-state=EXISTING")
else:
    payload = json.dumps(
        {"version": 1, "not_before": 0, "hold_reason": None},
        separators=(",", ":"),
    )
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(directory))
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, owner, group)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if fd != -1:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    print("workbuddy-intl-risk-state=INITIALIZED")
PY
then
  restore_all
  echo "ROLLBACK workbuddy-intl-risk_state"
  exit 1
fi
if ! grep -Fq 'ExecStart=/opt/cliproxyapi/cpa-admission-launch.sh' "$ADMISSION_UNIT" ||
   ! grep -Fq 'VPS_SSH_LAUNCHER_ADMISSION_TARGET=/opt/cliproxyapi/cpa-admission.py' "$ADMISSION_LAUNCHER" ||
   ! grep -Fq 'VPS_SSH_LAUNCHER_ADMISSION_CONFIG=/opt/cliproxyapi/cpa-admission.json' "$ADMISSION_LAUNCHER"; then
  restore_all
  echo "ROLLBACK admission_unit_contract"
  exit 1
fi
if ! python3 -m py_compile "$DIR/cpa-health.py"; then
  restore_all
  echo "ROLLBACK health_syntax"
  exit 1
fi
if ! python3 -m py_compile "$DIR/cpa_health_runtime.py"; then
  restore_all
  echo "ROLLBACK health_runtime_syntax"
  exit 1
fi
if grep -Eq '^[[:space:]]*listen[[:space:]]+\[::\]:8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_public_listener"
  exit 1
fi
if ! python3 -m py_compile "$DIR/cpa_policy.py"; then
  restore_all
  echo "ROLLBACK policy_syntax"
  exit 1
fi
if ! python3 "$DIR/cpa_policy.py" "$DIR/config.yaml" >"$BK/cpa-policy-test.log" 2>&1; then
  restore_all
  echo "ROLLBACK semantic_policy"
  tail -n 20 "$BK/cpa-policy-test.log"
  exit 1
fi
# Read the projection back through the same counter the doctor arms against:
# every INTL credential must be disabled once the transaction completes, not
# just "one more than before". The readback runs the payload's own module copy
# for the same reason the precondition does: the deployed $DIR/cpa_policy.py is
# still the previous generation on this first rollout and answers nothing.
INTL_COUNTER_DIR=$(mktemp -d /tmp/cpa-intl-verify.XXXXXXXXXX)
python3 - "$INTL_COUNTER_DIR" <<'PY' || echo "INTL_VERIFY_COUNTER_UNAVAILABLE"
import base64
import sys
from pathlib import Path
Path(sys.argv[1], "cpa_policy.py").write_bytes(base64.b64decode("__CPA_POLICY_B64__"))
PY
INTL_AFTER_DISABLED=$(python3 "$INTL_COUNTER_DIR/cpa_policy.py" count-intl-disabled "$DIR/auth" 2>/dev/null || true)
rm -rf -- "$INTL_COUNTER_DIR"
INTL_AUTH_TOTAL_AFTER=$(find "$DIR/auth" -maxdepth 1 -type f -name 'workbuddyai-*.json' 2>/dev/null | wc -l)
if [ "$INTL_AFTER_DISABLED" != "$INTL_AUTH_TOTAL_AFTER" ]; then
  restore_all
  echo "ROLLBACK intl_auth_disabled_readback want=$INTL_AUTH_TOTAL_AFTER got=$INTL_AFTER_DISABLED"
  exit 1
fi
echo "INTL_AUTH_READBACK disabled=$INTL_AFTER_DISABLED of=$INTL_AUTH_TOTAL_AFTER"

chmod 600 "$DIR/config.yaml"
chmod 700 "$DIR/auto-update.sh"
if [ -d "$DIR/auth/logs" ]; then
  chmod 700 "$DIR/auth/logs"
  find "$DIR/auth/logs" -maxdepth 1 -type f -name 'error-*.log' -exec chmod 600 -- {} +
fi

if ! bash -n "$DIR/auto-update.sh"; then
  restore_all
  echo "ROLLBACK updater_syntax"
  exit 1
fi
if ! docker compose -f "$DIR/compose.yml" config --quiet; then
  restore_all
  echo "ROLLBACK compose_config"
  exit 1
fi
if ! grep -Fq 'umask 077' "$DIR/compose.yml" ||
   ! grep -Fq 'exec ./CLIProxyAPI' "$DIR/compose.yml"; then
  restore_all
  echo "ROLLBACK compose_umask"
  exit 1
fi
if ! assert_merged_nginx_route_contract; then
  restore_all
  echo "ROLLBACK merged_nginx_route_contract"
  exit 1
fi
if ! grep -Fq 'access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK safe_access_log"
  exit 1
fi
if ! grep -Fq 'map $uri $cpa_route_class {' "$NGINX_CONF" ||
   ! grep -Fq 'route=$cpa_route_class' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK redacted_route_class_observability"
  exit 1
fi
for anchor in \
  'client_max_body_size 32m;' \
  'client_body_buffer_size 2m;' \
  'proxy_buffering off;' \
  'proxy_read_timeout 300s;' \
  'proxy_send_timeout 300s;'; do
  if ! grep -Fq "$anchor" "$NGINX_CONF"; then
    restore_all
    echo "ROLLBACK gateway_transport_contract anchor=$anchor"
    exit 1
  fi
done
for anchor in \
  'limit_req zone=cpa_rl burst=10;' \
  'limit_req_status 429;' \
  'limit_conn_status 429;' \
  'add_header Retry-After $cpa_throttle_retry_after always;' \
  'map "$limit_req_status:$limit_conn_status" $cpa_throttle_retry_after {'; do
  if ! grep -Fq "$anchor" "$NGINX_CONF"; then
    restore_all
    echo "ROLLBACK gateway_throttle_contract anchor=$anchor"
    exit 1
  fi
done
for anchor in \
  'proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;' \
  'proxy_pass http://127.0.0.1:8317/v1/models$is_args$args;'; do
  if ! grep -Fq "$anchor" "$NGINX_CONF"; then
    restore_all
    echo "ROLLBACK admission_route_contract anchor=$anchor"
    exit 1
  fi
done
if ! fail2ban-client -t >/dev/null 2>&1; then
  restore_all
  echo "ROLLBACK fail2ban_syntax"
  exit 1
fi
if ! migrate_legacy_admission; then
  restore_all
  echo "ROLLBACK legacy_admission_migration"
  exit 1
fi

# Drain contract before the two restarts below: both cut in-flight streams,
# and admission write-ahead latches an unverified_request hold when its own
# upstream request ends unresolved (the 2026-10-24 04:03Z unlocked restart
# latched one and cost a 14-minute 403 window). Hold requests.lock
# exclusively so admission refuses new business requests while in-flight
# streams finish, then assert idle before restarting.
exec 8< /run/cpa-admission/requests.lock || {
  restore_all
  echo "ROLLBACK admission_drain_lock_file"
  exit 1
}
if ! flock -w 60 -x 8; then
  restore_all
  echo "ROLLBACK admission_drain_timeout"
  exit 1
fi
sleep 1
if ! python3 - <<'PY_IDLE'
import json
import urllib.request

with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health['status'] == 'ok'
assert all(
    not lane['state']['inflight'] and not lane['state']['pending']
    for lane in health['lanes'].values()
), 'active traffic survived the exclusive drain lock'
PY_IDLE
then
  restore_all
  echo "ROLLBACK admission_drain_idle_assert"
  exit 1
fi

if ! docker restart cli-proxy-api >/dev/null; then
  restore_all
  echo "ROLLBACK cpa_restart"
  exit 1
fi

READY=000
for _ in $(seq 1 30); do
  READY=$(curl --noproxy '*' -sS --max-time 5 -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" \
    http://127.0.0.1:8317/v1/models || true)
  if [ "$READY" = "200" ]; then
    break
  fi
  sleep 1
done
if [ "$READY" != "200" ]; then
  restore_all
  echo "ROLLBACK cpa_readiness status=$READY"
  exit 1
fi
# The readiness battery probes 8318 while the just-restarted container and
# admission are still binding; a single curl inside that window sees a
# connection reset and misreads a healthy deploy as a contract failure
# (2026-11-11 rollback-on-restart-race). Retry bounded before reverting.
HEALTH_OK=0
for _ in $(seq 1 12); do
  if python3 "$DIR/cpa-health.py" readiness; then
    HEALTH_OK=1
    break
  fi
  sleep 5
done
if [ "$HEALTH_OK" != "1" ]; then
  restore_all
  echo "ROLLBACK cpa_model_catalog_contract"
  exit 1
fi
if ! systemctl daemon-reload >"$BK/cpa-admission-daemon-reload.log" 2>&1; then
  restore_all
  echo "ROLLBACK admission_daemon_reload"
  tail -n 5 "$BK/cpa-admission-daemon-reload.log"
  exit 1
fi
# A daemon-reload that succeeds proves only that the filesystem projection
# parsed. TimersCalendar and RandomizedDelayUSec are the values the running
# timer actually uses; a drop-in that shadows our unit or an unloaded old
# generation must fail this transaction rather than appear green in its file.
if [ -f "$UPDATER_TIMER_UNIT" ]; then
  UPDATER_TIMER_CALENDAR=$(systemctl show "$UPDATER_TIMER_UNIT" -p TimersCalendar --value 2>/dev/null || true)
  if [ -z "$UPDATER_TIMER_CALENDAR" ]; then
    UPDATER_TIMER_CALENDAR=$(systemctl show "$(basename "$UPDATER_TIMER_UNIT")" -p TimersCalendar --value 2>/dev/null || true)
  fi
  UPDATER_TIMER_DELAY_USEC=$(systemctl show "$UPDATER_TIMER_UNIT" -p RandomizedDelayUSec --value 2>/dev/null || true)
  if [ -z "$UPDATER_TIMER_DELAY_USEC" ]; then
    UPDATER_TIMER_DELAY_USEC=$(systemctl show "$(basename "$UPDATER_TIMER_UNIT")" -p RandomizedDelayUSec --value 2>/dev/null || true)
  fi
  case "$UPDATER_TIMER_DELAY_USEC" in
    __UPDATER_TIMER_RANDOM_DELAY_USEC__|__UPDATER_TIMER_RANDOM_DELAY_SHORT__)
      ;;
    *)
      restore_all
      echo "ROLLBACK updater_timer_effective_readback delay=$UPDATER_TIMER_DELAY_USEC"
      exit 1
      ;;
  esac
  case "$UPDATER_TIMER_CALENDAR" in
    *"OnCalendar=__UPDATER_TIMER_ONCALENDAR__"*)
      ;;
    *)
      restore_all
      echo "ROLLBACK updater_timer_effective_readback calendar=$UPDATER_TIMER_CALENDAR"
      exit 1
      ;;
  esac
  UPDATER_TIMEOUT_START=$(systemctl show cliproxyapi-update.service -p TimeoutStartUSec --value 2>/dev/null || true)
  case "$UPDATER_TIMEOUT_START" in
    __UPDATER_TIMEOUT_START_USEC__|__UPDATER_TIMEOUT_START_SHORT__)
      ;;
    *)
      restore_all
      echo "ROLLBACK updater_timeout_effective_readback timeout_start=$UPDATER_TIMEOUT_START"
      exit 1
      ;;
  esac
echo "UPDATER_TIMER_EFFECTIVE on_calendar=0400 randomized_delay_usec=$UPDATER_TIMER_DELAY_USEC"
echo "UPDATER_TIMEOUT_EFFECTIVE timeout_start=$UPDATER_TIMEOUT_START"
  if ! systemctl start cliproxyapi-update.timer >"$BK/cpa-updater-timer-start.log" 2>&1; then
    restore_all
    echo "ROLLBACK updater_timer_start"
    exit 1
  fi
  echo "UPDATER_TIMER_SLOT=04:00 UTC jitter=__UPDATER_TIMER_RANDOM_DELAY__sec"
fi
if ! systemctl enable cpa-admission.service >"$BK/cpa-admission-enable.log" 2>&1; then
  restore_all
  echo "ROLLBACK admission_enable"
  tail -n 5 "$BK/cpa-admission-enable.log"
  exit 1
fi
# restart, not enable --now: on an already enabled+active service
# "enable --now" is a no-op, so a re-apply would leave the previous
# generation's process running the old projected code.
if ! systemctl restart cpa-admission.service >"$BK/cpa-admission-start.log" 2>&1; then
  restore_all
  echo "ROLLBACK admission_restart"
  tail -n 5 "$BK/cpa-admission-start.log"
  exit 1
fi
ADMISSION_READY=000
for _ in $(seq 1 15); do
  ADMISSION_READY=$(curl --noproxy '*' -sS --max-time 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8318/healthz || true)
  if [ "$ADMISSION_READY" = "200" ]; then
    break
  fi
  sleep 1
done
if [ "$ADMISSION_READY" != "200" ]; then
  restore_all
  echo "ROLLBACK admission_health status=$ADMISSION_READY"
  exit 1
fi
# Release the drain lock before the authenticated public probe: while it is
# held admission answers new business requests with maintenance_in_progress,
# so the probe would read as a gateway failure and force a spurious
# rollback.
exec 8>&-
SERVER_NAME=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' "$NGINX_CONF")
# || true keeps the named ROLLBACK public_route_inputs reachable: the EXIT
# trap would still restore, but the reason would be lost to a silent death.
PREFIX=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2 || true)
if [ -z "$SERVER_NAME" ] || [ -z "$PREFIX" ]; then
  restore_all
  echo "ROLLBACK public_route_inputs"
  exit 1
fi
PUBLIC_READY=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
  --resolve "$SERVER_NAME:8443:127.0.0.1" -H "Authorization: Bearer $KEY" \
  -o /dev/null -w '%{http_code}' "https://$SERVER_NAME:8443/$PREFIX/v1/models" 2>/dev/null || echo 000)
if [ "$PUBLIC_READY" != "200" ]; then
  restore_all
  echo "ROLLBACK public_authenticated_probe status=$PUBLIC_READY"
  exit 1
fi

if ! nginx -t >"$BK/cpa-nginx-test.log" 2>&1; then
  restore_all
  echo "ROLLBACK nginx_syntax"
  tail -n 5 "$BK/cpa-nginx-test.log"
  exit 1
fi
if ! assert_admission_nginx_route_contract; then
  restore_all
  echo "ROLLBACK admission_route_contract_merged"
  exit 1
fi
if ! grep -Eq '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK nginx_public_listener_changed"
  exit 1
fi
RANDOM_PATH_AFTER=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2 || true)
if [ "$RANDOM_PATH_AFTER" != "$RANDOM_PATH_BEFORE" ]; then
  restore_all
  echo "ROLLBACK nginx_random_path_changed"
  exit 1
fi
if ! grep -Eq 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK nginx_route_shape_changed"
  exit 1
fi
if ! systemctl reload nginx >"$BK/cpa-nginx-reload.log" 2>&1; then
  restore_all
  echo "ROLLBACK nginx_reload"
  tail -n 5 "$BK/cpa-nginx-reload.log"
  exit 1
fi
if ! fail2ban-client reload --restart cpa-gateway >"$BK/cpa-fail2ban-reload.log" 2>&1; then
  restore_all
  echo "ROLLBACK fail2ban_reload"
  tail -n 5 "$BK/cpa-fail2ban-reload.log"
  exit 1
fi
if ! assert_merged_nginx_route_contract; then
  restore_all
  echo "ROLLBACK merged_nginx_route_contract_after_reload"
  exit 1
fi
if ! assert_admission_nginx_route_contract; then
  restore_all
  echo "ROLLBACK admission_route_contract_after_reload"
  exit 1
fi
if ! assert_public_route_contract; then
  restore_all
  echo "ROLLBACK public_route_contract_after_reload"
  exit 1
fi
if ! ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]'; then
  restore_all
  echo "ROLLBACK nginx_public_listener_missing"
  exit 1
fi
if ss -ltn | grep -Eq '\[::\]:8443[[:space:]]|:::8443[[:space:]]'; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_public_listener_runtime"
  exit 1
fi
if ! ss -ltn | grep -Eq '127\.0\.0\.1:8317[[:space:]]'; then
  restore_all
  echo "ROLLBACK cpa_loopback_listener_missing"
  exit 1
fi
if ss -ltn | grep -Eq '\[::\]:8317[[:space:]]|:::8317[[:space:]]'; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_cpa_listener_runtime"
  exit 1
fi
if ! ss -ltn | grep -Eq '127\.0\.0\.1:8318[[:space:]]'; then
  restore_all
  echo "ROLLBACK admission_listener_missing"
  exit 1
fi
if ss -ltn | grep -Eq '\[::\]:8318[[:space:]]|:::8318[[:space:]]'; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_admission_listener_runtime"
  exit 1
fi
if ! systemctl is-enabled --quiet cpa-admission.service ||
   ! systemctl is-active --quiet cpa-admission.service; then
  restore_all
  echo "ROLLBACK admission_service_not_active"
  exit 1
fi
if systemctl is-active --quiet cpa-luna-admission.service 2>/dev/null ||
   systemctl is-enabled --quiet cpa-luna-admission.service 2>/dev/null ||
   [ -e "$LEGACY_ADMISSION_UNIT" ] ||
   [ -e "$LEGACY_ADMISSION_SCRIPT" ] ||
   [ -e "$LEGACY_ADMISSION_CONFIG" ]; then
  restore_all
  echo "ROLLBACK legacy_admission_residue"
  exit 1
fi

if ! logrotate -d /etc/logrotate.conf >"$BK/cpa-logrotate-test.log" 2>&1; then
  restore_all
  echo "ROLLBACK logrotate_syntax"
  tail -n 5 "$BK/cpa-logrotate-test.log"
  exit 1
fi

# The transaction is committed: disarm every handler before reporting.
MUTATED=0
trap - EXIT HUP INT TERM
echo "BACKUP_DIR=$BK"
echo "READY_STATUS=$READY"
sha256sum "$DIR/config.yaml" "$DIR/auto-update.sh" "$DIR/cpa-health.py" "$DIR/cpa_health_runtime.py" "$DIR/cpa_policy.py" "$DIR/cpa_provider_routes.json" "$DIR/cpa-admission.py" "$DIR/cpa-admission.json" "$ADMISSION_UNIT" "$NGINX_CONF" "$FAIL2BAN_FILTER" "$FAIL2BAN_JAIL" || \
  echo "WARNING checksum_summary_failed"
echo "==catalog_summary=="
if ! curl --noproxy '*' -sS --max-time 20 -H "Authorization: Bearer $KEY" \
  http://127.0.0.1:8317/v1/models |
  python3 -c '
import json, sys
d = json.load(sys.stdin)
ids = [item.get("id", "") for item in d.get("data", [])]
print("models=" + str(len(ids)))
print("has_deepseek=" + str(any(i.startswith("deepseek-") for i in ids)))
print("has_r1=" + str(any(i.startswith("r1/") for i in ids)))
print("has_retired_bare_luna=" + str("gpt-5.6-luna" not in ids))
print("has_bare_gpt6_luna=" + str("gpt-6-luna" in ids))
print("has_retired_gpt_6_1_sol_input=" + str("gpt-6.1-sol-input" in ids))
print("has_ai_input_im_bare_astra=" + str("gpt-6-astra" in ids))
print("has_retired_ai_input_im_bare_gpt56_sol=" + str("gpt-5.6-sol" in ids))
print("has_retired_slot1_gpt6_sol_input=" + str("gpt-6-sol-input" in ids))
print("has_ai_input_im_bare_deepseek_v41_flash=" + str("deepseek-v4.1-flash" in ids))
print("has_retired_slot1_image_gpt_2_5=" + str("gpt-image-2.5" in ids))
print("has_ciii_gpt6_astra=" + str("gpt-6-astra-ciii" in ids))
print("has_ciii_gpt61_sol=" + str("gpt-6.1-sol-ciii" in ids))
print("has_retired_ciii_gpt6_astra_cii=" + str("gpt-6-astra-cii" in ids))
print("has_retired_ciii_gpt6_sol=" + str("gpt-6-sol-cii" in ids))
print("has_slot3_gpt61_sol_91=" + str("gpt-6.1-sol-91" in ids))
print("has_slot3_gpt56_terra=" + str("gpt-5.6-terra" in ids))
print("has_previous_gpt56_sol_terra_aliases=" + str(bool({"gpt-5.6-sol-91", "gpt-5.6-terra-91"} & set(ids))))
print("has_retired_deepseek_v4_pro=" + str("deepseek-v4-pro" in ids))
print("has_ciii_retired_models=" + str(bool({"codex-auto-review", "gpt-5.5", "gpt-5.6"} & set(ids))))
print("has_retired_gpt_reserve=" + str("gpt-reserve" in ids))
print("has_glm_5_3=" + str("glm-5.3" in ids))
print("has_glm_5_3_flash=" + str("glm-5.3-flash" in ids))
print("has_retired_glm_5_3_flashx=" + str("glm-5.3-flashx" in ids))
print("has_retired_antigravity_lane_models=" + str(bool({
    "gemini-3-flash",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash-high",
    "gemini-4-argon-high",
    "gemini-4-hydrogen-xhigh",
    "gemini-4-neon-xhigh",
    "claude-opus-4-6-thinking",
    "claude-sonnet-4-6",
    "gpt-oss-120b-medium",
} & set(ids))))
'; then
  echo "WARNING catalog_summary_failed"
fi
# Prune first, then emit the success marker as the ABSOLUTE last line: the local
# marker protocol (maintenance/adapters.py) matches the final stdout line
# exactly, and this file used to print the marker before PRUNE/PRUNE_FAILED, so
# a strict reader would never see it.
prune_backup_history
echo "GUARDRAILS_APPLIED"

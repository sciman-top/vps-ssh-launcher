#!/bin/sh
# Read-only updater-schedule gate. The daily CPA auto-update timer runs at
# 04:00 UTC while the host also has scheduled maintenance in 04:0x-04:3x UTC.
# All of those transactions share /run/vps-ssh-launcher-maintenance.lock, and
# the updater takes it with `flock -n` (no waiting), so its scheduled start
# decides whether a day's update happens at all. Measured on bwg
# 2026-11-19T04:13:29Z: "DEFER: no-update readiness failed; image unchanged",
# over a v8.0.78 release that had reached its 72h soak window that morning.
# The slot split below gives each background job its own half-hour, so the
# schedules stop racing instead of the lock deciding the winner.
#
# Two modes:
#   (default)  verify the projected slot split; exit 1 when an anchor is off.
#   --status   read-only readout only; always exits 0.
#
# Dependencies without docker are not a gate: a missing `docker compose` call
# here only means the readiness probe is unknown, never that it passed.
set -eu

DIR="${CPA_DIR:-/opt/cliproxyapi}"
TIMER=cliproxyapi-update.timer
MODE=verify
if [ "${1:-}" = "--status" ]; then
  MODE=status
fi

fail=0
flag() {
  # $1 result, $2 label -> prints label=result, records a verification failure
  if [ "$1" = OK ]; then
    echo "$2=OK"
  elif [ "$MODE" = status ]; then
    echo "$2=UNVERIFIED reason=$1"
  else
    fail=$((fail + 1))
    echo "$2=FAIL reason=$1"
  fi
}

# systemd renders a USec property as raw microseconds on some builds and as a
# duration ("20min", "1h 30min") on others. Normalise both to seconds so the
# thresholds below compare numbers, never strings.
normalize_seconds() {
  local total=0 token suffix value matched
  for token in $1; do
    case "$token" in
      ''|infinity|*infinity) printf '\n'; return 0 ;;
    esac
    matched=0
    for suffix in ms min h s; do
      case "$token" in
        *"$suffix")
          value=${token%"$suffix"}
          case "$value" in
            ''|*[!0-9]*) printf '\n'; return 0 ;;
          esac
          case "$suffix" in
            ms) total=$((total + value / 1000)) ;;
            min) total=$((total + value * 60)) ;;
            h) total=$((total + value * 3600)) ;;
            s) total=$((total + value)) ;;
          esac
          matched=1
          break
          ;;
      esac
    done
    if [ "$matched" -eq 0 ]; then
      case "$token" in
        *[!0-9]*) printf '\n'; return 0 ;;
        # A bare number is systemd's raw microsecond form.
        *) total=$((total + token / 1000000)) ;;
      esac
    fi
  done
  printf '%s\n' "$total"
}

budget_seconds() {
  local key="$1" token value
  for token in ${AUTO_UPDATE_BUDGETS:-}; do
    case "$token" in
      "$key"=*)
        value=${token#*=}
        case "$value" in
          ''|*[!0-9]*) return 1 ;;
        esac
        printf '%s\n' "$value"
        return 0
        ;;
    esac
  done
  return 1
}

budget_declaration_exact() {
  local token seen=" "
  # Reject unknown, missing and duplicate keys, not merely the six values the
  # schedule currently reads. An added retry leg must be reviewed before the
  # budget can claim exact coverage.
  for token in ${AUTO_UPDATE_BUDGETS:-}; do
    case "$token" in
      pull_seconds=*|compose_seconds=*|start_stop_seconds=*|\
      restore_seconds=*|docker_kill_after_seconds=*|health_request_seconds=*)
        ;;
      *) return 1 ;;
    esac
    case "$seen" in
      *" ${token%%=*} "*) return 1 ;;
    esac
    seen="$seen${token%%=*} "
  done
  for token in pull_seconds compose_seconds start_stop_seconds restore_seconds \
    docker_kill_after_seconds health_request_seconds; do
    case "$seen" in
      *" $token "*) ;;
      *) return 1 ;;
    esac
  done
}

budgets_every() {
  local key value
  local -a seen=''
  for key in pull_seconds compose_seconds start_stop_seconds restore_seconds \
    docker_kill_after_seconds health_request_seconds; do
    value="$(budget_seconds "$key" || true)"
    case "$value" in
      ''|*[!0-9]*) return 1 ;;
    esac
    for seen_value in "${seen[@]}"; do
      [[ "$seen_value" == "$key" ]] && return 1
    done
    seen+=("$key")
  done
}

timer_block="$(systemctl cat "$TIMER" 2>/dev/null || true)"
# The guardrail projection replaces this line from the updater source so the
# timing check cannot silently use a stale copy of the budgets.
__CPA_UPDATER_BUDGETS__
if [ -z "$timer_block" ]; then
  # A host without the timer is a different fact from a wrong schedule, and
  # --status must never fail: it is the read-only description.
  if [ "$MODE" = status ]; then
    echo "updater-schedule=ABSENT unit=$TIMER"
    exit 0
  fi
  fail=$((fail + 1))
  echo "updater-schedule=ABSENT unit=$TIMER"
  exit 1
fi

# Read the loaded unit through systemd's own properties, not by parsing the
# file: a drop-in or a hand edit that never reached daemon-reload shows the
# unit file as projected while the running timer keeps the old schedule.
calendar="$(systemctl show "$TIMER" -p TimersCalendar --value 2>/dev/null || true)"
if [ -z "$calendar" ]; then
  # Older systemd without TimersCalendar: fall back to the unit file text.
  calendar="$(printf '%s\n' "$timer_block" | sed -n 's/^OnCalendar=//p' | tr -d '\r' | paste -sd ',' -)"
fi
# TimersCalendar renders as "{ OnCalendar=... ; next_elapse=... }" pairs.
calendar="$(printf '%s' "$calendar" | grep -oE 'OnCalendar=[^;}]*' | sed 's/^OnCalendar=//; s/[[:space:]]*$//; s/^ *//' | paste -sd ',' -)"
# RandomizedDelayUSec is a duration string on this systemd ("30min"), so it
# goes through the same normaliser as the start budget rather than assuming a
# numeric value. Bind-mounts or older builds may still emit raw microseconds,
# which normalize_seconds already accepts.
random_delay_raw="$(systemctl show "$TIMER" -p RandomizedDelayUSec --value 2>/dev/null || true)"
random_delay="$(normalize_seconds "$random_delay_raw")"
echo "updater-schedule-on-calendar=${calendar:-ABSENT}"
echo "updater-schedule-randomized-delay=${random_delay:+${random_delay}s}"

# Anchors, each named for the transaction it protects:
#   04:00 UTC  the updater's own single slot of the day.
#   04:10 UTC  the monthly maintenance wrapper (system_maintenance_cron.ps1).
#   04:20 UTC  the xray kernel wrapper (vasma_kernel_update_cron.ps1 -Kernel xray).
#   04:30 UTC  the sing-box kernel wrapper (same script, -Kernel sing-box).
# Daily/weekly/monthly arming is per-wrapper and varies by host, so this script
# keeps the anchors exact rather than inferring them from another cron file.
for entry in $(printf '%s' "$calendar" | tr ',' ' '); do
  case "$entry" in
    '*-*-* 04:00:00'|'*-*-* 04:00'|'*-*-* 04:00:00 UTC'|'*-*-* 04:00 UTC') :
      echo "updater-schedule-anchor=updater-0400"
      ;;
    '*-*-* 04:10:00'|'*-*-* 04:10'|'*-*-* 04:10:00 UTC'|'*-*-* 04:10 UTC') :
      echo "updater-schedule-anchor=monthly-0410"
      ;;
    '*-*-* 04:20:00'|'*-*-* 04:20'|'*-*-* 04:20:00 UTC'|'*-*-* 04:20 UTC') :
      echo "updater-schedule-anchor=kernel-xray-0420"
      ;;
    '*-*-* 04:30:00'|'*-*-* 04:30'|'*-*-* 04:30:00 UTC'|'*-*-* 04:30 UTC') :
      echo "updater-schedule-anchor=kernel-singbox-0430"
      ;;
  esac
done
anchor_count=$(printf '%s' "$calendar" | tr ',' '\n' | grep -cE '^\*-\*-\* 04:(00|10|20|30)(:00)?( UTC)?$' || true)
if [ "$anchor_count" -eq 1 ]; then
  flag OK updater-schedule-slot
else
  flag "anchor_count=$anchor_count" updater-schedule-slot
fi

# The old projection spread the updater across 04:00-04:30, which is exactly
# the window the other three transactions own. A randomized delay above 300s
# cannot stay inside its own slot.
case "$random_delay" in
  ''|*[!0-9]*) flag "randomized_delay=$random_delay" updater-schedule-jitter ;;
  *)
    if [ "$random_delay" -le 300 ]; then
      flag OK updater-schedule-jitter
    else
      flag "randomized_delay=$random_delay" updater-schedule-jitter
    fi
    ;;
esac

# Each work leg and Docker kill grace is independently bounded at its call
# site. Their full cold-path total must still fit comfortably inside the
# systemd overall fail-stop. In particular, the 600s pull, 180s Compose client
# and the possible 30s+compose wait before the second 180s Compose step are
# not interchangeable just because neither leg exceeds the 40min ceiling.
# `-Apply` projects an explicit per-unit TimeoutStartSec. Read both that value
# and the manager default, so neither can silently cap a longer raw setting.
pull_budget="$(budget_seconds pull_seconds || true)"
compose_budget="$(budget_seconds compose_seconds || true)"
start_stop_budget="$(budget_seconds start_stop_seconds || true)"
restore_budget="$(budget_seconds restore_seconds || true)"
kill_budget="$(budget_seconds docker_kill_after_seconds || true)"
health_budget="$(budget_seconds health_request_seconds || true)"
largest_budget=0
recovery_reserve=600
budget_valid=1
for budget_value in "$pull_budget" "$compose_budget" "$start_stop_budget" \
  "$restore_budget" "$kill_budget" "$health_budget"; do
  case "$budget_value" in
    ''|*[!0-9]*) budget_valid=0; break ;;
  esac
  if [ "$budget_valid" -eq 1 ] && [ "$budget_value" -gt "$largest_budget" ]; then
    largest_budget="$budget_value"
  fi
done
full_path_budget=''
if [ "$budget_valid" -eq 1 ]; then
  full_path_budget=$((pull_budget + 2 * compose_budget + start_stop_budget + \
    restore_budget + kill_budget + health_budget))
fi
budget_declaration=incomplete
if budgets_every >/dev/null 2>&1 && budget_declaration_exact; then
  budget_declaration=exact
fi
expected_start_seconds=2400
echo "updater-schedule-budget-declared=$budget_declaration"
if [ "$budget_valid" -eq 1 ]; then
  echo "updater-schedule-budget-largest=${largest_budget}s"
else
  echo "updater-schedule-budget-largest=UNVERIFIED"
fi
if [ "$budget_valid" -eq 1 ]; then
  echo "updater-schedule-budget-full-path=${full_path_budget}s"
else
  echo "updater-schedule-budget-full-path=UNVERIFIED"
fi
echo "updater-schedule-budget-expected=${expected_start_seconds}s"

start_property="$(systemctl show -p TimeoutStartUSec --value cliproxyapi-update.service 2>/dev/null || true)"
effective_start_raw="$(systemctl show cliproxyapi-update.service -p EffectiveTimeoutStartUSec --value 2>/dev/null || true)"
effective_property="$(systemctl show -p EffectiveTimeoutStartUSec --value cliproxyapi-update.service 2>/dev/null || true)"
systemd_default_raw="$(systemctl show -p DefaultTimeoutStartUSec --value 2>/dev/null || true)"
property_seconds="$(normalize_seconds "$start_property")"
existing_default_seconds="$(normalize_seconds "$systemd_default_raw")"
echo "updater-schedule-timeout-raw=${start_property:-ABSENT}/${systemd_default_raw:-ABSENT}"
# EffectiveTimeoutStartUSec includes this service's drop-in and manager default.
# Some systemd builds return it under the explicit-property query but leave it
# out of `systemctl show`; the different probes must agree when both work.
# Prefer Effective, which includes both a drop-in and the manager default. A
# systemd build that omits it from one query is covered by the other query.
[ -n "$effective_start_raw" ] || effective_start_raw="$effective_property"
# systemd documents a per-unit explicit duration as the effective ceiling even
# when it exceeds DefaultTimeoutStartSec: the manager default applies only when
# the service leaves TimeoutStartSec unset. Keep that precedence explicit.
if [ -n "$start_property" ] && [ "$start_property" != invalid ]; then
  effective_start_raw="$start_property"
fi
start_seconds=''
case "$effective_start_raw" in
  ''|infinity|*infinity) : ;;
  invalid) start_property='conflict' ;;
  *)
    effective_start_seconds="$(normalize_seconds "$effective_start_raw")"
    case "$effective_start_seconds" in
      ''|*[!0-9]*) : ;;
      *)
        start_seconds="$effective_start_seconds"
        echo "updater-schedule-timeout-effective=${effective_start_seconds}s"
        ;;
    esac
    ;;
esac
if [ -n "$effective_property" ] &&
  [ "$(normalize_seconds "$effective_property")" != \
    "$(normalize_seconds "$effective_start_raw")" ]; then
  start_property='conflict'
fi
# A per-unit setting wins the manager default. An absent per-unit setting is
# explicitly reported as inherited, rather than pretending the default is the
# exact value that -Apply projected. If this systemd cannot return either
# property, the check remains fail-closed.
case "$start_property" in
  ''|infinity|*infinity) start_property='' ;;
  *)
    case "$property_seconds" in
      ''|*[!0-9]*) start_property='invalid' ;;
      *)
        if [ -n "$effective_start_raw" ] &&
          [ "$(normalize_seconds "$effective_start_raw")" != "$property_seconds" ]; then
          start_property='conflict'
        else
          start_property="explicit:$property_seconds"
        fi
        ;;
    esac
    ;;
esac
case "$effective_start_raw" in
  '')
    case "$systemd_default_raw" in
      ''|0) : ;;
      infinity|*infinity) start_seconds='' ;;
      *)
        if [ -z "$start_seconds" ]; then
          start_seconds="${existing_default_seconds:-}"
        fi
        ;;
    esac
    ;;
  infinity|*infinity) start_seconds='' ;;
  *) start_seconds="${effective_start_seconds:-}" ;;
esac
case "$start_property" in
  invalid|conflict) start_seconds=''
    echo "updater-schedule-timeout-start=UNVERIFIED reason=${start_property}_timeout_properties"
    flag "timeout_start_${start_property}" updater-schedule-budget
    ;;
  explicit:*) start_seconds="${start_property#explicit:}" ;;
esac
# A service's explicit duration is a ceiling; the manager's default is a
# fallback only when no service duration was provided. The Effective property
# is cross-check evidence (and a fallback when explicit reads are unavailable),
# not a replacement for the per-unit value that -Apply projected.
case "$systemd_default_raw" in
  ''|0|infinity|*infinity) ;;
  *)
    if [ "$existing_default_seconds" -le 0 ]; then
      start_seconds=''
    else
      case "$start_seconds" in
        ''|*[!0-9]*) : ;;
        *)
          if [ "$start_seconds" -gt "$existing_default_seconds" ]; then
            start_seconds="$existing_default_seconds"
          fi
          ;;
      esac
    fi
    ;;
esac
case "$start_seconds" in
  ''|*[!0-9]*)
    echo "updater-schedule-timeout-start=UNVERIFIED raw=${effective_start_raw:-ABSENT}"
    flag "timeout_start_unreadable" updater-schedule-budget
    ;;
  *)
    echo "updater-schedule-timeout-start=${start_seconds}s"
    case "$start_property" in
      '') echo "updater-schedule-timeout-source=default" ;;
      explicit:*) echo "updater-schedule-timeout-source=property" ;;
    esac
    # A shorter or longer unit is a configuration change that needs a fresh
    # review of the independent call-site ceilings and recovery paths.
    if [ "$budget_valid" -eq 1 ] && [ "$budget_declaration" = exact ] &&
      [ "$largest_budget" -lt "$start_seconds" ] &&
      [ "$full_path_budget" -le $((start_seconds - recovery_reserve)) ] &&
      [ "$start_seconds" -eq "$expected_start_seconds" ]; then
      flag OK updater-schedule-budget
    elif [ "$budget_valid" -eq 1 ] && [ "$largest_budget" -ge "$start_seconds" ]; then
      flag "single_budget=${largest_budget}s" updater-schedule-budget
    elif [ "$budget_valid" -eq 1 ] && [ "$full_path_budget" -gt $((start_seconds - recovery_reserve)) ]; then
      flag "full_path_budget=${full_path_budget}s" updater-schedule-budget
    else
      flag "timeout_start=${start_seconds}s" updater-schedule-budget
    fi
    ;;
esac

# Readiness precondition, read-only. The updater probes the non-OAuth GLM
# route before any update; without docker the probe cannot be reproduced here
# and the gate reports it as unknown rather than assuming success.
if command -v docker >/dev/null 2>&1 && [ -f "$DIR/compose.yml" ]; then
  if docker compose -f "$DIR/compose.yml" config --quiet >/dev/null 2>&1; then
    flag OK updater-schedule-compose
  else
    flag compose_config_invalid updater-schedule-compose
  fi
  if docker inspect cli-proxy-api >/dev/null 2>&1; then
    state="$(docker inspect --format '{{.State.Status}}' cli-proxy-api 2>/dev/null || true)"
    if [ "$state" = running ]; then
      flag OK updater-schedule-container
    else
      flag "container_state=$state" updater-schedule-container
    fi
  else
    flag container_absent updater-schedule-container
  fi
else
  echo "updater-schedule-compose=UNVERIFIED reason=no_docker_or_compose"
  echo "updater-schedule-container=UNVERIFIED reason=no_docker_or_compose"
fi

if [ "$fail" -gt 0 ]; then
  echo "updater-schedule=FAIL checks=$fail"
  # Every failing flag has already named its own reason; only strict mode
  # fails the caller here.
  exit 1
fi
echo "updater-schedule=OK anchors=4 slots=04:00,04:10,04:20,04:30"

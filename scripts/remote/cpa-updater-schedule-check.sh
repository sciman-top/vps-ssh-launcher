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
  total=0
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

timer_block="$(systemctl cat "$TIMER" 2>/dev/null || true)"
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

# A start budget longer than the slot cannot overlap the next owner's 04:10
# anchor. systemd renders a large USec value as a duration on some builds and
# as raw microseconds on others; both shapes are normalised to seconds.
# 2026-11-20: the service sets no TimeoutStartSec, so systemd's own default
# (DefaultTimeoutStartSec, 90s unless an override drop-in changes it) is what
# actually bounds a trigger. Read the effective value instead of the raw unit
# property, otherwise a default of 90s was reported as 1200s and failed a
# budget the reviewer had already sized to the slot. An explicit per-unit
# TimeoutStartSec still wins here, because systemctl show resolves it.
start_usec_raw="$(systemctl show cliproxyapi-update.service -p TimeoutStartUSec --value 2>/dev/null || true)"
systemd_default_raw="$(systemctl show -p DefaultTimeoutStartUSec --value 2>/dev/null || true)"
start_seconds="$(normalize_seconds "$start_usec_raw")"
# A configured 0/infinity renders as a huge USec value on older builds and as
# "infinity" on newer ones; both mean "no start budget" and stay unreadable.
case "$systemd_default_raw" in
  ''|infinity) : ;;
  *)
    existing_default_seconds="$(normalize_seconds "$systemd_default_raw")"
    case "$existing_default_seconds" in
      ''|*[!0-9]*) : ;;
      *)
        case "$start_seconds" in
          ''|*[!0-9]*) start_seconds="$existing_default_seconds" ;;
          *)
            if [ "$start_seconds" -ge "$existing_default_seconds" ]; then
              start_seconds="$existing_default_seconds"
            fi
            ;;
        esac
        ;;
    esac
    ;;
esac
case "$start_seconds" in
  ''|*[!0-9]*)
    echo "updater-schedule-timeout-start=UNVERIFIED raw=${start_usec_raw:-ABSENT}"
    flag "timeout_start_unreadable" updater-schedule-budget
    ;;
  *)
    echo "updater-schedule-timeout-start=${start_seconds}s"
    if [ "$start_seconds" -le 600 ]; then
      flag OK updater-schedule-budget
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

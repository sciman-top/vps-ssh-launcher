param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [string]$Profile,
  [string]$Schedule = "0 14 1 * *",
  [switch]$Apply
)

# Monthly apt system maintenance (upgrade + cleanup) on one VPS.
# Schedule uses the server's local time: bwg/zz run in UTC, so the default
# "0 14 1 * *" fires at 22:00 UTC+8 on day 1 of every month. For a host
# already running in UTC+8 pass -Schedule '0 22 1 * *'.
# Default is a read-only probe; -Apply installs the wrapper and the cron line
# with backup + rollback. It never reboots the host automatically.

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

function Assert-CronSchedule {
  param([string]$Value)

  if ($Value -notmatch '^[0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+$') {
    throw "Schedule must be a five-field cron expression."
  }
}

function Invoke-RemoteCommand {
  param([string]$Command)

  $exitCode = Invoke-LauncherPython -Python $script:Python -ProjectRoot $repoRoot -LauncherArgs @(
    "--config", $Config,
    "--profile", $Profile,
    "--strict-host-key-checking",
    "run",
    "--command", $Command
  )
  if ($exitCode -ne 0) {
    throw "Remote command failed with exit code $exitCode."
  }
}

Initialize-WindowsProcessEnvironment
Assert-CronSchedule -Value $Schedule
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher

$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}

$applyValue = if ($Apply) { "1" } else { "0" }

$remoteCommand = @"
set -Eeuo pipefail
schedule='$Schedule'
apply='$applyValue'
maintenance_script='/usr/local/sbin/monthly-maintenance.sh'

backup_file() {
  path="`$1"
  name="`$2"
  if [ -e "`$path" ]; then
    cp -a "`$path" "`$backup_dir/`$name"
  else
    : > "`$backup_dir/`$name.missing"
  fi
}

backup_apply_state() {
  backup_file "`$maintenance_script" maintenance-wrapper
  if crontab -l > "`$backup_dir/crontab" 2>/dev/null; then
    :
  else
    rm -f "`$backup_dir/crontab"
    : > "`$backup_dir/crontab.missing"
  fi
}

restore_file() {
  path="`$1"
  name="`$2"
  if [ -f "`$backup_dir/`$name.missing" ]; then
    rm -f "`$path"
  else
    cp -a "`$backup_dir/`$name" "`$path"
  fi
}

restore_apply_state() {
  set +e
  rollback_failed=0
  restore_file "`$maintenance_script" maintenance-wrapper || rollback_failed=1
  if [ -f "`$backup_dir/crontab.missing" ]; then
    if ! crontab -r 2>/dev/null; then
      crontab -l >/dev/null 2>&1 && rollback_failed=1 || true
    fi
  else
    crontab "`$backup_dir/crontab" || rollback_failed=1
  fi
  if [ "`$rollback_failed" -eq 0 ]; then
    echo "ROLLBACK_VERIFIED backup=`$backup_dir"
  else
    echo "ROLLBACK_FAILED backup=`$backup_dir"
  fi
}

rollback_apply() {
  rc="`$?"
  trap - ERR INT TERM
  restore_apply_state
  exit "`$rc"
}

write_maintenance_wrapper() {
  cat > "`$maintenance_script" <<'EOF'
#!/usr/bin/env bash
# Monthly system maintenance for apt-based hosts.
# Steps: apt-get update + upgrade + autoremove --purge + autoclean, then a
# 30-day journal vacuum. Requires manual confirmation for reboots.
# Manual trigger rule: run this wrapper as the only remote command, verify in a
# second SSH command, and never trigger multiple VPS maintenance in parallel.
set -uo pipefail
LOG="/var/log/monthly-maintenance.log"
LOCK_FILE="/run/v2ray-agent-maint.lock"

log() { echo "[`$(date '+%Y-%m-%d %H:%M:%S')] `$*" >> "`$LOG"; }

exec 9>"`$LOCK_FILE"
if ! flock -n 9; then
  log "INFO: another maintenance/update job is already running; exit"
  exit 0
fi

if ! command -v apt-get >/dev/null 2>&1; then
  log "ERROR: apt-get not available; wrapper only supports apt-based hosts"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
APT_OPTS=(-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold -y)
fail=0

step() {
  local name="`$1"; shift
  log "INFO: `$name"
  if ! "`$@" >> "`$LOG" 2>&1; then
    log "ERROR: `$name failed"
    fail=1
  fi
}

log "========== monthly maintenance start =========="
step "apt-get update" apt-get update
step "apt-get upgrade" apt-get upgrade "`${APT_OPTS[@]}"
step "apt-get autoremove --purge" apt-get autoremove --purge "`${APT_OPTS[@]}"
step "apt-get autoclean" apt-get autoclean
step "journalctl --vacuum-time=30d" journalctl --vacuum-time=30d

if [ -f /run/reboot-required ]; then
  log "WARN: reboot required; NOT rebooting automatically"
  cat /run/reboot-required.pkgs >> "`$LOG" 2>/dev/null || true
fi

if [ "`$fail" -eq 0 ]; then
  log "========== monthly maintenance done =========="
else
  log "========== monthly maintenance finished with errors =========="
fi
exit "`$fail"
EOF
  chmod 755 "`$maintenance_script"
}

install_cron() {
  tmp="`$(mktemp)"
  crontab -l 2>/dev/null | grep -v -E '/usr/local/sbin/monthly-maintenance\.sh' > "`$tmp" || true
  echo "`$schedule /bin/bash `$maintenance_script" >> "`$tmp"
  crontab "`$tmp"
  rm -f "`$tmp"
}

if [ "`$apply" = '1' ]; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo 'missing apt-get; monthly maintenance only supports apt-based hosts' >&2
    exit 2
  fi
  backup_dir="`$(mktemp -d /var/backups/v2ray-agent-maint.XXXXXX)"
  chmod 700 "`$backup_dir"
  backup_apply_state
  trap rollback_apply ERR INT TERM
  write_maintenance_wrapper
  install_cron
  echo "APPLY_BACKUP_DIR=`$backup_dir"
fi

echo '==apt=='
command -v apt-get || true
echo '==schedule=='
echo "`$schedule"
echo '==cron=='
crontab -l 2>/dev/null | grep -E 'monthly-maintenance\.sh' || true
echo '==script=='
if [ -e "`$maintenance_script" ]; then
  ls -l "`$maintenance_script"
  grep -nE 'LOCK_FILE|flock|DEBIAN_FRONTEND|force-conf|apt-get|autoremove|autoclean|vacuum|reboot-required|NOT rebooting' "`$maintenance_script" || true
  bash -n "`$maintenance_script"
  echo syntax-ok
else
  echo missing
fi
if [ "`$apply" = '1' ]; then
  trap - ERR INT TERM
fi
"@

Invoke-RemoteCommand -Command $remoteCommand

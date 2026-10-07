#requires -Version 7
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

Initialize-WindowsProcessEnvironment
if ($Schedule -notmatch '^[0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+$') {
  throw "Schedule must be a five-field cron expression."
}
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
cron_file='/etc/cron.d/vps-launcher-monthly-maintenance'
logrotate_file='/etc/logrotate.d/vps-launcher-monthly-maintenance'
lock_file='/run/vps-ssh-launcher-maintenance.lock'
# Schedule lives in /etc/cron.d, NOT root's crontab: vasma installCronTLS
# rewrites `crontab -l` with `sed '/v2ray-agent/d'`, silently deleting any
# line whose path contains /etc/v2ray-agent/ (proven 2026-09-24 on bwg).

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
  backup_file "`$cron_file" cron-file
  backup_file "`$logrotate_file" logrotate-file
  read_crontab_or_empty "`$backup_dir/crontab" "`$backup_dir/crontab.error"
  if [ "`$CRONTAB_MISSING" -eq 1 ]; then
    rm -f "`$backup_dir/crontab"
    : > "`$backup_dir/crontab.missing"
  fi
  rm -f "`$backup_dir/crontab.error"
}

read_crontab_or_empty() {
  output_path="`$1"
  error_path="`$2"
  CRONTAB_MISSING=0
  if crontab -l > "`$output_path" 2>"`$error_path"; then
    return 0
  fi
  if grep -qiE 'no crontab for ' "`$error_path"; then
    : > "`$output_path"
    CRONTAB_MISSING=1
    return 0
  fi
  cat "`$error_path" >&2 || true
  return 1
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
  restore_file "`$cron_file" cron-file || rollback_failed=1
  restore_file "`$logrotate_file" logrotate-file || rollback_failed=1
  if [ -f "`$backup_dir/crontab.missing" ]; then
    crontab -r 2>/dev/null || {
      restore_error="`$(mktemp)"
      if ! crontab -l >/dev/null 2>"`$restore_error"; then
        grep -qiE 'no crontab for ' "`$restore_error" || rollback_failed=1
      else
        rollback_failed=1
      fi
      rm -f "`$restore_error"
    }
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
# Steps: apt-get update + upgrade (including required new packages) +
# autoremove --purge + autoclean, then a 30-day journal vacuum. Requires
# manual confirmation for reboots.
# Manual trigger rule: run this wrapper as the only remote command, verify in a
# second SSH command, and never trigger multiple VPS maintenance in parallel.
set -uo pipefail
LOG="/var/log/monthly-maintenance.log"
LOCK_FILE="/run/vps-ssh-launcher-maintenance.lock"
STATUS_DIR="/var/lib/vps-ssh-launcher/maintenance-status"
STATUS_FILE="`$STATUS_DIR/monthly-maintenance.status"
STATUS_STARTED_AT="`$(date -u +%FT%TZ)"

write_status() {
  # Best effort only: status persistence must never change the maintenance
  # result or mask an apt/verification failure.
  local result="`$1" code="`$2" finished tmp
  finished="`$(date -u +%FT%TZ)"
  mkdir -m 700 -p "`$STATUS_DIR" 2>/dev/null || return 0
  tmp="`$(mktemp "`$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
  [ -n "`$tmp" ] || return 0
  {
    printf 'name=monthly-maintenance\n'
    printf 'started_at=%s\n' "`$STATUS_STARTED_AT"
    printf 'finished_at=%s\n' "`$finished"
    printf 'result=%s\n' "`$result"
    printf 'exit_code=%s\n' "`$code"
  } > "`$tmp" 2>/dev/null || true
  chmod 600 "`$tmp" 2>/dev/null || true
  mv -f -- "`$tmp" "`$STATUS_FILE" 2>/dev/null || rm -f -- "`$tmp"
}

status_on_exit() {
  local code="`$?" result=failed
  if [ "`$code" -eq 0 ]; then
    result=success
  elif [ "`$code" -eq 75 ]; then
    result=busy
  fi
  write_status "`$result" "`$code"
  exit "`$code"
}
trap status_on_exit EXIT
write_status running 0

log() { echo "[`$(date '+%Y-%m-%d %H:%M:%S')] `$*" >> "`$LOG"; }

service_manager() {
  if command -v systemctl >/dev/null 2>&1 && systemctl cat "`$1" >/dev/null 2>&1; then
    printf 'systemctl\n'
  elif command -v rc-service >/dev/null 2>&1; then
    printf 'rc-service\n'
  else
    return 1
  fi
}

service_is_active() {
  manager="`$(service_manager "`$1")" || return 1
  if [ "`$manager" = systemctl ]; then
    systemctl is-active --quiet "`$1"
  else
    rc-service "`$1" status >/dev/null 2>&1
  fi
}

verify_proxy_services() {
  local checked=0 failed=0
  if [ -x /etc/v2ray-agent/xray/xray ] && [ -d /etc/v2ray-agent/xray/conf ]; then
    checked=1
    if ! /etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf >> "`$LOG" 2>&1 ||
       ! service_is_active xray; then
      log "ERROR: Xray post-maintenance verification failed"
      failed=1
    fi
  fi
  if [ -x /etc/v2ray-agent/sing-box/sing-box ] && [ -f /etc/v2ray-agent/sing-box/conf/config.json ]; then
    checked=1
    if ! /etc/v2ray-agent/sing-box/sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json >> "`$LOG" 2>&1 ||
       ! service_is_active sing-box; then
      log "ERROR: sing-box post-maintenance verification failed"
      failed=1
    fi
  fi
  if command -v nginx >/dev/null 2>&1; then
    checked=1
    if ! nginx -t >> "`$LOG" 2>&1; then
      log "ERROR: Nginx post-maintenance configuration test failed"
      failed=1
    fi
    if ! service_is_active nginx; then
      log "ERROR: Nginx post-maintenance service verification failed"
      failed=1
    fi
  fi
  if command -v fail2ban-client >/dev/null 2>&1; then
    checked=1
    if ! fail2ban-client ping >/dev/null 2>&1; then
      log "ERROR: fail2ban post-maintenance verification failed"
      failed=1
    fi
  fi
  if [ "`$checked" -eq 0 ]; then
    log "INFO: no managed proxy service detected; service verification skipped"
  else
    log "INFO: managed proxy services verified after apt phase"
  fi
  return "`$failed"
}

exec 9>"`$LOCK_FILE"
if ! flock -n 9; then
  log "DEFERRED_BUSY: another maintenance/update job is already running"
  exit 75
fi

if ! command -v apt-get >/dev/null 2>&1; then
  log "ERROR: apt-get not available; wrapper only supports apt-based hosts"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
# DPkg::Lock::Timeout makes apt wait for the dpkg frontend lock instead of
# failing immediately. The shared flock only serialises this repo's own
# maintenance entries; it cannot exclude the host's unattended-upgrades timer,
# and a lock collision used to end the whole monthly run as a hard failure.
APT_LOCK_OPTS=(-o DPkg::Lock::Timeout=600)
APT_OPTS=(-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "`${APT_LOCK_OPTS[@]}" -y)
fail=0
FAILURE_REASONS=""

record_failure() {
  local reason="`$1"
  fail=1
  if [ -z "`$FAILURE_REASONS" ]; then
    FAILURE_REASONS="`$reason"
  else
    FAILURE_REASONS="`$FAILURE_REASONS,`$reason"
  fi
  log "ERROR: `$reason"
}

package_state_sha() {
  if ! command -v dpkg-query >/dev/null 2>&1; then
    printf 'unavailable\n'
    return 0
  fi
  dpkg-query -W -f='`${binary:Package}\t`${Status}\t`${Version}\n' 2>/dev/null |
    LC_ALL=C sort |
    sha256sum |
    awk '{print `$1}'
}

step() {
  local name="`$1"; shift
  log "INFO: `$name"
  if ! "`$@" >> "`$LOG" 2>&1; then
    record_failure "`$name"
  fi
}

log "========== monthly maintenance start =========="
# Snapshot running containers before the apt phase: upgrades that touch
# docker-ce/glibc can bounce the daemon, and restart=always containers are
# expected to come back on their own. A present-but-unreachable daemon blocks
# package changes because its workloads cannot be verified afterwards.
DOCKER_SNAPSHOT_VALID=0
DOCKER_SNAPSHOT=""
if command -v docker >/dev/null 2>&1; then
  if ! docker info >/dev/null 2>&1; then
    record_failure "docker-daemon-preflight"
  elif ! DOCKER_SNAPSHOT="`$(docker ps --format '{{.Names}}' | sort)"; then
    record_failure "docker-container-inventory-preflight"
  else
    DOCKER_SNAPSHOT_VALID=1
    log "INFO: docker containers before upgrade: `$(printf '%s\n' "`$DOCKER_SNAPSHOT" | tr '\n' ' ')"
  fi
fi
if [ "`$fail" -ne 0 ]; then
  log "FAILURE_SUMMARY=`$FAILURE_REASONS"
  exit 1
fi
PACKAGE_STATE_BEFORE="`$(package_state_sha)"
log "INFO: package-state-sha256-before=`$PACKAGE_STATE_BEFORE"
DPKG_AUDIT_PRE="`$(mktemp)"
dpkg_audit_dirty() {
  # Explicit returns: this helper is invoked from an `if`, where bash ignores
  # `set -e`, so a bare trailing test would be the only thing that counts.
  if ! dpkg --audit >"`$DPKG_AUDIT_PRE" 2>&1; then
    return 0
  fi
  [ -s "`$DPKG_AUDIT_PRE" ]
}
if dpkg_audit_dirty; then
  tail -n 20 "`$DPKG_AUDIT_PRE" >> "`$LOG" 2>/dev/null || true
  # A half-configured dpkg state (interrupted apt) makes every later apt phase
  # fail in a chain, and this script used to just record it and continue.
  # Attempt the standard recovery once, then re-audit: only a state that is
  # still dirty afterwards is a real maintenance failure.
  log "WARN: dpkg-audit-pre=dirty; attempting dpkg --configure -a recovery"
  if DEBIAN_FRONTEND=noninteractive dpkg --configure -a >> "`$LOG" 2>&1 && ! dpkg_audit_dirty; then
    log "INFO: dpkg-audit-pre=recovered"
  else
    tail -n 20 "`$DPKG_AUDIT_PRE" >> "`$LOG" 2>/dev/null || true
    record_failure "dpkg-audit-pre"
  fi
else
  log "INFO: dpkg-audit-pre=clean"
fi
rm -f "`$DPKG_AUDIT_PRE"
if ! apt-get check >> "`$LOG" 2>&1; then
  record_failure "apt-get-check-pre"
else
  log "INFO: apt-get-check-pre=OK"
fi
step "apt-get update" apt-get update "`${APT_LOCK_OPTS[@]}"
APT_SIM_TMP="`$(mktemp)"
if apt-get -s -o Debug::NoLocking=1 upgrade --with-new-pkgs >"`$APT_SIM_TMP" 2>&1; then
  APT_SIM_SUMMARY="`$(grep -E '^[0-9]+ upgraded,' "`$APT_SIM_TMP" | tail -n 1)"
  log "INFO: apt-simulation-before-upgrade=`${APT_SIM_SUMMARY:-unknown}"
else
  tail -n 20 "`$APT_SIM_TMP" >> "`$LOG" 2>/dev/null || true
  record_failure "apt-simulation-pre"
fi
rm -f "`$APT_SIM_TMP"
# Never start a package mutation when a preflight, index refresh, or upgrade
# simulation failed. Autoremove is simulated only after the upgrade so its
# review reflects the new package dependency and auto/manual marks.
if [ "`$fail" -ne 0 ]; then
  log "FAILURE_SUMMARY=`$FAILURE_REASONS"
  exit 1
fi
step "apt-get upgrade --with-new-pkgs" apt-get upgrade --with-new-pkgs "`${APT_OPTS[@]}"
if [ "`$fail" -eq 0 ]; then
  APT_PURGE_SIM_TMP="`$(mktemp)"
  if apt-get -s -o Debug::NoLocking=1 autoremove --purge >"`$APT_PURGE_SIM_TMP" 2>&1; then
    log "INFO: apt-simulation-before-autoremove-start"
    head -n 200 "`$APT_PURGE_SIM_TMP" | sed 's/^/autoremove-sim: /' >> "`$LOG" 2>/dev/null || true
    log "INFO: apt-simulation-before-autoremove-end"
    step "apt-get autoremove --purge" apt-get autoremove --purge "`${APT_OPTS[@]}"
  else
    tail -n 20 "`$APT_PURGE_SIM_TMP" >> "`$LOG" 2>/dev/null || true
    record_failure "apt-simulation-pre-autoremove"
  fi
  rm -f "`$APT_PURGE_SIM_TMP"
fi
if [ "`$fail" -eq 0 ]; then
  step "apt-get autoclean" apt-get autoclean
  step "journalctl --vacuum-time=30d" journalctl --vacuum-time=30d
fi
PACKAGE_STATE_AFTER="`$(package_state_sha)"
log "INFO: package-state-sha256-after=`$PACKAGE_STATE_AFTER"
if [ "`$PACKAGE_STATE_BEFORE" = "`$PACKAGE_STATE_AFTER" ]; then
  log "INFO: package-state-change=none"
else
  log "INFO: package-state-change=changed"
fi
DPKG_AUDIT_POST="`$(mktemp)"
if ! dpkg --audit >"`$DPKG_AUDIT_POST" 2>&1; then
  tail -n 20 "`$DPKG_AUDIT_POST" >> "`$LOG" 2>/dev/null || true
  record_failure "dpkg-audit-post"
elif [ -s "`$DPKG_AUDIT_POST" ]; then
  tail -n 20 "`$DPKG_AUDIT_POST" >> "`$LOG" 2>/dev/null || true
  record_failure "dpkg-audit-post"
else
  log "INFO: dpkg-audit-post=clean"
fi
rm -f "`$DPKG_AUDIT_POST"
if ! apt-get check >> "`$LOG" 2>&1; then
  record_failure "apt-get-check-post"
else
  log "INFO: apt-get-check-post=OK"
fi
APT_SIM_POST="`$(mktemp)"
if apt-get -s -o Debug::NoLocking=1 upgrade --with-new-pkgs >"`$APT_SIM_POST" 2>&1; then
  APT_POST_SUMMARY="`$(grep -E '^[0-9]+ upgraded,' "`$APT_SIM_POST" | tail -n 1)"
  log "INFO: apt-simulation-after-upgrade=`${APT_POST_SUMMARY:-unknown}"
else
  tail -n 20 "`$APT_SIM_POST" >> "`$LOG" 2>/dev/null || true
  record_failure "apt-simulation-post"
fi
rm -f "`$APT_SIM_POST"

if [ "`$DOCKER_SNAPSHOT_VALID" -eq 1 ]; then
  log "INFO: re-verifying docker containers recovered after apt phase"
  missing=""
  docker_readback_failed=0
  for _ in `$(seq 1 12); do
    if ! docker info >/dev/null 2>&1; then
      docker_readback_failed=1
    elif ! running="`$(docker ps --format '{{.Names}}' | sort)"; then
      docker_readback_failed=1
    else
      docker_readback_failed=0
      missing="`$(printf '%s\n' "`$DOCKER_SNAPSHOT" | grep -Fxv -f <(printf '%s\n' "`$running") || true)"
      if [ -z "`$missing" ]; then
        break
      fi
    fi
    sleep 10
  done
  if [ "`$docker_readback_failed" -eq 1 ]; then
    log "ERROR: Docker daemon or container inventory remained unavailable after apt phase"
    record_failure "docker-daemon-post-maintenance"
  elif [ -n "`$missing" ]; then
    for name in `$missing; do
      log "WARN: container not running after upgrade; attempting explicit start: `$name"
      docker start "`$name" >/dev/null 2>&1 || log "ERROR: explicit start failed: `$name"
    done
    sleep 10
    if ! docker info >/dev/null 2>&1 ||
       ! running="`$(docker ps --format '{{.Names}}' 2>/dev/null | sort)"; then
      docker_readback_failed=1
    else
      missing="`$(printf '%s\n' "`$DOCKER_SNAPSHOT" | grep -Fxv -f <(printf '%s\n' "`$running") || true)"
    fi
  fi
  if [ "`$docker_readback_failed" -eq 1 ]; then
    log "ERROR: Docker daemon or container inventory became unavailable during recovery"
    record_failure "docker-daemon-recovery"
  elif [ -n "`$missing" ]; then
    log "ERROR: containers still down after recovery attempt: `$(printf '%s\n' "`$missing" | tr '\n' ' ')"
    record_failure "docker-containers-missing"
  else
    log "INFO: docker containers verified running"
  fi
fi

if [ -f /run/reboot-required ]; then
  # Structured marker so the outcome is greppable from the local probe; the
  # age is how long this boot has been pending, since /run is tmpfs.
  REBOOT_AGE_DAYS=`$(( ( `$(date +%s) - `$(stat -c %Y /run/reboot-required) ) / 86400 ))
  log "REBOOT_REQUIRED=pending age_days=`$REBOOT_AGE_DAYS"
  log "WARN: reboot required; NOT rebooting automatically"
  cat /run/reboot-required.pkgs >> "`$LOG" 2>/dev/null || true
else
  log "REBOOT_REQUIRED=none"
fi

if ! verify_proxy_services; then
  record_failure "proxy-services"
fi

if [ "`$fail" -eq 0 ]; then
  log "FAILURE_SUMMARY=none"
  log "========== monthly maintenance done =========="
else
  log "FAILURE_SUMMARY=`$FAILURE_REASONS"
  log "========== monthly maintenance finished with errors =========="
fi
exit "`$fail"
EOF
  chmod 755 "`$maintenance_script"
}

install_cron() {
  tmp="`$(mktemp)"
  current="`$(mktemp)"
  error="`$(mktemp)"
  if ! read_crontab_or_empty "`$current" "`$error"; then
    rm -f "`$tmp" "`$current" "`$error"
    return 1
  fi
  sed -E '/\/usr\/local\/sbin\/monthly-maintenance\.sh/d' "`$current" > "`$tmp"
  if ! crontab "`$tmp"; then
    rm -f "`$tmp" "`$current" "`$error"
    return 1
  fi
  rm -f "`$tmp" "`$current" "`$error"
  printf 'SHELL=/bin/bash\n%s root /bin/bash %s\n' "`$schedule" "`$maintenance_script" > "`$cron_file"
  chmod 644 "`$cron_file"
}

install_logrotate() {
  # /var/log/monthly-maintenance.log is append-only and previously grew
  # unbounded; keep six compressed generations.
  cat > "`$logrotate_file" <<'EOF'
/var/log/monthly-maintenance.log {
    monthly
    rotate 6
    size 10M
    compress
    delaycompress
    missingok
    notifempty
}
EOF
  chmod 644 "`$logrotate_file"
}

prune_deploy_backups() {
  local keep=8 entry removed=0
  while IFS= read -r entry; do
    if rm -rf -- "`$entry"; then
      removed="`$((removed + 1))"
    else
      echo "PRUNE_FAILED path=`$entry" >&2
      return 0
    fi
  done < <(
    find /var/backups -mindepth 1 -maxdepth 1 -type d \
      -name 'v2ray-agent-maint.*' -printf '%T@ %p\n' |
      sort -rn | tail -n +`$((keep + 1)) | cut -d' ' -f2-
  )
  echo "PRUNE scope=monthly_deploy_backups removed=`$removed policy=keep_`$keep"
}

if [ "`$apply" = '1' ]; then
  if [ "`$(id -u)" != '0' ]; then
    echo 'apply requires root' >&2
    exit 4
  fi
  if ! command -v flock >/dev/null 2>&1; then
    echo 'missing dependency: flock' >&2
    exit 3
  fi
  exec 9>"`$lock_file"
  if ! flock -n 9; then
    echo "REFUSE busy lock=`$lock_file" >&2
    exit 75
  fi
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
  install_logrotate
  prune_deploy_backups
  echo "APPLY_BACKUP_DIR=`$backup_dir"
fi

echo '==apt=='
command -v apt-get || true
echo '==schedule=='
echo "`$schedule"
echo '==cron=='
echo '--crontab-legacy--'
crontab -l 2>/dev/null | sed -n -E '/monthly-maintenance\.sh/p' || true
echo "--`$cron_file--"
if [ -e "`$cron_file" ]; then
  cat "`$cron_file"
else
  echo missing
fi
echo '==script=='
if [ -e "`$maintenance_script" ]; then
  ls -l "`$maintenance_script"
  grep -nE 'LOCK_FILE|flock|DEBIAN_FRONTEND|force-conf|apt-get|autoremove|autoclean|vacuum|reboot-required|NOT rebooting|DOCKER_SNAPSHOT|re-verifying|explicit start|still down' "`$maintenance_script" || true
  bash -n "`$maintenance_script"
  echo syntax-ok
else
  echo missing
fi
echo '==logrotate=='
if [ -e "`$logrotate_file" ]; then
  cat "`$logrotate_file"
  if command -v logrotate >/dev/null 2>&1; then
    logrotate -d "`$logrotate_file" >/dev/null 2>&1 && echo logrotate-config-ok || echo logrotate-config-invalid
  fi
else
  echo missing
fi
echo '==monthly-log=='
# Surface the last structured outcome instead of leaving it to age silently in
# a remote log no gate reads. Only the wrapper's own marker lines are echoed;
# the apt transcript itself stays in the log.
if [ -e /var/log/monthly-maintenance.log ]; then
  grep -E 'FAILURE_SUMMARY=|REBOOT_REQUIRED=|package-state-change=|apt-simulation-before-|apt-simulation-after-|ROLLBACK_VERIFIED|ROLLBACK_FAILED|WARN: |ERROR: ' /var/log/monthly-maintenance.log 2>/dev/null | tail -n 10 || true
else
  echo missing
fi
if [ "`$apply" = '1' ]; then
  trap - ERR INT TERM
fi
"@

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand

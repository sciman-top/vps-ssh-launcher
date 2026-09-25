#requires -Version 7
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [string]$Profile,
  [Parameter(Mandatory = $true)]
  [ValidateSet("xray", "sing-box")]
  [string]$Kernel,
  [string]$Version,
  [Alias("Sha256")]
  [string]$InstalledSha256,
  [string]$VasmaSha256,
  [string]$Schedule = "20 14 * * 5",
  [switch]$Apply
)

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
$normalizedVersion = if ($Version) { $Version.Trim().TrimStart('v') } else { "" }
if ($Apply) {
  if ($normalizedVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "-Apply requires -Version like 26.3.27 so vasma output can be verified."
  }
  if ($InstalledSha256 -notmatch '^(?i:[0-9a-f]{64})$') {
    throw "-Apply requires a 64-hex -InstalledSha256 (or legacy -Sha256) pin for the selected installed core binary."
  }
  if ($VasmaSha256 -notmatch '^(?i:[0-9a-f]{64})$') {
    throw "-Apply requires a 64-hex -VasmaSha256 pin for the deployed vasma script."
  }
}
$targetVersion = if ($normalizedVersion) { "v$normalizedVersion" } else { "" }
$expectedSha256 = if ($InstalledSha256) { $InstalledSha256.ToLowerInvariant() } else { "" }
$expectedVasmaSha256 = if ($VasmaSha256) { $VasmaSha256.ToLowerInvariant() } else { "" }
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher

$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}

$applyValue = if ($Apply) { "1" } else { "0" }

$remoteCommand = @"
set -Eeuo pipefail
kernel='$Kernel'
schedule='$Schedule'
apply='$applyValue'
target_version='$targetVersion'
expected_sha256='$expectedSha256'
expected_vasma_sha256='$expectedVasmaSha256'

xray_script='/etc/v2ray-agent/auto_update_xray.sh'
singbox_script='/etc/v2ray-agent/auto_update_singbox.sh'
cron_file='/etc/cron.d/vps-launcher-kernel-update'
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
  backup_file "`$xray_script" xray-wrapper
  backup_file "`$singbox_script" singbox-wrapper
  backup_file "`$cron_file" cron-file
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
  restore_file "`$xray_script" xray-wrapper || rollback_failed=1
  restore_file "`$singbox_script" singbox-wrapper || rollback_failed=1
  restore_file "`$cron_file" cron-file || rollback_failed=1
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

require_vasma() {
  for candidate in /usr/bin/vasma /usr/sbin/vasma; do
    if [ -x "`$candidate" ]; then
      return 0
    fi
  done
  echo 'missing executable /usr/bin/vasma or /usr/sbin/vasma' >&2
  exit 2
}

require_vasma_pin() {
  if [ "`$apply" != '1' ]; then
    return 0
  fi
  vasma_path="`$(for candidate in /usr/bin/vasma /usr/sbin/vasma; do
    if [ -x "`$candidate" ]; then printf '%s' "`$candidate"; break; fi
  done)"
  if [ -z "`$vasma_path" ]; then
    echo 'missing executable /usr/bin/vasma or /usr/sbin/vasma' >&2
    exit 2
  fi
  actual_vasma_sha256="`$(sha256sum "`$vasma_path" | awk '{print `$1}')"
  if [ "`$actual_vasma_sha256" != "`$expected_vasma_sha256" ]; then
    echo "vasma SHA-256 mismatch expected=`$expected_vasma_sha256 actual=`$actual_vasma_sha256" >&2
    exit 13
  fi
}

require_update_dependencies() {
  for command_name in curl jq sha256sum flock; do
    if ! command -v "`$command_name" >/dev/null 2>&1; then
      echo "missing dependency: `$command_name" >&2
      exit 3
    fi
  done
  if ! command -v systemctl >/dev/null 2>&1 && ! command -v rc-service >/dev/null 2>&1; then
    echo 'missing service manager: systemctl or rc-service' >&2
    exit 3
  fi
}

write_xray_wrapper() {
  cat > "`$xray_script" <<'EOF'
#!/usr/bin/env bash
# Auto-update Xray core through v2ray-agent/vasma.
# Menu path: 16.core管理 -> 1.Xray-core -> 1.升级Xray-core.
# Manual trigger rule: run this wrapper as the only remote command, verify in a
# second SSH command, and never trigger multiple VPS kernel updates in parallel.
set -Eeuo pipefail
LOG="/etc/v2ray-agent/crontab_xray_update.log"
LOCK_FILE="/run/vps-ssh-launcher-maintenance.lock"
XRAY_BINARY="/etc/v2ray-agent/xray/xray"
XRAY_CONFDIR="/etc/v2ray-agent/xray/conf"
TARGET_VERSION="__TARGET_VERSION__"
EXPECTED_SHA256="__EXPECTED_SHA256__"
EXPECTED_VASMA_SHA256="__EXPECTED_VASMA_SHA256__"
VASMA=""
BACKUP_DIR=""
UPDATE_STARTED=0

log() { echo "[`$(date '+%Y-%m-%d %H:%M:%S')] `$*" >> "`$LOG"; }

find_vasma() {
  for candidate in /usr/bin/vasma /usr/sbin/vasma; do
    if [ -x "`$candidate" ]; then
      VASMA="`$candidate"
      return 0
    fi
  done
  return 1
}

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

service_restart() {
  manager="`$(service_manager "`$1")" || return 1
  if [ "`$manager" = systemctl ]; then
    systemctl restart "`$1"
  else
    rc-service "`$1" restart
  fi
}

service_start() {
  manager="`$(service_manager "`$1")" || return 1
  if [ "`$manager" = systemctl ]; then
    systemctl start "`$1"
  else
    rc-service "`$1" start
  fi
}

recover_on_error() {
  rc="`$?"
  log "ERROR: vasma Xray-core update failed with exit=`$rc; checking xray state"
  restored=0
  if [ "`$UPDATE_STARTED" = '1' ] && [ -n "`$BACKUP_DIR" ] && [ -f "`$BACKUP_DIR/xray" ]; then
    if cp -a "`$BACKUP_DIR/xray" "`$XRAY_BINARY"; then
      restored=1
      log "WARN: restored pre-update Xray binary after failure"
      service_restart xray >> "`$LOG" 2>&1 || log "ERROR: xray restart after rollback failed"
    else
      log "ERROR: restoring pre-update Xray binary failed"
    fi
  fi
  if [ "`$restored" = '1' ]; then
    if verify_current_xray; then
      log "ROLLBACK_VERIFIED backup=`$BACKUP_DIR"
    else
      log "ERROR: Xray service/config verification after rollback failed"
    fi
  elif ! service_is_active xray; then
    log "WARN: xray inactive after failure; trying service start"
    service_start xray >> "`$LOG" 2>&1 || true
  fi
  exit "`$rc"
}
trap recover_on_error ERR

exec 9>"`$LOCK_FILE"
if ! flock -n 9; then
  log "DEFERRED_BUSY: another maintenance/update job is already running"
  exit 75
fi

if ! find_vasma; then
  log "ERROR: /usr/bin/vasma or /usr/sbin/vasma not executable"
  exit 1
fi
if [ "`$(sha256sum "`$VASMA" | awk '{print `$1}')" != "`$EXPECTED_VASMA_SHA256" ]; then
  log "ERROR: vasma SHA-256 mismatch; refusing unreviewed script"
  exit 13
fi
if [ ! -x "`$XRAY_BINARY" ] || [ ! -d "`$XRAY_CONFDIR" ]; then
  log "ERROR: v2ray-agent Xray layout not detected; refusing vasma compatibility path"
  exit 4
fi
if [ -z "`$TARGET_VERSION" ] || [ -z "`$EXPECTED_SHA256" ]; then
  log "ERROR: no version/SHA-256 pin was projected; vasma update refused"
  exit 5
fi

verify_vasma_anchors() {
  # The menu pipeline is position-coupled to the deployed script's prompts.
  # Refuse before driving vasma if the expected menu structure is absent, so a
  # repointed or rewritten vasma cannot receive inputs meant for another menu.
  if ! grep -qF '16.core管理' "`$VASMA" \
     || ! grep -qF 'coreVersionManageMenu' "`$VASMA" \
     || ! grep -qF 'xrayVersionManageMenu' "`$VASMA"; then
    log "ERROR: vasma menu anchors missing for xray pipeline; refusing"
    exit 12
  fi
  if ! grep -qF '1.升级Xray-core' "`$VASMA" \
     && ! grep -qF '1.Upgrade Xray-core' "`$VASMA"; then
    log "ERROR: Xray menu label anchors missing; refusing"
    exit 12
  fi
  if ! grep -qF '是否更新、升级？[y/n]' "`$VASMA" \
     && ! grep -qF '是否更新？[y/n]' "`$VASMA" \
     && ! grep -qF '是否重新安装？[y/n]' "`$VASMA" \
     && ! grep -qF 'Update? [y/n]' "`$VASMA" \
     && ! grep -qF 'Upgrade? [y/n]' "`$VASMA"; then
    log "ERROR: vasma update prompt anchors missing; refusing"
    exit 12
  fi
}
verify_vasma_anchors

current_xray_version() {
  "`$XRAY_BINARY" --version | awk 'NR == 1 { print "v" `$2 }'
}

vasma_visible_stable_xray_version() {
  curl -fsSL --connect-timeout 10 --max-time 30 "https://api.github.com/repos/XTLS/Xray-core/releases/latest" |
    jq -r '.tag_name // empty'
}

verify_current_xray() {
  service_is_active xray
  "`$XRAY_BINARY" run -test -confdir "`$XRAY_CONFDIR" >> "`$LOG" 2>&1
}

verify_target_xray() {
  [ "`$(current_xray_version)" = "`$TARGET_VERSION" ]
  [ "`$(sha256sum "`$XRAY_BINARY" | awk '{print `$1}')" = "`$EXPECTED_SHA256" ]
  [ -x "`$XRAY_BINARY" ]
  verify_current_xray
}

restore_xray() {
  if [ -z "`$BACKUP_DIR" ] || [ ! -f "`$BACKUP_DIR/xray" ]; then
    log "ERROR: Xray rollback backup is missing"
    return 1
  fi
  if ! cp -a "`$BACKUP_DIR/xray" "`$XRAY_BINARY"; then
    log "ERROR: restoring pre-update Xray binary failed"
    return 1
  fi
  if ! service_restart xray; then
    log "ERROR: xray restart after rollback failed"
    return 1
  fi
  if ! verify_current_xray; then
    log "ERROR: Xray service/config verification after rollback failed"
    return 1
  fi
  UPDATE_STARTED=0
  log "ROLLBACK_VERIFIED backup=`$BACKUP_DIR"
}

log "========== vasma Xray-core update start =========="
current_version="`$(current_xray_version)"
if [ "`$current_version" = "`$TARGET_VERSION" ] && [ "`$(sha256sum "`$XRAY_BINARY" | awk '{print `$1}')" = "`$EXPECTED_SHA256" ]; then
  verify_current_xray
  log "INFO: pinned Xray version and hash already match; skip reinstall"
  exit 0
fi
if ! latest_version="`$(vasma_visible_stable_xray_version)"; then
  log "WARN: unable to query latest stable Xray version; keep and verify current installation"
  verify_current_xray
  log "========== vasma Xray-core update skipped =========="
  exit 0
fi
if [ -z "`$latest_version" ]; then
  log "WARN: vasma-visible stable Xray version is empty; skip update to avoid empty download URL"
  verify_current_xray
  log "========== vasma Xray-core update skipped =========="
  exit 0
fi
if [ "`$current_version" = "`$latest_version" ]; then
  log "INFO: current Xray version `$current_version equals vasma-visible latest but hash differs; reinstalling pinned target"
fi
if [ "`$latest_version" != "`$TARGET_VERSION" ]; then
  log "UNVERIFIED: upstream latest `$latest_version does not equal pinned target `$TARGET_VERSION; skip vasma"
  verify_current_xray
  exit 10
fi
BACKUP_DIR="`$(mktemp -d /var/backups/v2ray-agent-core-update.XXXXXX)"
chmod 700 "`$BACKUP_DIR"
cp -a "`$XRAY_BINARY" "`$BACKUP_DIR/xray"
UPDATE_STARTED=1
printf '16\n1\n1\ny\n' | "`$VASMA" >> "`$LOG" 2>&1
if ! verify_target_xray; then
  log "ERROR: pinned Xray version/hash/config verification failed"
  restore_xray || log "ROLLBACK_FAILED backup=`$BACKUP_DIR"
  exit 11
fi
UPDATE_STARTED=0
log "========== vasma Xray-core update done =========="
EOF
  sed -i "s/__TARGET_VERSION__/`$target_version/; s/__EXPECTED_SHA256__/`$expected_sha256/; s/__EXPECTED_VASMA_SHA256__/`$expected_vasma_sha256/" "`$xray_script"
  chmod 755 "`$xray_script"
}

write_singbox_wrapper() {
  cat > "`$singbox_script" <<'EOF'
#!/usr/bin/env bash
# Auto-update sing-box core through v2ray-agent/vasma.
# Menu path: 16.core管理 -> 2.sing-box -> 1.升级 sing-box.
# Manual trigger rule: run this wrapper as the only remote command, verify in a
# second SSH command, and never trigger multiple VPS kernel updates in parallel.
set -Eeuo pipefail
LOG="/etc/v2ray-agent/crontab_singbox_update.log"
LOCK_FILE="/run/vps-ssh-launcher-maintenance.lock"
SINGBOX_CONFIG="/etc/v2ray-agent/sing-box/conf/config.json"
SINGBOX_CONF_DIR="/etc/v2ray-agent/sing-box/conf"
SINGBOX_SOURCE_DIR="/etc/v2ray-agent/sing-box/conf/config"
SINGBOX_ROUTE_FRAGMENT="/etc/v2ray-agent/sing-box/conf/config/99_vps_ssh_launcher_ipv4_only.json"
SINGBOX_BINARY="/etc/v2ray-agent/sing-box/sing-box"
TARGET_VERSION="__TARGET_VERSION__"
EXPECTED_SHA256="__EXPECTED_SHA256__"
EXPECTED_VASMA_SHA256="__EXPECTED_VASMA_SHA256__"
VASMA=""
BACKUP_DIR=""
ROUTE_BACKUP_DIR=""
ROUTE_CHANGED=0
UPDATE_STARTED=0

log() { echo "[`$(date '+%Y-%m-%d %H:%M:%S')] `$*" >> "`$LOG"; }

find_vasma() {
  for candidate in /usr/bin/vasma /usr/sbin/vasma; do
    if [ -x "`$candidate" ]; then
      VASMA="`$candidate"
      return 0
    fi
  done
  return 1
}

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

service_restart() {
  manager="`$(service_manager "`$1")" || return 1
  if [ "`$manager" = systemctl ]; then
    systemctl restart "`$1"
  else
    rc-service "`$1" restart
  fi
}

service_start() {
  manager="`$(service_manager "`$1")" || return 1
  if [ "`$manager" = systemctl ]; then
    systemctl start "`$1"
  else
    rc-service "`$1" start
  fi
}

recover_on_error() {
  rc="`$?"
  log "ERROR: vasma sing-box update failed with exit=`$rc; checking sing-box state"
  restored=0
  if [ "`$UPDATE_STARTED" = '1' ] && [ -n "`$BACKUP_DIR" ] && [ -f "`$BACKUP_DIR/sing-box" ] && [ -d "`$BACKUP_DIR/conf" ]; then
    if rm -rf "`$SINGBOX_CONF_DIR" &&
       cp -a "`$BACKUP_DIR/conf" "`$SINGBOX_CONF_DIR" &&
       cp -a "`$BACKUP_DIR/sing-box" "`$SINGBOX_BINARY"; then
      restored=1
      log "WARN: restored pre-update sing-box binary and configuration after failure"
      service_restart sing-box >> "`$LOG" 2>&1 || log "ERROR: sing-box restart after rollback failed"
    else
      log "ERROR: restoring pre-update sing-box binary failed"
    fi
  fi
  if [ "`$restored" = '1' ]; then
    if verify_current_singbox; then
      log "ROLLBACK_VERIFIED backup=`$BACKUP_DIR"
    else
      log "ERROR: sing-box service/config verification after rollback failed"
    fi
  elif [ "`$ROUTE_CHANGED" = '1' ] && [ -n "`$ROUTE_BACKUP_DIR" ]; then
    if restore_route_state &&
       service_restart sing-box >> "`$LOG" 2>&1 &&
       service_is_active sing-box &&
       "`$SINGBOX_BINARY" check -c "`$SINGBOX_CONFIG" >> "`$LOG" 2>&1; then
      log "ROLLBACK_VERIFIED route_backup=`$ROUTE_BACKUP_DIR"
    else
      log "ERROR: sing-box route rollback verification failed"
    fi
  elif ! service_is_active sing-box; then
    log "WARN: sing-box inactive after failure; trying service start"
    service_start sing-box >> "`$LOG" 2>&1 || true
  fi
  exit "`$rc"
}
trap recover_on_error ERR

exec 9>"`$LOCK_FILE"
if ! flock -n 9; then
  log "DEFERRED_BUSY: another maintenance/update job is already running"
  exit 75
fi

if ! find_vasma; then
  log "ERROR: /usr/bin/vasma or /usr/sbin/vasma not executable"
  exit 1
fi
if [ "`$(sha256sum "`$VASMA" | awk '{print `$1}')" != "`$EXPECTED_VASMA_SHA256" ]; then
  log "ERROR: vasma SHA-256 mismatch; refusing unreviewed script"
  exit 13
fi
if [ ! -x "`$SINGBOX_BINARY" ] || [ ! -f "`$SINGBOX_CONFIG" ]; then
  log "ERROR: v2ray-agent sing-box layout not detected; refusing vasma compatibility path"
  exit 4
fi
if [ -z "`$TARGET_VERSION" ] || [ -z "`$EXPECTED_SHA256" ]; then
  log "ERROR: no version/SHA-256 pin was projected; vasma update refused"
  exit 5
fi

verify_vasma_anchors() {
  # The menu pipeline is position-coupled to the deployed script's prompts.
  # Refuse before driving vasma if the expected menu structure is absent, so a
  # repointed or rewritten vasma cannot receive inputs meant for another menu.
  if ! grep -qF '16.core管理' "`$VASMA" \
     || ! grep -qF 'coreVersionManageMenu' "`$VASMA" \
     || ! grep -qF 'singBoxVersionManageMenu' "`$VASMA"; then
    log "ERROR: vasma menu anchors missing for sing-box pipeline; refusing"
    exit 12
  fi
  if ! grep -qF '1.升级 sing-box' "`$VASMA" \
     && ! grep -qF '1. Upgrade sing-box' "`$VASMA"; then
    log "ERROR: sing-box menu label anchors missing; refusing"
    exit 12
  fi
  if ! grep -qF '是否更新、升级？[y/n]' "`$VASMA" \
     && ! grep -qF 'Update? [y/n]' "`$VASMA" \
     && ! grep -qF 'Upgrade? [y/n]' "`$VASMA"; then
    log "ERROR: vasma update prompt anchors missing; refusing"
    exit 12
  fi
}
verify_vasma_anchors

current_singbox_version() {
  "`$SINGBOX_BINARY" version | awk '/^sing-box version/ { print "v" `$3 }'
}

vasma_visible_stable_singbox_version() {
  curl -fsSL --connect-timeout 10 --max-time 30 "https://api.github.com/repos/SagerNet/sing-box/releases/latest" |
    jq -r '.tag_name // empty'
}

backup_route_state() {
  ROUTE_BACKUP_DIR="`$(mktemp -d /var/backups/v2ray-agent-route.XXXXXX)"
  chmod 700 "`$ROUTE_BACKUP_DIR"
  if [ -e "`$SINGBOX_ROUTE_FRAGMENT" ]; then
    cp -a "`$SINGBOX_ROUTE_FRAGMENT" "`$ROUTE_BACKUP_DIR/route-fragment"
  else
    : > "`$ROUTE_BACKUP_DIR/route-fragment.missing"
  fi
  if [ -f "`$SINGBOX_CONFIG" ]; then
    cp -a "`$SINGBOX_CONFIG" "`$ROUTE_BACKUP_DIR/config.json"
  else
    : > "`$ROUTE_BACKUP_DIR/config.json.missing"
  fi
}

restore_route_state() {
  [ -n "`$ROUTE_BACKUP_DIR" ] || return 0
  if [ -f "`$ROUTE_BACKUP_DIR/route-fragment.missing" ]; then
    rm -f "`$SINGBOX_ROUTE_FRAGMENT"
  else
    cp -a "`$ROUTE_BACKUP_DIR/route-fragment" "`$SINGBOX_ROUTE_FRAGMENT"
  fi
  if [ -f "`$ROUTE_BACKUP_DIR/config.json.missing" ]; then
    rm -f "`$SINGBOX_CONFIG"
  else
    cp -a "`$ROUTE_BACKUP_DIR/config.json" "`$SINGBOX_CONFIG"
  fi
}

source_has_ipv4_only_route() {
  local source_file
  while IFS= read -r -d '' source_file; do
    if jq -e '(.route.rules // []) | any(.action == "resolve" and .strategy == "ipv4_only")' "`$source_file" >/dev/null 2>&1; then
      return 0
    fi
  done < <(find "`$SINGBOX_SOURCE_DIR" -maxdepth 1 -type f -name '*.json' -print0)
  return 1
}

ensure_ipv4_only_route() {
  ROUTE_CHANGED=0
  if [ ! -d "`$SINGBOX_SOURCE_DIR" ]; then
    log "ERROR: sing-box source config directory missing; durable ipv4_only route cannot be enforced"
    return 1
  fi
  if ! source_has_ipv4_only_route; then
    if [ -z "`$ROUTE_BACKUP_DIR" ]; then
      backup_route_state
    fi
    candidate="`$(mktemp "`$SINGBOX_ROUTE_FRAGMENT.tmp.XXXXXX")"
    trap 'rm -f "`$candidate"' RETURN EXIT
    cat > "`$candidate" <<'VPS_IPV4_ONLY_EOF'
{
  "route": {
    "rules": [
      {
        "action": "resolve",
        "strategy": "ipv4_only"
      }
    ]
  }
}
VPS_IPV4_ONLY_EOF
    jq empty "`$candidate"
    chmod --reference="`$SINGBOX_SOURCE_DIR" "`$candidate" 2>/dev/null || chmod 644 "`$candidate"
    chown --reference="`$SINGBOX_SOURCE_DIR" "`$candidate" 2>/dev/null || true
    mv -f "`$candidate" "`$SINGBOX_ROUTE_FRAGMENT"
    candidate=''
    trap - RETURN EXIT
    ROUTE_CHANGED=1
    log "INFO: projected durable ipv4_only route fragment=`$SINGBOX_ROUTE_FRAGMENT"
  fi
  "`$SINGBOX_BINARY" merge config.json -C "`$SINGBOX_SOURCE_DIR/" -D "`$SINGBOX_CONF_DIR/" >> "`$LOG" 2>&1
  jq -e '(.route.rules // []) | any(.action == "resolve" and .strategy == "ipv4_only")' "`$SINGBOX_CONFIG" >/dev/null
}

verify_current_singbox() {
  ensure_ipv4_only_route
  if [ "`$ROUTE_CHANGED" = '1' ]; then
    service_restart sing-box
  fi
  service_is_active sing-box
  "`$SINGBOX_BINARY" check -c "`$SINGBOX_CONFIG" >> "`$LOG" 2>&1
}

verify_target_singbox() {
  [ "`$(current_singbox_version)" = "`$TARGET_VERSION" ]
  [ "`$(sha256sum "`$SINGBOX_BINARY" | awk '{print `$1}')" = "`$EXPECTED_SHA256" ]
  verify_current_singbox
}

restore_singbox() {
  if [ -z "`$BACKUP_DIR" ] || [ ! -f "`$BACKUP_DIR/sing-box" ]; then
    log "ERROR: sing-box rollback backup is missing"
    return 1
  fi
  if ! rm -rf "`$SINGBOX_CONF_DIR" ||
     ! cp -a "`$BACKUP_DIR/conf" "`$SINGBOX_CONF_DIR" ||
     ! cp -a "`$BACKUP_DIR/sing-box" "`$SINGBOX_BINARY"; then
    log "ERROR: restoring pre-update sing-box binary/configuration failed"
    return 1
  fi
  if ! service_restart sing-box; then
    log "ERROR: sing-box restart after rollback failed"
    return 1
  fi
  if ! verify_current_singbox; then
    log "ERROR: sing-box service/config verification after rollback failed"
    return 1
  fi
  UPDATE_STARTED=0
  log "ROLLBACK_VERIFIED backup=`$BACKUP_DIR"
}

log "========== vasma sing-box update start =========="
current_version="`$(current_singbox_version)"
if [ "`$current_version" = "`$TARGET_VERSION" ] && [ "`$(sha256sum "`$SINGBOX_BINARY" | awk '{print `$1}')" = "`$EXPECTED_SHA256" ]; then
  verify_current_singbox
  log "INFO: pinned sing-box version and hash already match; skip reinstall"
  exit 0
fi
if ! latest_version="`$(vasma_visible_stable_singbox_version)"; then
  log "WARN: unable to query latest stable sing-box version; keep and verify current installation"
  verify_current_singbox
  log "========== vasma sing-box update skipped =========="
  exit 0
fi
if [ -z "`$latest_version" ]; then
  log "WARN: vasma-visible stable sing-box version is empty; skip update to avoid empty download URL"
  verify_current_singbox
  log "========== vasma sing-box update skipped =========="
  exit 0
fi
if [ "`$current_version" = "`$latest_version" ]; then
  log "INFO: current sing-box version `$current_version equals vasma-visible latest but hash differs; reinstalling pinned target"
fi
if [ "`$latest_version" != "`$TARGET_VERSION" ]; then
  log "UNVERIFIED: upstream latest `$latest_version does not equal pinned target `$TARGET_VERSION; skip vasma"
  verify_current_singbox
  exit 10
fi
BACKUP_DIR="`$(mktemp -d /var/backups/v2ray-agent-core-update.XXXXXX)"
chmod 700 "`$BACKUP_DIR"
cp -a "`$SINGBOX_BINARY" "`$BACKUP_DIR/sing-box"
cp -a "`$SINGBOX_CONF_DIR" "`$BACKUP_DIR/conf"
UPDATE_STARTED=1
printf '16\n2\n1\ny\n' | "`$VASMA" >> "`$LOG" 2>&1
if ! verify_target_singbox; then
  log "ERROR: pinned sing-box version/hash/config verification failed"
  restore_singbox || log "ROLLBACK_FAILED backup=`$BACKUP_DIR"
  exit 11
fi
UPDATE_STARTED=0
log "========== vasma sing-box update done =========="
EOF
  sed -i "s/__TARGET_VERSION__/`$target_version/; s/__EXPECTED_SHA256__/`$expected_sha256/; s/__EXPECTED_VASMA_SHA256__/`$expected_vasma_sha256/" "`$singbox_script"
  chmod 755 "`$singbox_script"
}

install_cron() {
  tmp="`$(mktemp)"
  current="`$(mktemp)"
  error="`$(mktemp)"
  if ! read_crontab_or_empty "`$current" "`$error"; then
    rm -f "`$tmp" "`$current" "`$error"
    return 1
  fi
  sed -E '/\/etc\/v2ray-agent\/auto_update_(xray|singbox)\.sh/d' "`$current" > "`$tmp"
  if ! crontab "`$tmp"; then
    rm -f "`$tmp" "`$current" "`$error"
    return 1
  fi
  rm -f "`$tmp" "`$current" "`$error"
  printf 'SHELL=/bin/bash\n%s root /bin/bash %s\n' "`$schedule" "`$selected_script" > "`$cron_file"
  chmod 644 "`$cron_file"
}

require_vasma
if [ "`$apply" = '1' ]; then
  require_update_dependencies
  require_vasma_pin
  if [ -z "`$target_version" ] || [ -z "`$expected_sha256" ]; then
    echo 'apply requires a version and SHA-256 pin for the selected core' >&2
    exit 6
  fi
fi

selected_script="`$xray_script"
if [ "`$kernel" = 'sing-box' ]; then
  selected_script="`$singbox_script"
fi

if [ "`$apply" = '1' ]; then
  backup_dir="`$(mktemp -d /var/backups/v2ray-agent-maint.XXXXXX)"
  chmod 700 "`$backup_dir"
  backup_apply_state
  trap rollback_apply ERR INT TERM
  if [ "`$kernel" = 'xray' ]; then
    write_xray_wrapper
    rm -f "`$singbox_script"
  else
    write_singbox_wrapper
    rm -f "`$xray_script"
  fi
  install_cron
  echo "APPLY_BACKUP_DIR=`$backup_dir"
fi

echo '==vasma=='
vasma_probe_path=''
for candidate in /usr/bin/vasma /usr/sbin/vasma; do
  if [ -x "`$candidate" ]; then vasma_probe_path="`$candidate"; break; fi
done
ls -l "`$vasma_probe_path" /etc/v2ray-agent/install.sh 2>/dev/null || true
if [ -n "`$vasma_probe_path" ]; then
  sha256sum "`$vasma_probe_path" 2>/dev/null || true
fi
grep -oE '当前版本：v[0-9.]+' /etc/v2ray-agent/install.sh 2>/dev/null | head -1 || true
if [ -z "`$vasma_probe_path" ] || ! grep -qF 'coreVersionManageMenu' "`$vasma_probe_path" 2>/dev/null; then
  echo 'anchors:missing'
else
  echo 'anchors:present'
fi
echo '==selected-kernel=='
echo "`$kernel"
echo '==cron=='
echo '--crontab-legacy--'
crontab -l 2>/dev/null | sed -n -E '/auto_update_(xray|singbox)\.sh/p' || true
echo "--`$cron_file--"
if [ -e "`$cron_file" ]; then
  cat "`$cron_file"
else
  echo missing
fi
echo '==scripts=='
for f in "`$xray_script" "`$singbox_script"; do
  echo "--`$f--"
  if [ -e "`$f" ]; then
    ls -l "`$f"
    grep -nE 'vasma|printf|github|wget|curl|REPO=|Xray-core|sing-box|Menu path|ipv4_only|verify_vasma_anchors' "`$f" || true
    bash -n "`$f"
    echo syntax-ok
  else
    echo missing
  fi
done
if [ "`$apply" = '1' ]; then
  trap - ERR INT TERM
fi
"@

Invoke-RemoteCommand -Command $remoteCommand

param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [string]$Profile,
  [Parameter(Mandatory = $true)]
  [ValidateSet("xray", "sing-box")]
  [string]$Kernel,
  [string]$Version,
  [string]$Sha256,
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
  if ($Sha256 -notmatch '^(?i:[0-9a-f]{64})$') {
    throw "-Apply requires a 64-hex -Sha256 pin for the selected installed core binary."
  }
}
$targetVersion = if ($normalizedVersion) { "v$normalizedVersion" } else { "" }
$expectedSha256 = if ($Sha256) { $Sha256.ToLowerInvariant() } else { "" }
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
  restore_file "`$xray_script" xray-wrapper || rollback_failed=1
  restore_file "`$singbox_script" singbox-wrapper || rollback_failed=1
  restore_file "`$cron_file" cron-file || rollback_failed=1
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

require_vasma() {
  if [ ! -x /usr/bin/vasma ]; then
    echo 'missing executable /usr/bin/vasma' >&2
    exit 2
  fi
}

require_update_dependencies() {
  for command_name in curl jq sha256sum systemctl flock; do
    if ! command -v "`$command_name" >/dev/null 2>&1; then
      echo "missing dependency: `$command_name" >&2
      exit 3
    fi
  done
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
BACKUP_DIR=""
UPDATE_STARTED=0

log() { echo "[`$(date '+%Y-%m-%d %H:%M:%S')] `$*" >> "`$LOG"; }

recover_on_error() {
  rc="`$?"
  log "ERROR: vasma Xray-core update failed with exit=`$rc; checking xray state"
  restored=0
  if [ "`$UPDATE_STARTED" = '1' ] && [ -n "`$BACKUP_DIR" ] && [ -f "`$BACKUP_DIR/xray" ]; then
    if cp -a "`$BACKUP_DIR/xray" "`$XRAY_BINARY"; then
      restored=1
      log "WARN: restored pre-update Xray binary after failure"
      systemctl restart xray >> "`$LOG" 2>&1 || log "ERROR: xray restart after rollback failed"
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
  elif ! systemctl is-active --quiet xray; then
    log "WARN: xray inactive after failure; trying systemctl start xray"
    systemctl start xray >> "`$LOG" 2>&1 || true
  fi
  exit "`$rc"
}
trap recover_on_error ERR

exec 9>"`$LOCK_FILE"
if ! flock -n 9; then
  log "INFO: another maintenance/update job is already running; exit"
  exit 0
fi

if [ ! -x /usr/bin/vasma ]; then
  log "ERROR: /usr/bin/vasma not executable"
  exit 1
fi
if [ ! -x "`$XRAY_BINARY" ] || [ ! -d "`$XRAY_CONFDIR" ]; then
  log "ERROR: v2ray-agent Xray layout not detected; refusing vasma compatibility path"
  exit 4
fi
if [ -z "`$TARGET_VERSION" ] || [ -z "`$EXPECTED_SHA256" ]; then
  log "ERROR: no version/SHA-256 pin was projected; vasma update refused"
  exit 5
fi

current_xray_version() {
  "`$XRAY_BINARY" --version | awk 'NR == 1 { print "v" `$2 }'
}

vasma_visible_stable_xray_version() {
  curl -fsSL --connect-timeout 10 --max-time 30 "https://api.github.com/repos/XTLS/Xray-core/releases/latest" |
    jq -r '.tag_name // empty'
}

verify_current_xray() {
  systemctl is-active --quiet xray
  "`$XRAY_BINARY" run -test -confdir "`$XRAY_CONFDIR" >> "`$LOG" 2>&1
}

verify_target_xray() {
  [ "`$(current_xray_version)" = "`$TARGET_VERSION" ]
  [ "`$(sha256sum "`$XRAY_BINARY" | awk '{print `$1}')" = "`$EXPECTED_SHA256" ]
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
  if ! systemctl restart xray; then
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
printf '16\n1\n1\ny\n' | /usr/bin/vasma >> "`$LOG" 2>&1
if ! verify_target_xray; then
  log "ERROR: pinned Xray version/hash/config verification failed"
  restore_xray || log "ROLLBACK_FAILED backup=`$BACKUP_DIR"
  exit 11
fi
UPDATE_STARTED=0
log "========== vasma Xray-core update done =========="
EOF
  sed -i "s/__TARGET_VERSION__/`$target_version/; s/__EXPECTED_SHA256__/`$expected_sha256/" "`$xray_script"
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
SINGBOX_BINARY="/etc/v2ray-agent/sing-box/sing-box"
TARGET_VERSION="__TARGET_VERSION__"
EXPECTED_SHA256="__EXPECTED_SHA256__"
BACKUP_DIR=""
UPDATE_STARTED=0

log() { echo "[`$(date '+%Y-%m-%d %H:%M:%S')] `$*" >> "`$LOG"; }

recover_on_error() {
  rc="`$?"
  log "ERROR: vasma sing-box update failed with exit=`$rc; checking sing-box state"
  restored=0
  if [ "`$UPDATE_STARTED" = '1' ] && [ -n "`$BACKUP_DIR" ] && [ -f "`$BACKUP_DIR/sing-box" ]; then
    if cp -a "`$BACKUP_DIR/sing-box" "`$SINGBOX_BINARY"; then
      restored=1
      log "WARN: restored pre-update sing-box binary after failure"
      systemctl restart sing-box >> "`$LOG" 2>&1 || log "ERROR: sing-box restart after rollback failed"
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
  elif ! systemctl is-active --quiet sing-box; then
    log "WARN: sing-box inactive after failure; trying systemctl start sing-box"
    systemctl start sing-box >> "`$LOG" 2>&1 || true
  fi
  exit "`$rc"
}
trap recover_on_error ERR

exec 9>"`$LOCK_FILE"
if ! flock -n 9; then
  log "INFO: another maintenance/update job is already running; exit"
  exit 0
fi

if [ ! -x /usr/bin/vasma ]; then
  log "ERROR: /usr/bin/vasma not executable"
  exit 1
fi
if [ ! -x "`$SINGBOX_BINARY" ] || [ ! -f "`$SINGBOX_CONFIG" ]; then
  log "ERROR: v2ray-agent sing-box layout not detected; refusing vasma compatibility path"
  exit 4
fi
if [ -z "`$TARGET_VERSION" ] || [ -z "`$EXPECTED_SHA256" ]; then
  log "ERROR: no version/SHA-256 pin was projected; vasma update refused"
  exit 5
fi

current_singbox_version() {
  "`$SINGBOX_BINARY" version | awk '/^sing-box version/ { print "v" `$3 }'
}

vasma_visible_stable_singbox_version() {
  curl -fsSL --connect-timeout 10 --max-time 30 "https://api.github.com/repos/SagerNet/sing-box/releases/latest" |
    jq -r '.tag_name // empty'
}

ensure_ipv4_only_route() {
  ROUTE_CHANGED=0
  if jq -e '(.route.rules // []) | any(.action == "resolve" and .strategy == "ipv4_only")' "`$SINGBOX_CONFIG" >/dev/null; then
    return 0
  fi

  backup="`${SINGBOX_CONFIG}.pre-ipv4-only.`$(date -u '+%Y%m%dT%H%M%SZ')"
  candidate="`$(mktemp "`${SINGBOX_CONFIG}.tmp.XXXXXX")"
  trap 'rm -f "`$candidate"' RETURN EXIT
  cp -a "`$SINGBOX_CONFIG" "`$backup"
  jq '.route.rules = ((.route.rules // []) + [{"action":"resolve","strategy":"ipv4_only"}])' "`$SINGBOX_CONFIG" > "`$candidate"
  chmod --reference="`$SINGBOX_CONFIG" "`$candidate"
  chown --reference="`$SINGBOX_CONFIG" "`$candidate"
  "`$SINGBOX_BINARY" check -c "`$candidate" >> "`$LOG" 2>&1
  mv -f "`$candidate" "`$SINGBOX_CONFIG"
  candidate=''
  trap - RETURN EXIT
  ROUTE_CHANGED=1
  log "INFO: restored ipv4_only route; backup=`$backup"
}

verify_current_singbox() {
  ensure_ipv4_only_route
  if [ "`$ROUTE_CHANGED" = '1' ]; then
    systemctl restart sing-box
  fi
  systemctl is-active --quiet sing-box
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
  if ! cp -a "`$BACKUP_DIR/sing-box" "`$SINGBOX_BINARY"; then
    log "ERROR: restoring pre-update sing-box binary failed"
    return 1
  fi
  if ! systemctl restart sing-box; then
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
UPDATE_STARTED=1
printf '16\n2\n1\ny\n' | /usr/bin/vasma >> "`$LOG" 2>&1
if ! verify_target_singbox; then
  log "ERROR: pinned sing-box version/hash/config verification failed"
  restore_singbox || log "ROLLBACK_FAILED backup=`$BACKUP_DIR"
  exit 11
fi
UPDATE_STARTED=0
log "========== vasma sing-box update done =========="
EOF
  sed -i "s/__TARGET_VERSION__/`$target_version/; s/__EXPECTED_SHA256__/`$expected_sha256/" "`$singbox_script"
  chmod 755 "`$singbox_script"
}

install_cron() {
  tmp="`$(mktemp)"
  crontab -l 2>/dev/null | grep -v -E '/etc/v2ray-agent/auto_update_(xray|singbox)\.sh' > "`$tmp" || true
  crontab "`$tmp"
  rm -f "`$tmp"
  printf 'SHELL=/bin/bash\n%s root /bin/bash %s\n' "`$schedule" "`$selected_script" > "`$cron_file"
  chmod 644 "`$cron_file"
}

require_vasma
if [ "`$apply" = '1' ]; then
  require_update_dependencies
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
ls -l /usr/bin/vasma /etc/v2ray-agent/install.sh 2>/dev/null || true
echo '==selected-kernel=='
echo "`$kernel"
echo '==cron=='
echo '--crontab-legacy--'
crontab -l 2>/dev/null | grep -E 'auto_update_(xray|singbox)\.sh' || true
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
    grep -nE 'vasma|printf|github|wget|curl|REPO=|Xray-core|sing-box|Menu path|ipv4_only' "`$f" || true
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

#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg")]
  [string]$Profile,
  [string]$Schedule = "30 1 * * *",
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
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}

$scheduleLiteral = $Schedule.Trim()
$wrapperPath = "/usr/local/sbin/vps-launcher-v2ray-agent-renewtls.sh"
$cronPath = "/etc/cron.d/vps-launcher-v2ray-agent-renewtls"
$lockPath = "/run/vps-ssh-launcher-maintenance.lock"
$legacyPattern = "/etc/v2ray-agent/install.sh RenewTLS"
$wrapperText = @'
#!/usr/bin/env bash
# Run v2ray-agent certificate renewal under the shared maintenance lock.
set -Eeuo pipefail
LOCK_FILE="/run/vps-ssh-launcher-maintenance.lock"
LOG_FILE="/etc/v2ray-agent/crontab_tls.log"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    printf '[%s] DEFERRED_BUSY: another maintenance/update job is running\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 75
fi
exec /bin/bash /etc/v2ray-agent/install.sh RenewTLS >> "$LOG_FILE" 2>&1
'@.Replace("`r`n", "`n").Replace("`r", "`n")
$wrapperBytes = [Text.Encoding]::UTF8.GetBytes($wrapperText)
$wrapperBase64 = [Convert]::ToBase64String($wrapperBytes)
$wrapperSha256 = ([BitConverter]::ToString([Security.Cryptography.SHA256]::HashData($wrapperBytes))).Replace("-", "").ToLowerInvariant()

$remoteCommand = @"
set -Eeuo pipefail
apply='$([int]$Apply.IsPresent)'
schedule='$scheduleLiteral'
payload='$wrapperBase64'
payload_sha='$wrapperSha256'
wrapper='$wrapperPath'
cron_file='$cronPath'
lock_file='$lockPath'
legacy_pattern='$legacyPattern'

read_crontab_or_empty() {
  output_path="`$1"
  error_path="`$2"
  if crontab -l > "`$output_path" 2>"`$error_path"; then
    return 0
  fi
  if grep -qiE 'no crontab for ' "`$error_path"; then
    : > "`$output_path"
    return 0
  fi
  cat "`$error_path" >&2 || true
  return 1
}

write_payload() {
  tmp="`$(mktemp "`$wrapper.XXXXXX")"
  if ! printf '%s' "`$payload" | base64 -d > "`$tmp"; then
    rm -f -- "`$tmp"
    return 1
  fi
  chmod 755 "`$tmp"
  chown root:root "`$tmp"
  if [ "`$(sha256sum "`$tmp" | awk '{print `$1}')" != "`$payload_sha" ]; then
    rm -f -- "`$tmp"
    echo 'PROJECTION_HASH_MISMATCH'
    return 1
  fi
  mv -f -- "`$tmp" "`$wrapper"
}

verify_runtime() {
  if [ ! -x "`$wrapper" ] || [ ! -f "`$cron_file" ]; then
    echo 'RUNTIME_VERIFY_FAILED renewtls_files'
    return 1
  fi
  bash -n "`$wrapper"
  grep -qF "`$wrapper" "`$cron_file"
  if crontab -l 2>/dev/null | grep -Fq "`$legacy_pattern"; then
    echo 'RUNTIME_VERIFY_FAILED legacy_renewtls_cron'
    return 1
  fi
  echo 'RUNTIME_VERIFY_OK'
}

if [ "`$apply" = '0' ]; then
  echo '==renewtls-wrapper=='
  if [ -e "`$wrapper" ]; then
    stat -c '%a %U %G %s %n' "`$wrapper"
    sha256sum "`$wrapper"
    bash -n "`$wrapper" && echo syntax=OK || echo syntax=FAIL
  else
    echo missing
  fi
  echo '==renewtls-cron=='
  if [ -e "`$cron_file" ]; then cat "`$cron_file"; else echo missing; fi
  echo '==legacy-root-cron=='
  crontab -l 2>/dev/null | grep -F "`$legacy_pattern" || true
  if [ -e "`$wrapper" ] && [ -e "`$cron_file" ]; then
    verify_runtime
  else
    echo 'RUNTIME_VERIFY_NOT_PROJECTED'
  fi
  exit 0
fi

exec 9>"`$lock_file"
if ! flock -n 9; then
  echo "REFUSE busy lock=`$lock_file"
  exit 75
fi
if [ "`$(id -u)" != '0' ]; then
  echo 'REFUSE root_required'
  exit 4
fi

backup_dir="`$(mktemp -d /var/backups/v2ray-agent-renewtls-deploy.XXXXXX)"
chmod 700 "`$backup_dir"
if [ -e "`$wrapper" ]; then cp -a -- "`$wrapper" "`$backup_dir/wrapper"; else : > "`$backup_dir/wrapper.missing"; fi
if [ -e "`$cron_file" ]; then cp -a -- "`$cron_file" "`$backup_dir/cron"; else : > "`$backup_dir/cron.missing"; fi
if read_crontab_or_empty "`$backup_dir/crontab" "`$backup_dir/crontab.error"; then
  if grep -qiE '^no crontab for ' "`$backup_dir/crontab.error" 2>/dev/null; then
    : > "`$backup_dir/crontab.missing"
  fi
  rm -f "`$backup_dir/crontab.error"
else
  echo 'BACKUP_CRONTAB_FAILED'
  exit 6
fi

rollback() {
  set +e
  failed=0
  if [ -f "`$backup_dir/wrapper.missing" ]; then rm -f -- "`$wrapper" || failed=1; else cp -a -- "`$backup_dir/wrapper" "`$wrapper" || failed=1; fi
  if [ -f "`$backup_dir/cron.missing" ]; then rm -f -- "`$cron_file" || failed=1; else cp -a -- "`$backup_dir/cron" "`$cron_file" || failed=1; fi
  if [ -f "`$backup_dir/crontab.missing" ]; then crontab -r >/dev/null 2>&1 || true; else crontab "`$backup_dir/crontab" || failed=1; fi
  verify_rollback_state() {
    local rollback_tmp
    if [ -f "`$backup_dir/wrapper.missing" ]; then
      [ ! -e "`$wrapper" ] || return 1
    else
      cmp -s "`$backup_dir/wrapper" "`$wrapper" || return 1
    fi
    if [ -f "`$backup_dir/cron.missing" ]; then
      [ ! -e "`$cron_file" ] || return 1
    else
      cmp -s "`$backup_dir/cron" "`$cron_file" || return 1
    fi
    rollback_tmp="`$(mktemp)"
    if [ -f "`$backup_dir/crontab.missing" ]; then
      if crontab -l > "`$rollback_tmp" 2>/dev/null; then
        rm -f -- "`$rollback_tmp"
        return 1
      fi
    else
      if ! crontab -l > "`$rollback_tmp" 2>/dev/null || ! cmp -s "`$backup_dir/crontab" "`$rollback_tmp"; then
        rm -f -- "`$rollback_tmp"
        return 1
      fi
    fi
    rm -f -- "`$rollback_tmp"
    return 0
  }
  if [ "`$failed" -eq 0 ] && verify_rollback_state; then
    echo "ROLLBACK_VERIFIED backup=`$backup_dir"
  else
    echo "ROLLBACK_FAILED backup=`$backup_dir"
  fi
  return 0
}
rollback_on_exit() {
  rc="`$?"
  trap - EXIT INT TERM
  if [ "`$rc" -ne 0 ]; then rollback; fi
  exit "`$rc"
}
trap rollback_on_exit EXIT INT TERM

write_payload
tmp_cron="`$(mktemp "`$cron_file.XXXXXX")"
printf 'SHELL=/bin/bash\n%s root /bin/bash %s\n' "`$schedule" "`$wrapper" > "`$tmp_cron"
chmod 644 "`$tmp_cron"
chown root:root "`$tmp_cron"
mv -f -- "`$tmp_cron" "`$cron_file"
tmp_crontab="`$(mktemp)"
sed -E '/\/etc\/v2ray-agent\/install\.sh[[:space:]]+RenewTLS/d' "`$backup_dir/crontab" > "`$tmp_crontab"
crontab "`$tmp_crontab"
rm -f -- "`$tmp_crontab"
verify_runtime
trap - EXIT INT TERM
echo "APPLY_BACKUP_DIR=`$backup_dir"
echo "WRAPPER_SHA256=`$(sha256sum "`$wrapper" | awk '{print `$1}')"
echo 'RENEWTLS_LOCKED_PROJECTED'
"@

Invoke-RemoteCommand -Command $remoteCommand

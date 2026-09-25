#requires -Version 7
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [string]$Profile,
  [string]$InstallSha256,
  [string]$Schedule = "40 14 * * 5",
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
if ($InstallSha256 -and $InstallSha256 -notmatch '^(?i:[0-9a-f]{64})$') {
  throw "-InstallSha256 must contain exactly 64 hexadecimal characters."
}
if ($Apply -and -not $InstallSha256) {
  throw "-Apply requires -InstallSha256 from the fresh read-only host probe."
}

$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}

$sourcePath = Join-Path $repoRoot "scripts\remote\v2ray-agent-script-update.sh"
if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
  throw "Updater source was not found at $sourcePath"
}
$sourceText = (Get-Content -LiteralPath $sourcePath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$sourceBytes = [Text.Encoding]::UTF8.GetBytes($sourceText)
$sourceSha256 = ([BitConverter]::ToString([Security.Cryptography.SHA256]::HashData($sourceBytes))).Replace("-", "").ToLowerInvariant()
$sourceBase64 = [Convert]::ToBase64String($sourceBytes)
$scheduleLiteral = $Schedule.Trim()
$installShaLiteral = if ($InstallSha256) { $InstallSha256.ToLowerInvariant() } else { "" }

$remoteScriptPath = "/usr/local/sbin/vps-launcher-v2ray-agent-update.sh"
$cronPath = "/etc/cron.d/vps-launcher-v2ray-agent-update"
$lockPath = "/run/vps-ssh-launcher-maintenance.lock"

$remoteCommand = @"
set -Eeuo pipefail
apply='$([int]$Apply.IsPresent)'
schedule='$scheduleLiteral'
expected_install_sha='$installShaLiteral'
payload='$sourceBase64'
payload_sha='$sourceSha256'
remote_script='$remoteScriptPath'
cron_file='$cronPath'
lock_file='$lockPath'

write_payload() {
  tmp="`$(mktemp "`$remote_script.XXXXXX")"
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
  mv -f -- "`$tmp" "`$remote_script"
}

service_is_present_and_active() {
  if command -v systemctl >/dev/null 2>&1 && systemctl cat "`$1" >/dev/null 2>&1; then
    systemctl is-active --quiet "`$1"
  elif command -v rc-service >/dev/null 2>&1; then
    rc-service "`$1" status >/dev/null 2>&1
  else
    return 0
  fi
}

verify_runtime() {
  for service in xray nginx fail2ban; do
    service_is_present_and_active "`$service" || {
      echo "RUNTIME_VERIFY_FAILED service=`$service"
      return 1
    }
  done
  if [ -x /etc/v2ray-agent/xray/xray ] && [ -d /etc/v2ray-agent/xray/conf ]; then
    /etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf >/dev/null 2>&1 || {
      echo 'RUNTIME_VERIFY_FAILED xray_config'
      return 1
    }
  fi
  echo 'RUNTIME_VERIFY_OK'
}

if [ "`$apply" = '0' ]; then
  echo '==v2ray-agent-script-updater=='
  if [ -e "`$remote_script" ]; then
    stat -c '%a %U %G %s %n' "`$remote_script"
    sha256sum "`$remote_script"
    bash -n "`$remote_script" && echo syntax=OK || echo syntax=FAIL
  else
    echo missing
  fi
  echo '==cron=='
  if [ -e "`$cron_file" ]; then cat "`$cron_file"; else echo missing; fi
  echo '==install-script=='
  sha256sum /etc/v2ray-agent/install.sh
  grep -oE '当前版本：v[0-9.]+' /etc/v2ray-agent/install.sh | head -1 || true
  verify_runtime
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
if [ ! -f /etc/v2ray-agent/install.sh ] || [ -L /etc/v2ray-agent/install.sh ]; then
  echo 'REFUSE install_script_missing_or_symlink'
  exit 5
fi
actual_install_sha="`$(sha256sum /etc/v2ray-agent/install.sh | awk '{print `$1}')"
if [ -n "`$expected_install_sha" ] && [ "`$actual_install_sha" != "`$expected_install_sha" ]; then
  echo "REFUSE install_sha_mismatch expected=`$expected_install_sha actual=`$actual_install_sha"
  exit 13
fi

backup_dir="`$(mktemp -d /var/backups/v2ray-agent-script-update-deploy.XXXXXX)"
chmod 700 "`$backup_dir"
if [ -e "`$remote_script" ]; then cp -a -- "`$remote_script" "`$backup_dir/updater.sh"; else : > "`$backup_dir/updater.sh.missing"; fi
if [ -e "`$cron_file" ]; then cp -a -- "`$cron_file" "`$backup_dir/cron"; else : > "`$backup_dir/cron.missing"; fi

rollback() {
  set +e
  failed=0
  if [ -f "`$backup_dir/updater.sh.missing" ]; then rm -f -- "`$remote_script" || failed=1; else cp -a -- "`$backup_dir/updater.sh" "`$remote_script" || failed=1; fi
  if [ -f "`$backup_dir/cron.missing" ]; then rm -f -- "`$cron_file" || failed=1; else cp -a -- "`$backup_dir/cron" "`$cron_file" || failed=1; fi
  if [ "`$failed" -eq 0 ] && bash -n "`$remote_script" 2>/dev/null; then
    echo "ROLLBACK_VERIFIED backup=`$backup_dir"
  else
    echo "ROLLBACK_FAILED backup=`$backup_dir"
  fi
  return 0
}
rollback_on_exit() {
  rc="`$?"
  trap - EXIT INT TERM
  if [ "`$rc" -ne 0 ]; then
    rollback
  fi
  exit "`$rc"
}
trap rollback_on_exit EXIT INT TERM

write_payload
for anchor in \
  'https://raw.githubusercontent.com/mack-a/v2ray-agent/master/install.sh' \
  'coreVersionManageMenu' \
  'xrayVersionManageMenu' \
  '17.更新脚本'; do
  grep -qF "`$anchor" "`$remote_script" || { echo "PROJECTION_ANCHOR_MISSING=`$anchor"; exit 11; }
done
bash -n "`$remote_script"
tmp_cron="`$(mktemp "`$cron_file.XXXXXX")"
printf 'SHELL=/bin/bash\n%s root /bin/bash %s --apply\n' "`$schedule" "`$remote_script" > "`$tmp_cron"
chmod 644 "`$tmp_cron"
chown root:root "`$tmp_cron"
mv -f -- "`$tmp_cron" "`$cron_file"
if ! verify_runtime; then
  echo 'PROJECTION_RUNTIME_FAILED'
  exit 12
fi
trap - EXIT INT TERM
echo "APPLY_BACKUP_DIR=`$backup_dir"
echo "UPDATER_SHA256=`$(sha256sum "`$remote_script" | awk '{print `$1}')"
echo "INSTALL_SHA256=`$actual_install_sha"
echo 'UPDATER_PROJECTED'
"@

Invoke-RemoteCommand -Command $remoteCommand

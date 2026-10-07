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

Initialize-WindowsProcessEnvironment
if ($Schedule -notmatch '^[0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+$') {
  throw "Schedule must be a five-field cron expression."
}
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
$sourcePinPath = Join-Path $repoRoot "scripts\remote\v2ray-agent-source-pin.json"
if (-not (Test-Path -LiteralPath $sourcePinPath -PathType Leaf)) {
  throw "v2ray-agent source pin was not found at $sourcePinPath"
}
$sourcePin = Get-Content -LiteralPath $sourcePinPath -Raw | ConvertFrom-Json
$sourceRef = [string]$sourcePin.ref
$sourceInstallSha = [string]$sourcePin.install_sha256
$sourceRepository = [string]$sourcePin.repository
if ($sourceRepository -ne "https://github.com/mack-a/v2ray-agent") {
  throw "Unexpected v2ray-agent source repository in $sourcePinPath"
}
if ($sourceRef -notmatch '^[0-9a-f]{40}$') {
  throw "v2ray-agent source pin ref must be a 40-hex commit SHA."
}
if ($sourceInstallSha -notmatch '^(?i:[0-9a-f]{64})$') {
  throw "v2ray-agent source pin install_sha256 must be a 64-hex SHA-256."
}
$sourceText = (Get-Content -LiteralPath $sourcePath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$sourceRefAnchor = 'SOURCE_REF="' + $sourceRef + '"'
$sourceShaAnchor = 'EXPECTED_CANDIDATE_SHA="' + $sourceInstallSha.ToLowerInvariant() + '"'
if ($sourceText -notmatch [regex]::Escape($sourceRefAnchor) -or
    $sourceText -notmatch [regex]::Escape($sourceShaAnchor)) {
  throw "Updater source does not match the committed v2ray-agent source pin."
}
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
  if command -v systemctl >/dev/null 2>&1; then
    systemctl cat "`$1" >/dev/null 2>&1 || return 1
    systemctl is-active --quiet "`$1"
  elif command -v rc-service >/dev/null 2>&1; then
    rc-service "`$1" status >/dev/null 2>&1
  else
    return 1
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
      -name 'v2ray-agent-script-update-deploy.*' -printf '%T@ %p\n' |
      sort -rn | tail -n +`$((keep + 1)) | cut -d' ' -f2-
  )
  echo "PRUNE scope=script_update_deploy_backups removed=`$removed policy=keep_`$keep"
}

if [ "`$apply" = '0' ]; then
  echo '==v2ray-agent-script-updater=='
  if [ -e "`$remote_script" ]; then
    stat -c '%a %U %G %s %n' "`$remote_script"
    sha256sum "`$remote_script"
    bash -n "`$remote_script" && echo syntax=OK || echo syntax=FAIL
    grep -E '^(SOURCE_REPOSITORY|SOURCE_REF|EXPECTED_CANDIDATE_SHA)=' "`$remote_script" || true
  else
    echo missing
  fi
  echo '==cron=='
  if [ -e "`$cron_file" ]; then cat "`$cron_file"; else echo missing; fi
  echo '==install-script=='
  # A read-only probe must complete on any host, including one that never had
  # vasma installed: a missing install.sh or an inactive proxy service is a
  # reported state, not a probe failure. The markers stay greppable.
  if [ -e /etc/v2ray-agent/install.sh ]; then
    sha256sum /etc/v2ray-agent/install.sh || true
    grep -oE '当前版本：v[0-9.]+' /etc/v2ray-agent/install.sh | head -1 || true
  else
    echo missing
  fi
  verify_runtime || echo 'RUNTIME_VERIFY_NONFATAL_READ_ONLY'
  echo '==update-log=='
  # Same redaction-first rule as the kernel lane: surface only the updater's
  # own structured markers so a failed weekly run stops aging silently in a
  # remote log no gate reads. The raw transcript is never echoed.
  if [ -e /var/log/vps-launcher-v2ray-agent-update.log ]; then
    grep -E 'START mode=|CANDIDATE |APPLIED |NO_CHANGE|CHECK_NO_CHANGE|CHECK_UPDATE_AVAILABLE|REFUSE |DEFERRED_BUSY|UNVERIFIED |VERIFY_FAILED|SERVICES_OK|ROLLBACK_|ERROR ' /var/log/vps-launcher-v2ray-agent-update.log 2>/dev/null | tail -n 8 || true
  else
    echo missing
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
  '$sourceRefAnchor' \
  '$sourceShaAnchor' \
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
prune_deploy_backups
echo 'UPDATER_PROJECTED'
"@

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand

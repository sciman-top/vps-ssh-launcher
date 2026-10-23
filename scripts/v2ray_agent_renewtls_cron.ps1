#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [string]$Schedule = "30 1 * * *",
  [switch]$Apply
)

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

$scheduleLiteral = $Schedule.Trim()
$wrapperPath = "/usr/local/sbin/vps-launcher-v2ray-agent-renewtls.sh"
$cronPath = "/etc/cron.d/vps-launcher-v2ray-agent-renewtls"
$lockPath = "/run/vps-ssh-launcher-maintenance.lock"
$legacyPattern = "/etc/v2ray-agent/install.sh RenewTLS"
# Per-profile certificate facts. zz differs from bwg in three ways: the acme
# entry is the single hostname (not the wildcard), the gateway certificate is
# also consumed by sing-box (hysteria2), so a renewed file must reload nginx
# AND restart sing-box exactly once, and the live-load probe targets the
# nginx reality-handshake listener instead of the 8443 gateway port.
$tlsProfile = @{
  "bwg" = [pscustomobject]@{ CertPath = "/etc/v2ray-agent/tls/fq.sciman.top.crt"; AcmeDomain = '*.sciman.top' }
  "zz"  = [pscustomobject]@{ CertPath = "/etc/v2ray-agent/tls/zz.sciman.top.crt"; AcmeDomain = "zz.sciman.top" }
}
$tls = $tlsProfile[$Profile]
$commonText = @'
#!/usr/bin/env bash
# Run v2ray-agent certificate renewal under the shared maintenance lock.
set -Eeuo pipefail
LOCK_FILE="/run/vps-ssh-launcher-maintenance.lock"
LOG_FILE="/etc/v2ray-agent/crontab_tls.log"
STATUS_DIR="/var/lib/vps-ssh-launcher/maintenance-status"
STATUS_FILE="$STATUS_DIR/renewtls.status"
STATUS_STARTED_AT="$(date -u +%FT%TZ)"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=renewtls\n'
        printf 'started_at=%s\n' "$STATUS_STARTED_AT"
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

status_on_exit() {
    local code="$?" result=failed
    if [ "$code" -eq 0 ]; then result=success; fi
    if [ "$code" -eq 75 ]; then result=busy; fi
    write_status "$result" "$code"
    exit "$code"
}
trap status_on_exit EXIT
write_status running 0

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    printf '[%s] DEFERRED_BUSY: another maintenance/update job is running\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 75
fi
# acme.sh returns 2 for a normal renewal skip, and nonzero errors must
# survive the legacy RenewTLS backstop and the EXIT heartbeat.
ACME_HOME="/root/.acme.sh"
GATEWAY_CERT="__CERT_PATH__"
# Both are required for this reviewed early-renewal path. Missing tools or
# certificate state must be visible rather than deferred until expiry day.
if [ ! -x "$ACME_HOME/acme.sh" ] || [ ! -f "$GATEWAY_CERT" ]; then
    printf 'RENEWTLS_FAILED stage=prerequisite\n' >> "$LOG_FILE"
    exit 4
fi
renew_rc=0
"$ACME_HOME/acme.sh" --renew -d '__ACME_DOMAIN__' --ecc >> "$LOG_FILE" 2>&1 || renew_rc=$?
case "$renew_rc" in
    0|2) : ;;
    *) printf 'RENEWTLS_FAILED stage=acme exit=%s\n' "$renew_rc" >> "$LOG_FILE"; exit "$renew_rc" ;;
esac
/bin/bash /etc/v2ray-agent/install.sh RenewTLS >> "$LOG_FILE" 2>&1
openssl x509 -in "$GATEWAY_CERT" -noout -checkend 604800 >> "$LOG_FILE" 2>&1

__RELOAD_SECTION__

'@.Replace("`r`n", "`n").Replace("`r", "`n")

$bwgReloadSection = @'
verify_loaded_certificate() {
    python3 - "$GATEWAY_CERT" "$1" <<'PY'
import hashlib
import socket
import ssl
import subprocess
import sys
import time

expected = hashlib.sha256(subprocess.check_output(
    ["openssl", "x509", "-in", sys.argv[1], "-outform", "DER"]
)).digest()
context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE
deadline = time.monotonic() + int(sys.argv[2])
while True:
    try:
        with socket.create_connection(("127.0.0.1", 8443), timeout=3) as sock:
            with context.wrap_socket(sock, server_hostname="fq.sciman.top") as tls:
                if hashlib.sha256(tls.getpeercert(binary_form=True)).digest() == expected:
                    raise SystemExit(0)
    except (OSError, ssl.SSLError):
        pass
    if time.monotonic() >= deadline:
        raise SystemExit(1)
    time.sleep(1)
PY
}
# Retry a previously failed reload even if today's renewal changed no file.
if ! verify_loaded_certificate 0; then
    nginx -t >> "$LOG_FILE" 2>&1
    nginx -s reload >> "$LOG_FILE" 2>&1
    verify_loaded_certificate 30
    printf 'RENEWTLS_LOADED_CERT_VERIFIED\n' >> "$LOG_FILE"
fi
'@

$zzReloadSection = @'
verify_loaded_certificate() {
    python3 - "$GATEWAY_CERT" "$1" <<'PY'
import hashlib
import socket
import ssl
import subprocess
import sys
import time

expected = hashlib.sha256(subprocess.check_output(
    ["openssl", "x509", "-in", sys.argv[1], "-outform", "DER"]
)).digest()
context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE
deadline = time.monotonic() + int(sys.argv[2])
while True:
    try:
        with socket.create_connection(("127.0.0.1", 35622), timeout=3) as sock:
            with context.wrap_socket(sock, server_hostname="zz.sciman.top") as tls:
                if hashlib.sha256(tls.getpeercert(binary_form=True)).digest() == expected:
                    raise SystemExit(0)
    except (OSError, ssl.SSLError):
        pass
    if time.monotonic() >= deadline:
        raise SystemExit(1)
    time.sleep(1)
PY
}
# nginx and sing-box both consume the gateway certificate. The state file
# records the last sha applied to both consumers: a renewed file triggers
# exactly one reload/restart, while a missing baseline is recorded without a
# restart only when both consumers provably serve the file certificate.
state_file="/var/lib/vps-ssh-launcher/maintenance-status/renewtls-applied.sha"
file_sha="$(sha256sum "$GATEWAY_CERT" | awk '{print $1}')"
loaded_ok=0
core_ok=0
core_fresh=0
if verify_loaded_certificate 0; then
    loaded_ok=1
fi
if systemctl is-active --quiet sing-box; then
    core_ok=1
    # is-active alone cannot prove the running process serves the file cert:
    # a file written after sing-box started would be baselined while the old
    # certificate stays in memory until the next real renewal. stat(1) on
    # /proc/<pid> reports the process start in epoch seconds, so a file older
    # than the process is provably loaded, and anything else takes the
    # reload/restart path instead of being frozen in as a baseline.
    core_pid="$(systemctl show -p MainPID --value sing-box 2>/dev/null || true)"
    core_started_at="$(stat -c %Y "/proc/$core_pid" 2>/dev/null || echo 0)"
    cert_mtime="$(stat -c %Y "$GATEWAY_CERT" 2>/dev/null || echo 0)"
    if [ "$cert_mtime" -gt 0 ] && [ "$cert_mtime" -le "$core_started_at" ]; then
        core_fresh=1
    fi
fi
state_sha=""
if [ -f "$state_file" ]; then
    state_sha="$(cat "$state_file" 2>/dev/null || true)"
fi
if [ "$loaded_ok" -eq 1 ] && [ "$core_ok" -eq 1 ] && [ "$core_fresh" -eq 1 ] && [ -z "$state_sha" ]; then
    printf '%s\n' "$file_sha" > "$state_file"
    chmod 600 "$state_file"
    printf 'RENEWTLS_BASELINED sha=%s\n' "$file_sha" >> "$LOG_FILE"
elif [ "$loaded_ok" -eq 0 ] || [ "$core_ok" -eq 0 ] || [ "$core_fresh" -eq 0 ] || [ "$state_sha" != "$file_sha" ]; then
    nginx -t >> "$LOG_FILE" 2>&1
    nginx -s reload >> "$LOG_FILE" 2>&1
    systemctl restart sing-box >> "$LOG_FILE" 2>&1
    systemctl is-active --quiet sing-box
    verify_loaded_certificate 30
    printf '%s\n' "$file_sha" > "$state_file"
    chmod 600 "$state_file"
    printf 'RENEWTLS_LOADED_CERT_VERIFIED\n' >> "$LOG_FILE"
fi
'@

$reloadSection = if ($Profile -eq "zz") { $zzReloadSection } else { $bwgReloadSection }
$wrapperText = $commonText.Replace("__CERT_PATH__", $tls.CertPath).Replace("__ACME_DOMAIN__", $tls.AcmeDomain).Replace("__RELOAD_SECTION__", ($reloadSection -replace "`r`n", "`n").Replace("`r", "`n")).Replace("`r`n", "`n").Replace("`r", "`n")
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
      -name 'v2ray-agent-renewtls-deploy.*' -printf '%T@ %p\n' |
      sort -rn | tail -n +`$((keep + 1)) | cut -d' ' -f2-
  )
  echo "PRUNE scope=renewtls_deploy_backups removed=`$removed policy=keep_`$keep"
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
  echo '==renewtls-log=='
  # vasma's RenewTLS transcript can carry domain details, so only the file's
  # freshness is reported: a log that has not been touched in weeks means the
  # renewal cron is not firing. No content is echoed.
  if [ -e /etc/v2ray-agent/crontab_tls.log ]; then
    echo "renewtls_log_age_days=`$(( ( `$(date +%s) - `$(stat -c %Y /etc/v2ray-agent/crontab_tls.log) ) / 86400 ))"
  else
    echo missing
  fi
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
trap rollback_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

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
prune_deploy_backups
echo 'RENEWTLS_LOCKED_PROJECTED'
"@

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand

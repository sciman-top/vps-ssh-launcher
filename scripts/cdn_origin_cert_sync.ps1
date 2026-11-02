#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [ValidateSet("zz")]
  [string]$Profile = "zz",
  [string]$SourceProfile = "bwg",
  [string]$SourceCert = "/etc/v2ray-agent/tls/fq.sciman.top.crt",
  [string]$SourceKey = "/etc/v2ray-agent/tls/fq.sciman.top.key",
  [string]$TargetCert = "/etc/v2ray-agent/tls/cdn.zz.sciman.top.crt",
  [string]$TargetKey = "/etc/v2ray-agent/tls/cdn.zz.sciman.top.key",
  [string]$VerifyHost = "zzcdn.sciman.top",
  [int]$VerifyPort = 2087,
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

Initialize-WindowsProcessEnvironment
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}
foreach ($path in @($SourceCert, $SourceKey, $TargetCert, $TargetKey)) {
  if ($path -notmatch '^/[A-Za-z0-9._/-]+$') {
    throw "Certificate paths must be absolute paths without shell syntax: $path"
  }
}
if ($VerifyHost -notmatch '^[A-Za-z0-9.-]+$') {
  throw "-VerifyHost must be a DNS name."
}
if ($VerifyPort -lt 1024 -or $VerifyPort -gt 65535) {
  throw "-VerifyPort must be an unprivileged port (1024..65535)."
}

$applyLiteral = if ($Apply) { "1" } else { "0" }

# zz's CDN origin serves a copy of bwg's wildcard certificate because zz's
# acme account has no Cloudflare DNS token and cannot issue a wildcard itself.
# bwg renews that wildcard every ~60 days, but nothing updated the copy: the
# zz CDN lane would simply stop verifying on the copy's expiry date while every
# service stayed active. This lane copies the live pair, proves the key matches
# the certificate, backs up the old pair, reloads nginx and checks the
# certificate actually served on the origin port. Read-only by default.
#
# The private key travels only inside the base64 remote payload (the same
# pattern the Hysteria2 acceptance upload already uses) and is never printed;
# only SHA-256 fingerprints appear in the output.
function Get-RemoteOutput {
  param(
    [Parameter(Mandatory = $true)][string]$ProfileName,
    [Parameter(Mandatory = $true)][string]$Command
  )

  $output = @(& $script:Python.Exe @($script:Python.Args + @(
        (Join-Path $repoRoot "ssh_tool.py"),
        "--config", $Config,
        "--profile", $ProfileName,
        "--strict-host-key-checking",
        "run", "--command", $Command
      )) 2>&1)
  if ($LASTEXITCODE -ne 0) {
    throw "Remote read failed on profile $ProfileName."
  }
  return @($output | ForEach-Object { [string]$_ })
}

function Get-RemoteBase64 {
  param(
    [Parameter(Mandatory = $true)][string]$ProfileName,
    [Parameter(Mandatory = $true)][string]$Path
  )

  $lines = Get-RemoteOutput -ProfileName $ProfileName -Command "base64 -w0 -- '$Path'; echo"
  $line = $lines | Where-Object { $_ -match '^[A-Za-z0-9+/=]+$' } | Select-Object -First 1
  if (-not $line) {
    throw "Profile $ProfileName returned no base64 for $Path."
  }
  return [string]$line
}

# The common case is "nothing changed": compare the public certificate and the
# leaf zz actually serves first, so a daily run never moves the private key.
$sourceProbe = Get-RemoteOutput -ProfileName $SourceProfile -Command (@'
openssl x509 -in '__SOURCE_CERT__' -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1
openssl x509 -in '__SOURCE_CERT__' -noout -enddate 2>/dev/null | sed 's/^notAfter=//'
'@.Replace('__SOURCE_CERT__', $SourceCert))
$sourceSha = $sourceProbe | Where-Object { $_ -match '^[0-9a-f]{64}$' } | Select-Object -First 1
if (-not $sourceSha) {
  throw "Could not hash $SourceCert on $SourceProfile."
}
$sourceNotAfter = $sourceProbe | Where-Object { $_ -match 'GMT$' } | Select-Object -First 1

$targetProbe = Get-RemoteOutput -ProfileName $Profile -Command (@'
cert=$(openssl x509 -in '__TARGET_CERT__' -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1)
pair=bad
if [ -f '__TARGET_CERT__' ] && [ -f '__TARGET_KEY__' ]; then
    a=$(openssl x509 -in '__TARGET_CERT__' -noout -pubkey 2>/dev/null |
        openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1)
    b=$(openssl pkey -in '__TARGET_KEY__' -pubout -outform DER 2>/dev/null |
        sha256sum | cut -d' ' -f1)
    [ -n "$a" ] && [ "$a" = "$b" ] && pair=ok
fi
served=$(echo | openssl s_client -connect 127.0.0.1:__VERIFY_PORT__ \
    -servername '__VERIFY_HOST__' 2>/dev/null | openssl x509 -outform DER 2>/dev/null |
    sha256sum | cut -d' ' -f1)
printf 'cert=%s\npair=%s\nserved=%s\n' "${cert:-none}" "$pair" "${served:-none}"
'@.Replace('__TARGET_CERT__', $TargetCert).Replace('__TARGET_KEY__', $TargetKey).Replace('__VERIFY_PORT__', "$VerifyPort").Replace('__VERIFY_HOST__', $VerifyHost))
$targetCertSha = ($targetProbe | Where-Object { $_ -like 'cert=*' } | Select-Object -First 1) -replace '^cert=', ''
$targetPair = ($targetProbe | Where-Object { $_ -like 'pair=*' } | Select-Object -First 1) -replace '^pair=', ''
$targetServed = ($targetProbe | Where-Object { $_ -like 'served=*' } | Select-Object -First 1) -replace '^served=', ''

Write-Output "cdn_cert_source_sha=$sourceSha not_after=$sourceNotAfter"
Write-Output "cdn_cert_target_sha=$targetCertSha pair=$targetPair served=$targetServed"
if ($sourceSha -eq $targetCertSha -and $targetPair -eq 'ok' -and $targetServed -eq $sourceSha) {
  Write-Output 'cdn_cert_sync=applied'
  exit 0
}
Write-Output 'cdn_cert_sync=drift'
if (-not $Apply) {
  exit 0
}

$certBase64 = Get-RemoteBase64 -ProfileName $SourceProfile -Path $SourceCert
$keyBase64 = Get-RemoteBase64 -ProfileName $SourceProfile -Path $SourceKey

$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
CERT_B64="__CERT_B64__"
KEY_B64="__KEY_B64__"
TARGET_CERT="__TARGET_CERT__"
TARGET_KEY="__TARGET_KEY__"
VERIFY_HOST="__VERIFY_HOST__"
VERIFY_PORT="__VERIFY_PORT__"
LOCK_FILE="${VPS_CDN_CERT_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
BACKUP_PARENT="${VPS_CDN_CERT_BACKUP_DIR:-/var/backups}"
STATUS_DIR="${VPS_CDN_CERT_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
STATUS_FILE="$STATUS_DIR/cdn-cert-sync.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=cdn-cert-sync\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

WORK="$(mktemp -d)"
cleanup() { rm -rf -- "$WORK"; }
trap cleanup EXIT INT TERM

printf '%s' "$CERT_B64" | base64 -d > "$WORK/cert.pem"
printf '%s' "$KEY_B64" | base64 -d > "$WORK/key.pem"
chmod 600 "$WORK/cert.pem" "$WORK/key.pem"

if ! openssl x509 -in "$WORK/cert.pem" -noout -checkhost "$VERIFY_HOST" 2>/dev/null |
    grep -q 'does match'; then
    echo "CDN_CERT_HOST_MISMATCH host=$VERIFY_HOST" >&2
    write_status failed 4
    exit 4
fi
cert_pub="$(openssl x509 -in "$WORK/cert.pem" -noout -pubkey 2>/dev/null |
    openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
key_pub="$(openssl pkey -in "$WORK/key.pem" -pubout -outform DER 2>/dev/null |
    sha256sum | awk '{print $1}')"
if [ -z "$cert_pub" ] || [ "$cert_pub" != "$key_pub" ]; then
    echo 'CDN_CERT_KEY_MISMATCH' >&2
    write_status failed 4
    exit 4
fi

# Compare the DER leaf, not the PEM file bytes: the served certificate is read
# back as DER, and a PEM file's whitespace/chain must not look like a change.
new_cert_sha="$(openssl x509 -in "$WORK/cert.pem" -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
new_key_sha="$(sha256sum "$WORK/key.pem" | awk '{print $1}')"
cur_cert_sha="$(openssl x509 -in "$TARGET_CERT" -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
cur_key_sha="$(sha256sum "$TARGET_KEY" 2>/dev/null | awk '{print $1}')"
not_after="$(openssl x509 -in "$WORK/cert.pem" -noout -enddate 2>/dev/null | sed 's/^notAfter=//')"
echo "cdn_cert_source_sha=${new_cert_sha} not_after=${not_after}"
echo "cdn_cert_target_sha=${cur_cert_sha:-none} key_sha=${cur_key_sha:-none}"

if [ "$new_cert_sha" = "$cur_cert_sha" ] && [ "$new_key_sha" = "$cur_key_sha" ]; then
    echo 'cdn_cert_sync=applied'
    write_status noop 0
    exit 0
fi
echo 'cdn_cert_sync=drift'
if [ "$APPLY" != '1' ]; then
    write_status success 0
    exit 0
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/cdn-cert-sync.XXXXXX")"
chmod 700 "$backup_dir"
if [ -f "$TARGET_CERT" ]; then cp -a "$TARGET_CERT" "$backup_dir/cert"; else : > "$backup_dir/cert-absent"; fi
if [ -f "$TARGET_KEY" ]; then cp -a "$TARGET_KEY" "$backup_dir/key"; else : > "$backup_dir/key-absent"; fi

rollback() {
    trap - ERR INT TERM
    set +e
    local failed=0
    if [ -f "$backup_dir/cert" ]; then cp -a "$backup_dir/cert" "$TARGET_CERT" || failed=1; else rm -f -- "$TARGET_CERT" || failed=1; fi
    if [ -f "$backup_dir/key" ]; then cp -a "$backup_dir/key" "$TARGET_KEY" || failed=1; else rm -f -- "$TARGET_KEY" || failed=1; fi
    if ! nginx -t >/dev/null 2>&1; then failed=1; fi
    if ! nginx -s reload >/dev/null 2>&1; then failed=1; fi
    if [ "$failed" -eq 0 ]; then
        echo "ROLLBACK_VERIFIED backup=$backup_dir"
    else
        echo "ROLLBACK_FAILED backup=$backup_dir" >&2
    fi
    write_status failed 1
    exit 1
}
trap rollback ERR INT TERM

install -m 644 "$WORK/cert.pem" "$TARGET_CERT"
install -m 600 "$WORK/key.pem" "$TARGET_KEY"
nginx -t
nginx -s reload

served_sha=""
for _ in $(seq 1 10); do
    served_sha="$(echo | openssl s_client -connect "127.0.0.1:$VERIFY_PORT" \
        -servername "$VERIFY_HOST" 2>/dev/null | openssl x509 -outform DER 2>/dev/null |
        sha256sum | awk '{print $1}')"
    [ "$served_sha" = "$new_cert_sha" ] && break
    sleep 1
done
echo "cdn_cert_served_sha=${served_sha:-none}"
if [ "$served_sha" != "$new_cert_sha" ]; then
    echo 'CDN_CERT_SERVED_MISMATCH' >&2
    false
fi
trap - ERR INT TERM
echo "APPLY_BACKUP_DIR=$backup_dir"
echo 'CDN_CERT_SYNC_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral).
  Replace("__CERT_B64__", $certBase64).
  Replace("__KEY_B64__", $keyBase64).
  Replace("__TARGET_CERT__", $TargetCert).
  Replace("__TARGET_KEY__", $TargetKey).
  Replace("__VERIFY_HOST__", $VerifyHost).
  Replace("__VERIFY_PORT__", "$VerifyPort")

# The payload embeds the wildcard certificate private key: force the
# mode-600 temp-file transport so it never lands in remote /proc/*/cmdline.
Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "cdn-cert-sync" -ViaTempFile

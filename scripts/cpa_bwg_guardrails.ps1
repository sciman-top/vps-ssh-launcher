#requires -Version 7
param(
  [string]$Profile = "bwg",
  [string]$Config = "",
  [switch]$Apply,
  [switch]$Observe,
  [switch]$RotatePath,
  [switch]$DeactivateOAuthLuna,
  [switch]$QuarantineOAuthLuna,
  [switch]$RestoreOAuthLuna,
  [switch]$ConsumeUsageQueue,
  [switch]$AcknowledgeUsageQueueConsumption,
  [string]$ProviderEnvPath = ""
)

$ErrorActionPreference = "Stop"

if ($Profile -ne "bwg") {
  throw "This guardrail workflow is intentionally limited to the bwg profile."
}
if (@($Apply, $Observe, $RotatePath, $DeactivateOAuthLuna, $QuarantineOAuthLuna, $RestoreOAuthLuna | Where-Object { $_ }).Count -gt 1) {
  throw "Choose exactly one of the default strict doctor, -Observe, -Apply, -RotatePath, -DeactivateOAuthLuna, -QuarantineOAuthLuna, or -RestoreOAuthLuna."
}
if ($ConsumeUsageQueue -ne $AcknowledgeUsageQueueConsumption) {
  throw "Usage queue consumption requires both -ConsumeUsageQueue and -AcknowledgeUsageQueueConsumption."
}
if (($ConsumeUsageQueue -or $AcknowledgeUsageQueueConsumption) -and
    ($Apply -or $Observe -or $RotatePath -or $DeactivateOAuthLuna -or
     $QuarantineOAuthLuna -or $RestoreOAuthLuna)) {
  throw "Usage queue consumption is only available with the default strict doctor."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$connectScript = Join-Path $repoRoot "connect.ps1"
$updaterPath = Join-Path $scriptDir "remote\cpa-auto-update.sh"
$healthPath = Join-Path $scriptDir "remote\cpa-health.py"
$policyPath = Join-Path $scriptDir "remote\cpa_policy.py"
$providerRoutesPath = Join-Path $scriptDir "remote\cpa_provider_routes.json"
$fail2banFilterPath = Join-Path $scriptDir "remote\cpa-fail2ban-filter.conf"
$fail2banJailPath = Join-Path $scriptDir "remote\cpa-fail2ban-jail.conf"

if (-not (Test-Path -LiteralPath $connectScript -PathType Leaf)) {
  throw "connect.ps1 was not found at $connectScript"
}
if (-not (Test-Path -LiteralPath $updaterPath -PathType Leaf)) {
  throw "CPA updater source was not found at $updaterPath"
}
if (-not (Test-Path -LiteralPath $healthPath -PathType Leaf)) {
  throw "CPA health source was not found at $healthPath"
}
if (-not (Test-Path -LiteralPath $policyPath -PathType Leaf)) {
  throw "CPA policy source was not found at $policyPath"
}
if (-not (Test-Path -LiteralPath $providerRoutesPath -PathType Leaf)) {
  throw "CPA provider route source was not found at $providerRoutesPath"
}
if (-not (Test-Path -LiteralPath $fail2banFilterPath -PathType Leaf)) {
  throw "CPA fail2ban filter source was not found at $fail2banFilterPath"
}
if (-not (Test-Path -LiteralPath $fail2banJailPath -PathType Leaf)) {
  throw "CPA fail2ban jail source was not found at $fail2banJailPath"
}

function Get-LfNormalizedSha256 {
  param([Parameter(Mandatory = $true)][string]$Text)

  # Hashes must cover exactly the bytes that reach the remote file: the
  # LF-normalized UTF-8 payload, not the CRLF working-copy text.
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
    return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  }
  finally {
    $sha.Dispose()
  }
}

$fail2banFilterText = (Get-Content -LiteralPath $fail2banFilterPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$fail2banFilterBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($fail2banFilterText))
$fail2banFilterSha256 = Get-LfNormalizedSha256 -Text $fail2banFilterText
$fail2banJailText = (Get-Content -LiteralPath $fail2banJailPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$fail2banJailBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($fail2banJailText))
$fail2banJailSha256 = Get-LfNormalizedSha256 -Text $fail2banJailText
$updaterText = (Get-Content -LiteralPath $updaterPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$updaterBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($updaterText))
$updaterSha256 = Get-LfNormalizedSha256 -Text $updaterText
$healthText = (Get-Content -LiteralPath $healthPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$healthBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($healthText))
$healthSha256 = Get-LfNormalizedSha256 -Text $healthText
$policyText = (Get-Content -LiteralPath $policyPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$policyBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($policyText))
$policySha256 = Get-LfNormalizedSha256 -Text $policyText
$providerRoutesText = (Get-Content -LiteralPath $providerRoutesPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$providerRoutes = $providerRoutesText | ConvertFrom-Json
$providerRoutesBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($providerRoutesText)
)
$providerRoutesSha256 = Get-LfNormalizedSha256 -Text $providerRoutesText

function Get-HeadBlobSha256 {
  param([Parameter(Mandatory = $true)][string]$RepoRelativePath)

  # Doctor compares the committed source of truth (HEAD blob, LF as stored in
  # git) against the deployed files. Anchoring to HEAD instead of the working
  # tree keeps uncommitted parallel-session edits from turning into doctor
  # failures; an -Apply projection still verifies the exact working-tree bytes
  # it writes via write_base64_file.
  $tmp = [IO.Path]::GetTempFileName()
  try {
    $gitPath = "HEAD:$($RepoRelativePath -replace '\\', '/')"
    $proc = Start-Process -FilePath "git" `
      -ArgumentList @("-C", $repoRoot, "show", $gitPath) `
      -RedirectStandardOutput $tmp -NoNewWindow -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
      throw "git show $gitPath failed with exit code $($proc.ExitCode)."
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
      return ([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($tmp)))).Replace("-", "").ToLowerInvariant()
    }
    finally {
      $sha.Dispose()
    }
  }
  finally {
    Remove-Item -LiteralPath $tmp -Force
  }
}

$updaterHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-auto-update.sh"
$healthHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-health.py"
$policyHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa_policy.py"
$providerRoutesHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa_provider_routes.json"
$fail2banFilterHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-fail2ban-filter.conf"
$fail2banJailHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-fail2ban-jail.conf"

$projectionHashPairs = @(
  "/opt/cliproxyapi/auto-update.sh=$updaterHeadSha256",
  "/opt/cliproxyapi/cpa-health.py=$healthHeadSha256",
  "/opt/cliproxyapi/cpa_policy.py=$policyHeadSha256",
  "/opt/cliproxyapi/cpa_provider_routes.json=$providerRoutesHeadSha256",
  "/etc/fail2ban/filter.d/cpa-gateway.conf=$fail2banFilterHeadSha256",
  "/etc/fail2ban/jail.d/cpa-gateway.conf=$fail2banJailHeadSha256"
) -join " "
if (($projectionHashPairs -split ' ') | Where-Object { $_ -notmatch '^/[A-Za-z0-9._/-]+=[0-9a-f]{64}$' }) {
  throw "Projection hash pair list failed its injection format check."
}

function Invoke-BwgRemoteScript {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Script,
    [int]$CommandTimeout = 180
  )

  # gitattributes checks *.ps1 out as CRLF; real Linux bash rejects CR in the
  # payload (e.g. "func() {<CR>" is a syntax error), so embedded scripts must
  # be projected LF-only.
  $Script = $Script.Replace("`r`n", "`n").Replace("`r", "`n")
  $payload = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($Script)
  )
  $invoke = {
    param([string]$RemoteCommand)
    & $connectScript `
      -Config $Config `
      -Profile $Profile `
      -StrictHostKeyChecking `
      -Command $RemoteCommand `
      -CommandTimeout $CommandTimeout
    if ($LASTEXITCODE -ne 0) {
      throw "Remote BWG command failed with exit code $LASTEXITCODE."
    }
  }

  # Passing a full Base64 script as one Win32 command-line argument eventually
  # exceeds CreateProcess's limit. Keep normal doctor calls single-shot, while
  # projecting larger guarded scripts through a mode-600 remote temp file.
  $chunkSize = 12000
  if ($payload.Length -le $chunkSize) {
    & $invoke "printf %s $payload | base64 -d | bash"
    return
  }

  $remoteTemp = "/tmp/cpa-guardrails-$([guid]::NewGuid().ToString('N')).b64"
  $tempCreated = $false
  try {
    & $invoke ("umask 077; : > '$remoteTemp'; chmod 600 '$remoteTemp'")
    $tempCreated = $true
    for ($offset = 0; $offset -lt $payload.Length; $offset += $chunkSize) {
      $length = [Math]::Min($chunkSize, $payload.Length - $offset)
      $chunk = $payload.Substring($offset, $length)
      & $invoke ("printf %s '$chunk' >> '$remoteTemp'")
      # Pace the chunks so a burst of SSH channels cannot trip server-side
      # rate limits or crowd out interactive sessions mid-transfer.
      Start-Sleep -Milliseconds 100
    }
    # pipefail's $? is the rightmost nonzero pipeline status, so a corrupted
    # payload fails the decoder check AND a failing remote script keeps its
    # exit code. PIPESTATUS cannot be read across two assignments here: each
    # assignment resets the array.
    & $invoke (
      "set -o pipefail; base64 -d -- '$remoteTemp' | bash; " +
      "rc=`$?; rm -f -- '$remoteTemp'; exit `$rc"
    )
    $tempCreated = $false
  }
  finally {
    if ($tempCreated) {
      try {
        & $invoke "rm -f -- '$remoteTemp'"
      }
      catch {
        Write-Warning "Failed to remove remote CPA guardrail temp file."
      }
    }
  }
}

$doctorScript = @'
set -u
STRICT=1
DOCTOR_FAILED=0
DIR=/opt/cliproxyapi

mark_fail() {
  echo "$1=FAIL"
  DOCTOR_FAILED=1
}

echo "==cpa-doctor=="
date -u +%FT%TZ
hostname
echo "==container=="
if docker inspect --format "status={{.State.Status}} restart={{.RestartCount}} started={{.State.StartedAt}} image={{.Config.Image}}" cli-proxy-api; then
  :
else
  mark_fail container
fi
RUNNING_IMAGE=$(docker inspect --format "{{.Config.Image}}" cli-proxy-api 2>/dev/null || true)
if [ -n "$RUNNING_IMAGE" ] && docker image inspect --format "repo_digests={{json .RepoDigests}}" "$RUNNING_IMAGE" 2>/dev/null; then
  echo image-available=OK
else
  mark_fail image-available
fi
if docker inspect --format '{{.HostConfig.LogConfig.Config}}' cli-proxy-api 2>/dev/null | grep -Fq 'max-size:32m'; then
  echo container-log-rotation=OK
else
  mark_fail container-log-rotation
fi
if grep -Fq 'umask 077' "$DIR/compose.yml" &&
   grep -Fq 'exec ./CLIProxyAPI' "$DIR/compose.yml"; then
  echo compose-umask=OK
else
  mark_fail compose-umask
fi
echo "==listeners=="
if ss -ltnp | grep -E ":(8317|8443)\b"; then
  :
else
  mark_fail listeners
fi
echo "==nginx-guardrails=="
if grep -Eq '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo public-listen=OK
else
  mark_fail public-listen
fi
if grep -Eq 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo random-path=OK
else
  mark_fail random-path
fi
if grep -Fq 'access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-access-log=OK
else
  mark_fail safe-access-log
fi
if grep -Fq 'time=[$time_local]' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-log-timestamp=OK
else
  mark_fail safe-log-timestamp
fi
if grep -Fq 'map $uri $cpa_route_class {' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'route=$cpa_route_class' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-route-class=OK
else
  echo safe-route-class=LEGACY_UNPROJECTED
fi
if grep -Fq 'limit_req=$limit_req_status limit_conn=$limit_conn_status' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-limit-status=OK
else
  mark_fail safe-limit-status
fi
if grep -Fq 'retry_after=$cpa_retry_after_class' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-retry-after=OK
else
  mark_fail safe-retry-after
fi
if grep -Fq 'client_max_body_size 32m;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'proxy_buffering off;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'proxy_read_timeout 300s;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'proxy_send_timeout 300s;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo gateway-transport=OK
else
  mark_fail gateway-transport
fi
if fail2ban-client get cpa-gateway logpath 2>/dev/null | grep -Fq '/var/log/nginx/cpa_gateway.access.log'; then
  echo fail2ban-file-monitor=OK
else
  mark_fail fail2ban-file-monitor
fi
if grep -Fq 'auth_request /_cpa_auth;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'auth_status=(401|403)' /etc/fail2ban/filter.d/cpa-gateway.conf; then
  echo client-auth-classification=OK
else
  mark_fail client-auth-classification
fi
if grep -Fq 'failregex = ^<HOST> method=[A-Z]+ status=(401|403) .* auth_status=(401|403)\s*$' /etc/fail2ban/filter.d/cpa-gateway.conf &&
   grep -Fq 'backend = polling' /etc/fail2ban/jail.d/cpa-gateway.conf &&
   grep -Fq 'logpath = /var/log/nginx/cpa_gateway.access.log tail' /etc/fail2ban/jail.d/cpa-gateway.conf; then
  echo fail2ban-contract=OK
else
  mark_fail fail2ban-contract
fi
if grep -Fq 'limit_conn cpa_cc 6;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo gateway-per-ip-concurrency=6
else
  mark_fail gateway-per-ip-concurrency
fi
# The request limiter directive itself, not just its zone declaration or the
# log-format variable, must survive on the deployed file: a dropped
# `limit_req` silently removes per-IP rate limiting while every log field
# still reports the (never-triggered) status variable.
if grep -Fq 'limit_req zone=cpa_rl burst=10;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo gateway-per-ip-rate-limit=OK
else
  mark_fail gateway-per-ip-rate-limit
fi
# Local throttling must answer 429, not nginx's default 503: a 503 makes a
# self-inflicted limit indistinguishable from upstream overload and gives
# clients no back-off signal. Set 2026-09-09; asserted here since 2026-09-26.
if grep -Fq 'limit_req_status 429;' /etc/nginx/conf.d/cpa-gateway.conf &&
   grep -Fq 'limit_conn_status 429;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo gateway-throttle-status=429
else
  mark_fail gateway-throttle-status
fi
if grep -Eq '^[[:space:]]*error-logs-max-files:[[:space:]]*5[[:space:]]*$' "$DIR/config.yaml"; then
  echo error-logs-max-files=5
else
  mark_fail error-logs-max-files
fi
if grep -Eq '^[[:space:]]*logs-max-total-size-mb:[[:space:]]*32[[:space:]]*$' "$DIR/config.yaml"; then
  echo logs-max-total-size-mb=32
else
  mark_fail logs-max-total-size-mb
fi
if grep -Fq 'client_body_buffer_size 128k;' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo client-body-buffer=OK
else
  mark_fail client-body-buffer
fi
if test -f /etc/logrotate.d/nginx && grep -Fq '/var/log/nginx/*.log' /etc/logrotate.d/nginx; then
  echo nginx-logrotate=OK
else
  mark_fail nginx-logrotate
fi
if test -e /etc/logrotate.d/cpa-gateway; then
  mark_fail duplicate-cpa-logrotate
else
  echo duplicate-cpa-logrotate=ABSENT
fi
if ss -ltn | grep -Eq '127\.0\.0\.1:8317[[:space:]]'; then
  echo cpa-loopback=OK
else
  mark_fail cpa-loopback
fi
if ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]'; then
  echo nginx-public-socket=OK
else
  mark_fail nginx-public-socket
fi
if ss -ltn | grep -Eq '\[::\]:8443[[:space:]]|:::8443[[:space:]]'; then
  mark_fail nginx-public-ipv6-socket
else
  echo nginx-public-ipv6-socket=ABSENT
fi
if ss -ltn | grep -Eq '\[::\]:8317[[:space:]]|:::8317[[:space:]]'; then
  mark_fail cpa-ipv6-socket
else
  echo cpa-ipv6-socket=ABSENT
fi
MGMT_ALLOW=$(grep -E '^[[:space:]]*allow-remote:' "$DIR/config.yaml" | head -1 | sed 's/.*:[[:space:]]*//')
MGMT_KEY=$(grep -A2 '^remote-management:' "$DIR/config.yaml" | grep 'secret-key:' | sed 's/.*secret-key:[[:space:]]*//;s/"//g')
# Two acceptable states: fully disabled, or keyed management behind the
# loopback-only 8317 binding (docker-proxy forwards non-loopback source IPs,
# so tunnel/panel access needs allow-remote=true with a strong key; the
# binding assertion below keeps it off the public network either way).
if [ "$MGMT_ALLOW" = "false" ]; then
  echo management-remote=DISABLED
elif [ "$MGMT_ALLOW" = "true" ] && [ "${#MGMT_KEY}" -ge 32 ]; then
  echo management-remote=LOOPBACK_KEYED
else
  mark_fail management-remote
fi
if grep -qi 'management' /etc/nginx/conf.d/cpa-gateway.conf; then
  mark_fail nginx-no-management-route
else
  echo nginx-no-management-route=OK
fi
if python3 - "$DIR/config.yaml" "$DIR/auth" <<'PY'
import json
import sys
from pathlib import Path
import yaml

def contains_enabled(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).replace('_', '-') == 'identity-confuse' and child is True:
                return True
            if contains_enabled(child):
                return True
    elif isinstance(value, list):
        return any(contains_enabled(child) for child in value)
    return False

config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding='utf-8'))
if contains_enabled(config):
    raise SystemExit(1)
for path in Path(sys.argv[2]).glob('*.json'):
    if contains_enabled(json.loads(path.read_text(encoding='utf-8'))):
        raise SystemExit(1)
PY
then
  echo identity-confuse=ABSENT
else
  mark_fail identity-confuse
fi
if [ -f "$DIR/cpa_policy.py" ] && python3 "$DIR/cpa_policy.py" "$DIR/config.yaml"; then
  echo semantic-policy=OK
else
  mark_fail semantic-policy
fi
if python3 - "$DIR/auth" <<'PY'
import stat
import sys
from pathlib import Path

auth = Path(sys.argv[1])
if not auth.is_dir() or stat.S_IMODE(auth.stat().st_mode) != 0o700:
    raise SystemExit(1)
for path in auth.iterdir():
    if path.is_file() and path.suffix in {'.json', '.cds'}:
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise SystemExit(1)
PY
then
  echo auth-permissions=OK
else
  mark_fail auth-permissions
fi
if python3 - "$DIR/config.yaml" <<'PY'
import stat
import sys
from pathlib import Path

# config.yaml carries every provider API key in cleartext, so owner-only is the
# invariant -Apply enforces. It was previously only printed by `stat` and never
# gated, letting a permission drift leak credentials while the doctor passed.
mode = stat.S_IMODE(Path(sys.argv[1]).stat().st_mode)
raise SystemExit(0 if mode & 0o077 == 0 else 1)
PY
then
  echo config-permissions=owner-only
else
  mark_fail config-permissions
fi
echo "==oauth-monitor=="
if python3 - "$DIR/auth" "$DIR/auth/logs" <<'PY'
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

auth_dir = Path(sys.argv[1])
logs_dir = Path(sys.argv[2])
now = dt.datetime.now(dt.timezone.utc)
expiry_keys = {
    "expired",
    "expires",
    "expires_at",
    "expiresat",
    "access_token_expires",
    "access_token_expires_at",
}
refresh_keys = {
    "last_refresh",
    "last_refresh_at",
    "last-refreshed",
    "refreshed_at",
}


def parse_time(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def values_for_keys(value, wanted):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in wanted:
                found.append(child)
            found.extend(values_for_keys(child, wanted))
    elif isinstance(value, list):
        for child in value:
            found.extend(values_for_keys(child, wanted))
    return found


active = []
for path in sorted(auth_dir.glob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        continue
    if not isinstance(data, dict):
        continue
    keys = {str(key).lower() for key in data}
    if str(data.get("type", "")).lower() == "codex" and {
        "access_token",
        "refresh_token",
    } & keys:
        active.append(data)

print(f"oauth_codex_files={len(active)}")
if not active:
    print("oauth_codex=absent")
    print("oauth_monitor=ABSENT_OPTIONAL")
    raise SystemExit(0)
if len(active) != 1:
    print("oauth_codex=ambiguous")
    print("oauth_monitor=FAIL_MULTIPLE_ACTIVE_FILES")
    raise SystemExit(1)

record = active[0]
expiry = next(
    (parsed for value in values_for_keys(record, expiry_keys) if (parsed := parse_time(value))),
    None,
)
explicit_expired = next(
    (value for value in values_for_keys(record, {"expired"}) if isinstance(value, bool)),
    None,
)
if expiry is not None:
    expired = expiry <= now
    days_left = int((expiry - now).total_seconds() // 86400)
    print(f"oauth_expired={'true' if expired else 'false'}")
    print(f"oauth_days_left={days_left}")
    print(f"oauth_hours_left={(expiry - now).total_seconds() / 3600:.1f}")
    print("oauth_refresh_policy=lead24h_grace2h")
else:
    expired = explicit_expired
    print(
        "oauth_expired="
        + ("true" if expired is True else "false" if expired is False else "unknown")
    )
    print("oauth_days_left=unknown")

refresh = next(
    (parsed for value in values_for_keys(record, refresh_keys) if (parsed := parse_time(value))),
    None,
)
if refresh is None:
    print("oauth_refresh_age_hours=unknown")
else:
    age_hours = max(0.0, (now - refresh).total_seconds() / 3600)
    print(f"oauth_refresh_age_hours={age_hours:.1f}")

# Request bodies are untrusted client text: a failed request whose prompt
# merely mentions oauth failure keywords must never count as a credential
# failure (2026-09-21 false positive). Only response-side dump sections
# (API ERROR RESPONSE / API RESPONSE / RESPONSE) carry upstream error
# evidence; one matching file is one event, never one per regex hit.
refresh_signal_re = re.compile(
    r"invalid_grant|refresh_token_reused|refresh[_ ]token[^\n]{0,80}expired|oauth[^\n]{0,80}\b401\b",
    re.IGNORECASE,
)
error_section_markers = {
    "=== api error response ===",
    "=== api response ===",
    "=== response ===",
}


def dump_error_evidence(path):
    # Split a CLIProxyAPI error dump into (timestamp, error-section text).
    # REQUEST INFO/HEADERS/REQUEST BODY/API REQUEST sections are dropped:
    # headers are masked by CPA, but request bodies are plaintext client
    # payloads and the single source of the 2026-09-21 contamination.
    timestamp = None
    error_lines = []
    in_error_section = False
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None, ""
    for line in text.splitlines():
        marker = line.strip().lower()
        if marker.startswith("=== ") and marker.endswith(" ==="):
            in_error_section = marker in error_section_markers
            continue
        if in_error_section:
            error_lines.append(line)
        elif timestamp is None and line.startswith("Timestamp:"):
            timestamp = line.split(":", 1)[1].strip()
    return timestamp, "\n".join(error_lines)


def parse_dump_time(value, fallback_ts):
    # Dump timestamps carry nanosecond fractions that fromisoformat rejects.
    if value:
        trimmed = re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(trimmed)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return dt.datetime.fromtimestamp(fallback_ts, dt.timezone.utc)


dump_signals = 0
newest_signal_time = None
cutoff = now.timestamp() - 7 * 86400
for path in logs_dir.glob("error-*.log"):
    try:
        stat = path.stat()
    except OSError:
        continue
    if stat.st_mtime < cutoff or stat.st_size > 20_000_000:
        continue
    timestamp, error_text = dump_error_evidence(path)
    if not error_text or not refresh_signal_re.search(error_text):
        continue
    dump_signals += 1
    event_time = parse_dump_time(timestamp, stat.st_mtime)
    if newest_signal_time is None or event_time > newest_signal_time:
        newest_signal_time = event_time

# Background refresh failures are emitted by the CPA container as
# "credential refresh failed ..." warn lines (sdk/cliproxy/auth
# conductor_refresh.go). Container logs never contain request bodies, so
# this scan is immune to prompt-text contamination; an unavailable Docker
# CLI is not a credential failure.
container_signals = 0
try:
    completed = subprocess.run(
        ["docker", "logs", "--since", "168h", "--tail", "20000", "cli-proxy-api"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode == 0:
        container_text = (completed.stdout + "\n" + completed.stderr).lower()
        container_signals = len(
            re.findall(
                r"credential refresh failed|invalid_grant|refresh_token_reused",
                container_text,
            )
        )
except (OSError, subprocess.TimeoutExpired):
    pass

signals = dump_signals + container_signals
# A successful refresh after the newest retained signal means the failure
# was transient and already recovered: report it, but do not keep blocking
# until the dump ages out of the bounded retention window.
resolved = (
    signals > 0
    and container_signals == 0
    and refresh is not None
    and newest_signal_time is not None
    and refresh > newest_signal_time
)
print(f"oauth_refresh_failures_7d={signals}")
if resolved:
    print("oauth_refresh_signals_resolved=true")
print("oauth_refresh_coverage=retained_error_dumps_and_container_log_only; incomplete_bounded_sample")
if signals and not resolved:
    print("oauth_monitor=FAIL_REFRESH_SIGNAL")
    raise SystemExit(1)
if expired is True:
    print("oauth_monitor=FAIL_EXPIRED")
    raise SystemExit(1)
if expiry is None:
    print("oauth_monitor=FAIL_EXPIRY_UNKNOWN")
    raise SystemExit(1)
hours_left = (expiry - now).total_seconds() / 3600
# CLIProxyAPI refreshes codex OAuth at expiry-24h (sdk/auth RefreshLead). Its
# expiry-aware scheduler bounds timer waits to 30s and uses a 5m failure
# backoff. Grade on exact remaining hours, not floored days: integer-day
# thresholds still block up to ~24h before the refresh point. Blocking starts
# only after the refresh window is entered AND a 2h scheduling grace has
# passed without the expiry rolling; 72h out is a non-blocking renewal notice.
if hours_left <= 22:
    print("oauth_monitor=ACTION_REQUIRED_REENROLL_OR_VERIFY_REFRESH")
    raise SystemExit(1)
if hours_left <= 72:
    print("oauth_monitor=WARN_RENEWAL_WINDOW")
else:
    print("oauth_monitor=OK")
PY
then
  :
else
  mark_fail oauth-monitor
fi
echo "==oauth-quarantine=="
if python3 - "$DIR" <<'PY'
import json
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
marker_path = root / "oauth-quarantine.json"
try:
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
except Exception as exc:
    print("oauth_quarantine=UNAVAILABLE exc=" + type(exc).__name__)
    raise SystemExit(0)
exclusions = config.get("oauth-excluded-models") if isinstance(config, dict) else None
codex = exclusions.get("codex", []) if isinstance(exclusions, dict) else []
patterns = {
    pattern.strip().lower()
    for pattern in codex
    if isinstance(pattern, str) and pattern.strip()
}
try:
    manifest = json.loads(
        (root / "cpa_provider_routes.json").read_text(encoding="utf-8")
    )
    oauth_aliases = sorted(
        {
            model["alias"].lower()
            for route in manifest.get("oauth_routes", [])
            if isinstance(route, dict)
            for model in route.get("models", [])
            if isinstance(model, dict) and isinstance(model.get("alias"), str)
        }
    )
except Exception:
    oauth_aliases = []
blocked = sorted(alias for alias in oauth_aliases if alias in patterns)
if not marker_path.exists():
    # A blocked OAuth route without the operator marker is an unreviewed lane
    # change; the semantic policy also fails closed on it, and the doctor must
    # not read the state as a clean "no quarantine" contract.
    if blocked:
        print("oauth_quarantine=UNMARKED_OAUTH_BLOCK blocked=" + ",".join(blocked))
        raise SystemExit(1)
    print("oauth_quarantine=none")
    raise SystemExit(0)
try:
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
except Exception as exc:
    print("oauth_quarantine=INVALID_MARKER exc=" + type(exc).__name__)
    raise SystemExit(1)
if (
    not isinstance(marker, dict)
    or marker.get("version") != 1
    or marker.get("state") != "quarantined"
):
    print("oauth_quarantine=INVALID_MARKER")
    raise SystemExit(1)
aliases_value = marker.get("aliases")
declared = []
if isinstance(aliases_value, list):
    declared = sorted(
        {
            alias.strip().lower()
            for alias in aliases_value
            if isinstance(alias, str) and alias.strip()
        }
    )
if not declared or not set(declared) <= set(oauth_aliases):
    print("oauth_quarantine=INVALID_MARKER")
    raise SystemExit(1)
print("oauth_quarantine=active aliases=" + ",".join(declared))
print("oauth_quarantine_since=" + str(marker.get("since", "unknown")))
print("oauth_quarantine_reason=" + str(marker.get("reason", "unknown")))
if declared != blocked:
    print(
        "oauth_quarantine=INCONSISTENT declared="
        + ",".join(declared)
        + " blocked="
        + ",".join(blocked)
    )
    raise SystemExit(1)
print(
    "oauth_quarantine_note=credential_retained; background_refresh_continues; "
    "not_a_reset_mechanism"
)
raise SystemExit(0)
PY
then
  :
else
  mark_fail oauth-quarantine
fi
PORT_JSON=$(docker inspect --format '{{json .HostConfig.PortBindings}}' cli-proxy-api 2>/dev/null || true)
if [ -n "$PORT_JSON" ] && python3 - "$PORT_JSON" <<'PY'
import json
import sys

try:
    bindings = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    raise SystemExit(1)
expected = {"8317/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8317"}]}
if bindings != expected:
    raise SystemExit(1)
PY
then
  echo cpa-port-binding=exact-loopback-only
else
  mark_fail cpa-port-binding
fi
echo "==nginx-merged-contract=="
NGINX_DUMP=$(mktemp)
if nginx -T >"$NGINX_DUMP" 2>&1; then
  echo merged-config=OK
  listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_DUMP")
  ipv6_listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+\[::\]:8443[[:space:]]+ssl;' "$NGINX_DUMP")
  route_count=$(grep -Ec 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/\(\.\*\)\$' "$NGINX_DUMP")
  proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8317/v1/\$1\$is_args\$args;' "$NGINX_DUMP")
  fallback_count=$(grep -Ec 'location[[:space:]]*/[[:space:]]*\{|return[[:space:]]+404;' "$NGINX_DUMP")
  if [ "$listen_count" -eq 1 ]; then echo public-listen-count=1; else mark_fail public-listen-count; fi
  if [ "$ipv6_listen_count" -eq 0 ]; then echo public-ipv6-listen=ABSENT; else mark_fail public-ipv6-listen; fi
  if [ "$route_count" -eq 1 ]; then echo random-route-count=1; else mark_fail random-route-count; fi
  if [ "$proxy_count" -eq 1 ]; then echo loopback-proxy-count=1; else mark_fail loopback-proxy-count; fi
  if [ "$fallback_count" -ge 2 ]; then echo fallback-404=present; else mark_fail fallback-404; fi
  retry_after_map_count=$(grep -Ec 'map[[:space:]]+\$upstream_http_retry_after[[:space:]]+\$cpa_retry_after_class[[:space:]]+\{' "$NGINX_DUMP")
  if [ "$retry_after_map_count" -eq 1 ]; then echo retry-after-map-count=1; else mark_fail retry-after-map-count; fi
  if grep -Eq 'limit_conn_zone[[:space:]].*cpa_total|limit_conn[[:space:]]+cpa_total[[:space:]]+[0-9]+' "$NGINX_DUMP"; then
    mark_fail unexpected-global-account-concurrency
  else
    echo global-account-concurrency=ABSENT
  fi
else
  mark_fail merged-config
fi
rm -f "$NGINX_DUMP"
echo "==public-route-contract=="
SERVER_NAME=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' /etc/nginx/conf.d/cpa-gateway.conf)
PREFIX=$(grep -oE '/[0-9a-f]{16}/v1/' /etc/nginx/conf.d/cpa-gateway.conf | head -n 1 | cut -d/ -f2)
if [ -n "$SERVER_NAME" ] && [ -n "$PREFIX" ]; then
  PUBLIC_BASE="https://$SERVER_NAME:8443"
  valid_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' "$PUBLIC_BASE/$PREFIX/v1/models" 2>/dev/null || echo 000)
  bare_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' "$PUBLIC_BASE/v1/models" 2>/dev/null || echo 000)
  WRONG_PREFIX=0000000000000000
  if [ "$WRONG_PREFIX" = "$PREFIX" ]; then WRONG_PREFIX=ffffffffffffffff; fi
  wrong_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' "$PUBLIC_BASE/$WRONG_PREFIX/v1/models" 2>/dev/null || echo 000)
  echo "valid_path_unauth=$valid_status"
  echo "bare_path=$bare_status"
  echo "wrong_path=$wrong_status"
  if [ "$valid_status" = "401" ]; then :; else mark_fail valid-path; fi
  if [ "$bare_status" = "404" ]; then :; else mark_fail bare-path; fi
  if [ "$wrong_status" = "404" ]; then :; else mark_fail wrong-path; fi
else
  mark_fail public-route-inputs
fi
echo "==cpa-policy=="
grep -nE "^(host|port|force-model-prefix|request-retry|max-retry-credentials|max-retry-interval|save-cooldown-status|transient-error-cooldown-seconds|error-logs-max-files|logs-max-total-size-mb|usage-statistics-enabled|routing:|  strategy:|  session-affinity:|  session-affinity-ttl:|  session-affinity-subagents:|codex:|  stream-bootstrap-buffering:|  stream-bootstrap-timeout:)" "$DIR/config.yaml" || true
# Remind operator of any HTTP (cleartext) provider slots from the deployed
# route manifest. The slot-3 http://35.213.82.91:8003 is a user-authorised
# exception; no mark_fail, but the reminder prevents relying on memory alone.
python3 - "$DIR/cpa_provider_routes.json" <<'PY'
import json, sys
from pathlib import Path
try:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    http_slots = [
        f"slot={p['slot']} host={p['host']} port={p.get('port','')}"
        for p in manifest.get("providers", [])
        if isinstance(p, dict) and p.get("scheme") == "http"
    ]
    if http_slots:
        print("insecure_http_providers=" + "; ".join(http_slots))
    else:
        print("insecure_http_providers=none")
except Exception as exc:
    print("insecure_http_providers=UNAVAILABLE exc=" + type(exc).__name__)
PY
echo "==models-configured=="
grep -nE "^[[:space:]]+(name|prefix|alias):" "$DIR/config.yaml" || true
echo "==client-model-catalog=="
KEY=$(python3 - "$DIR/config.yaml" <<'PY'
import sys
import yaml

config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
keys = config.get("api-keys") if isinstance(config, dict) else None
if not isinstance(keys, list) or not keys or not isinstance(keys[0], str) or not keys[0]:
    raise SystemExit(1)
print(keys[0])
PY
)
MODEL_CATALOG=$(curl --noproxy '*' -fsS --max-time 20 \
  -H "Authorization: Bearer $KEY" http://127.0.0.1:8317/v1/models || true)
if [ -n "$MODEL_CATALOG" ]; then
  # Fail closed on any model ID the checked-in route manifest does not declare.
  # The doctor previously only printed MODEL_IDS and left the manifest
  # comparison to the reader, so a catalog that grew an unregistered (or
  # resurrected retired) alias still exited zero. A legitimate credential
  # cooldown only removes IDs, so an unknown-ID check cannot false-positive.
  if printf '%s' "$MODEL_CATALOG" | python3 -c '
import json, sys
from pathlib import Path
try:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    catalog = json.load(sys.stdin)
except (OSError, ValueError):
    raise SystemExit(1)
allowed = {
    model["alias"]
    for provider in manifest.get("providers", [])
    if isinstance(provider, dict)
    for model in provider.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
} | {
    model["alias"]
    for route in manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
if not allowed:
    raise SystemExit(1)
if not isinstance(catalog, dict) or not isinstance(catalog.get("data"), list):
    raise SystemExit(1)
ids = sorted({item["id"] for item in catalog["data"] if isinstance(item, dict) and isinstance(item.get("id"), str)})
print("MODEL_IDS=" + ",".join(ids))
unknown = sorted(set(ids) - allowed)
print("MODEL_IDS_UNKNOWN=" + (",".join(unknown) if unknown else "none"))
raise SystemExit(1 if unknown else 0)
' "$DIR/cpa_provider_routes.json"; then
    :
  else
    mark_fail client-model-catalog-contract
  fi
else
  mark_fail client-model-catalog
fi
unset KEY MODEL_CATALOG
echo "==files=="
stat -c "%a %U %G %s %n" "$DIR/config.yaml" "$DIR/compose.yml" "$DIR/auto-update.sh" "$DIR/cpa-health.py" "$DIR/cpa_policy.py" "$DIR/cpa_provider_routes.json" /etc/nginx/conf.d/cpa-gateway.conf
sha256sum "$DIR/config.yaml" "$DIR/compose.yml" "$DIR/auto-update.sh" "$DIR/cpa-health.py" "$DIR/cpa_policy.py" "$DIR/cpa_provider_routes.json" /etc/nginx/conf.d/cpa-gateway.conf
echo "==projection-drift=="
# The pair list is injected from the repo at invocation time (same bytes the
# -Apply projector writes), so any mismatch means the repo moved ahead of or
# behind the last -Apply projection and one of the two must be reconciled.
for pair in __CPA_PROJECTION_HASH_PAIRS__; do
  drift_path="${pair%%=*}"
  drift_want="${pair##*=}"
  drift_name=$(basename "$drift_path")
  if [ ! -f "$drift_path" ]; then
    echo "drift=$drift_name LIVE_MISSING want=$drift_want"
    mark_fail "projection-drift-$drift_name"
    continue
  fi
  drift_got=$(sha256sum "$drift_path" | awk '{print $1}')
  if [ "$drift_got" = "$drift_want" ]; then
    echo "drift=$drift_name MATCH"
  else
    echo "drift=$drift_name MISMATCH want=$drift_want got=$drift_got"
    mark_fail "projection-drift-$drift_name"
  fi
done
echo "==timer=="
systemctl is-enabled cliproxyapi-update.timer || true
systemctl is-active cliproxyapi-update.timer || true
systemctl show cliproxyapi-update.timer -p NextElapseUSecRealtime --value || true
LAST_TRIGGER=$(systemctl show cliproxyapi-update.timer -p LastTriggerUSec --value 2>/dev/null || true)
case "$LAST_TRIGGER" in
  ''|0|no)
    echo "timer_last_trigger=UNKNOWN"
    ;;
  *)
    # Some systemd builds render this USec property as a human-readable
    # timestamp instead of an integer; accept both shapes and never let a
    # bare word reach arithmetic (set -u turns that into a fatal error).
    trigger_epoch=0
    case "$LAST_TRIGGER" in
      *[!0-9]*)
        trigger_epoch=$(date -d "$LAST_TRIGGER" +%s 2>/dev/null || echo 0)
        ;;
      *)
        trigger_epoch=$(( LAST_TRIGGER / 1000000 ))
        ;;
    esac
    if [ "$trigger_epoch" -gt 0 ]; then
      trigger_age_hours=$(( ($(date +%s) - trigger_epoch) / 3600 ))
      echo "timer_last_trigger_age_hours=$trigger_age_hours"
      if [ "$trigger_age_hours" -ge 72 ]; then
        echo "timer_last_trigger=STALE (daily timer has not fired in >=72h; check systemctl list-timers cliproxyapi-update.timer)"
      fi
    else
      echo "timer_last_trigger=UNKNOWN"
    fi
    ;;
esac
echo "==timer-result=="
systemctl show cliproxyapi-update.service -p Result --value
systemctl show cliproxyapi-update.service -p ExecMainStatus --value
systemctl show cliproxyapi-update.service -p ExecMainExitTimestamp --value
grep -E '(BACKUP_HEALTH|CANDIDATE|PRUNE|OK:|UNVERIFIED|DEFER|WAIT:|ROLLBACK|REFRESH_SIGNALS)' "$DIR/auto-update.log" 2>/dev/null | tail -n 6 || true
echo "==inventory=="
df -h / | awk 'NR == 2 {print "root_total="$2" used="$3" avail="$4" use_pct="$5}'
find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | awk '{print "update_backups=" $1}'
du -sk "$DIR/backups" 2>/dev/null | awk 'NR == 1 {print "update_backups_kib=" $1}'
docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -c '^eceasy/cli-proxy-api:' | awk '{print "cpa_image_tags=" $1}'
echo "==cooldown-state=="
python3 - "$DIR" <<'PY'
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

import yaml

root = Path(sys.argv[1])
auth_dir = root / "auth"
now = dt.datetime.now(dt.timezone.utc)
retry_times = []


def collect_retry_times(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "next_retry_after" and isinstance(child, str):
                try:
                    parsed = dt.datetime.fromisoformat(child.replace("Z", "+00:00"))
                except ValueError:
                    continue
                retry_times.append(
                    parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
                )
            collect_retry_times(child)
    elif isinstance(value, list):
        for child in value:
            collect_retry_times(child)


for state_file in auth_dir.glob("*.cds"):
    try:
        collect_retry_times(json.loads(state_file.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        pass

if not retry_times:
    cooldown_state = "none"
    next_retry_after = "none"
elif any(retry_at > now for retry_at in retry_times):
    cooldown_state = "active"
    next_retry_after = max(retry_times).astimezone(dt.timezone.utc).isoformat()
else:
    cooldown_state = "expired"
    next_retry_after = max(retry_times).astimezone(dt.timezone.utc).isoformat()

catalog_read = "unavailable"
catalog_oauth_present = []
catalog_oauth_missing = []
manifest_oauth_aliases = []
try:
    manifest = json.loads(
        (root / "cpa_provider_routes.json").read_text(encoding="utf-8")
    )
    manifest_oauth_aliases = sorted(
        {
            model["alias"]
            for route in manifest.get("oauth_routes", [])
            if isinstance(route, dict)
            for model in route.get("models", [])
            if isinstance(model, dict) and isinstance(model.get("alias"), str)
        }
    )
except (OSError, ValueError):
    manifest_oauth_aliases = []

try:
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    key = config["api-keys"][0]
    request = urllib.request.Request(
        "http://127.0.0.1:8317/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        catalog = json.load(response)
    ids = {item.get("id") for item in catalog.get("data", []) if isinstance(item, dict)}
    catalog_read = "ok"
    catalog_oauth_present = sorted(
        alias for alias in manifest_oauth_aliases if alias in ids
    )
    catalog_oauth_missing = sorted(
        alias for alias in manifest_oauth_aliases if alias not in ids
    )
except Exception:
    pass

# Luna availability is a property of the whole OAuth route, not of one bare
# name. Upstream/account entitlement churn can drop the bare `gpt-6-luna` while
# the compatibility alias `gpt-5.6-luna` keeps serving, so keying the state off a
# single name produced a self-contradictory doctor (MODEL_IDS listing the OAuth
# route while luna_state reported it unavailable). The expected alias set comes
# from the same route manifest the projector and semantic policy use.
if catalog_read != "ok":
    catalog_gpt6_luna = "unknown"
elif "gpt-6-luna" in catalog_oauth_present:
    catalog_gpt6_luna = "present"
else:
    catalog_gpt6_luna = "absent"

if not manifest_oauth_aliases:
    luna_state = "unknown_route_manifest"
elif catalog_read != "ok":
    luna_state = "unknown_catalog_unreadable"
elif not catalog_oauth_missing:
    luna_state = "available"
elif catalog_oauth_present:
    luna_state = "available_partial"
elif cooldown_state == "active":
    luna_state = "active_cooldown"
elif cooldown_state == "expired":
    luna_state = "stale_cooldown_suspected"
else:
    luna_state = "unavailable_unclassified"

print(f"cds_files={len(list(auth_dir.glob('*.cds')))}")
print(f"cooldown_state={cooldown_state}")
print(f"cooldown_next_retry_after={next_retry_after}")
print(f"catalog_gpt6_luna={catalog_gpt6_luna}")
print(
    "catalog_oauth_aliases="
    + (",".join(catalog_oauth_present) if catalog_oauth_present else "none")
)
print(
    "catalog_oauth_missing="
    + (",".join(catalog_oauth_missing) if catalog_oauth_missing else "none")
)
print(f"luna_state={luna_state}")
print("cooldown_state_coverage=local_cooldown_and_catalog_only; not_provider_acceptance")
PY
echo "==auth-modes=="
find "$DIR/auth" -maxdepth 1 -type f -printf "%m\n" | sort | uniq -c
echo "==error-dump-permissions=="
if python3 - "$DIR/auth/logs" <<'PY'
import stat
import sys
from pathlib import Path

logs = Path(sys.argv[1])
if not logs.is_dir() or stat.S_IMODE(logs.stat().st_mode) != 0o700:
    raise SystemExit(1)
for path in logs.glob("error-*.log"):
    if path.is_file() and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise SystemExit(1)
PY
then
  echo error-dump-permissions=OK
else
  mark_fail error-dump-permissions
fi
echo "==gateway-statuses-current-log-24h=="
python3 - <<'PY'
import collections, datetime, json, re, statistics, time
from pathlib import Path
counts = collections.Counter()
upstream = collections.Counter()
status_upstream = collections.Counter()
limit_markers = collections.Counter()
retry_after_markers = collections.Counter()
route_classes = collections.Counter()
last_1h = collections.Counter()
five_xx_local_vs_upstream = collections.Counter()
five_xx_by_client = collections.Counter()
client_503_times = collections.defaultdict(list)
client_503_times_by_plane = collections.defaultdict(list)
abort_request_times = []
cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
cutoff_1h = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
unparsed = 0
# Bound the worst-case scan latency: only the most recent 64 MiB of the
# access log is analyzed (mirroring the 20 MiB error-dump cap); if the log
# grew past the cap within the 24h window, counts are a lower bound.
scan_cap_bytes = 64 * 1024 * 1024
log_handle = Path('/var/log/nginx/cpa_gateway.access.log').open()
log_total_bytes = log_handle.seek(0, 2)
if log_total_bytes > scan_cap_bytes:
    log_handle.seek(log_total_bytes - scan_cap_bytes)
    log_handle.readline()  # drop the partial line at the truncation boundary
    print('log_scan_truncated total_bytes=%d scanned_bytes=%d' % (log_total_bytes, scan_cap_bytes))
else:
    log_handle.seek(0)
for line in log_handle:
    match = re.search(
        r'^(?P<client>\S+) method=\S+(?: route=(?P<route>\S+))? '
        r'status=(?P<status>\d{3})(?: request_time=(?P<request_time>[0-9.]+))? .*'
        r'upstream_status=(?P<upstream>[^ ]+) .*limit_req=(?P<limit_req>[^ ]+) '
        r'limit_conn=(?P<limit_conn>[^ ]+)'
        r'(?: retry_after=(?P<retry_after>[^ ]+))? .*time=\[(?P<time>[^]]+)\]',
        line,
    )
    if not match:
        unparsed += 1
        continue
    try:
        stamp = datetime.datetime.strptime(match.group('time'), '%d/%b/%Y:%H:%M:%S %z')
    except ValueError:
        unparsed += 1
        continue
    if stamp < cutoff:
        continue
    status = match.group('status')
    route_classes[match.group('route') or 'legacy_unknown'] += 1
    counts[status] += 1
    upstream[match.group('upstream')] += 1
    status_upstream[f'{status}/{match.group("upstream")}'] += 1
    limit_markers[f'{match.group("limit_req")}/{match.group("limit_conn")}'] += 1
    retry_after_markers[match.group('retry_after') or 'legacy_unknown'] += 1
    if stamp >= cutoff_1h:
        last_1h[status] += 1
    if status == '499' and match.group('request_time'):
        abort_request_times.append(float(match.group('request_time')))
    if status in ('500', '502', '503'):
        # upstream_status is the primary plane discriminator. A numeric value
        # means the upstream returned the status; '-' means the response was
        # generated locally. request_time only refines the plane after that
        # distinction. IPs stay masked to /16 and retry patterns are aggregate
        # gap stats only, never an address.
        request_time = float(match.group('request_time')) if match.group('request_time') else -1.0
        upstream_known = match.group('upstream') not in ('-', '')
        if upstream_known:
            bucket = ('fast_upstream_lt_0_5s' if 0 <= request_time < 0.5
                      else 'mid_upstream_0_5_to_3s' if request_time < 3
                      else 'slow_upstream_ge_3s')
        else:
            bucket = ('fast_local_lt_0_5s' if 0 <= request_time < 0.5
                      else 'mid_local_0_5_to_3s' if request_time < 3
                      else 'slow_local_ge_3s')
        five_xx_local_vs_upstream[f'{status}/{bucket}'] += 1
        octets = match.group('client').split('.')
        client = '.'.join(octets[:2]) + '.x.x' if len(octets) == 4 else 'masked'
        five_xx_by_client[f'{client}/{status}'] += 1
        if status == '503':
            client_503_times[client].append(stamp.timestamp())
            plane = 'upstream' if upstream_known else 'local'
            client_503_times_by_plane[f'{client}/{plane}'].append(stamp.timestamp())
abort_request_times.sort()
abort_summary = {'count': len(abort_request_times)}
if abort_request_times:
    abort_summary.update({
        'min_s': abort_request_times[0],
        'p50_s': abort_request_times[len(abort_request_times) // 2],
        'max_s': abort_request_times[-1],
    })
retry_pattern = {}
for client, times in client_503_times.items():
    if len(times) < 5:
        continue
    times.sort()
    gaps = [later - earlier for earlier, later in zip(times, times[1:]) if 0 <= later - earlier < 300]
    if gaps:
        retry_pattern[client] = {
            'n503': len(times),
            'median_gap_s': round(statistics.median(gaps), 1),
        }
retry_pattern_by_plane = {}
for client, times in client_503_times_by_plane.items():
    if len(times) < 5:
        continue
    times.sort()
    gaps = [later - earlier for earlier, later in zip(times, times[1:]) if 0 <= later - earlier < 300]
    if gaps:
        retry_pattern_by_plane[client] = {
            'n503': len(times),
            'median_gap_s': round(statistics.median(gaps), 1),
        }
print(json.dumps({'statuses': dict(counts), 'upstream_statuses': dict(upstream),
                  'status_upstream': dict(status_upstream),
                  'limit_markers': dict(limit_markers),
                  'retry_after_classes': dict(retry_after_markers),
                  'route_classes': dict(route_classes),
                  'last_1h_statuses': dict(last_1h),
                  'five_xx_local_vs_upstream': dict(five_xx_local_vs_upstream),
                  'five_xx_by_client_masked': dict(five_xx_by_client),
                  'client_503_retry_pattern': retry_pattern,
                  'client_503_retry_pattern_by_plane': retry_pattern_by_plane,
                  'client_abort_request_time': abort_summary,
                  'unparsed_legacy_lines': unparsed,
                  'coverage': 'current access log only; rotated logs excluded; '
                              'five_xx_local_vs_upstream separates local cooldown fast-fails '
                              '(<0.5s) from upstream passthrough (>=3s); client IPs masked '
                              'to /16; '
                              'client_abort_request_time covers 499 lines carrying request_time '
                              '(a tight cluster, e.g. ~45.0s, proves a fixed client-side total timeout)'}))
error_section_markers = {
    '=== api error response ===',
    '=== api response ===',
    '=== response ===',
}


def dump_error_evidence(raw):
    # Response-side sections only: markers and timestamps taken from
    # REQUEST INFO / error sections never from plaintext request bodies
    # (2026-09-21 prompt-text contamination produced phantom markers and
    # quoted timestamps from earlier doctor output).
    timestamp = None
    error_lines = []
    keep = False
    for line in raw.splitlines():
        marker = line.strip().lower()
        if marker.startswith('=== ') and marker.endswith(' ==='):
            keep = marker in error_section_markers
            continue
        if keep:
            error_lines.append(line)
        elif timestamp is None and line.startswith('Timestamp:'):
            timestamp = line.split(':', 1)[1].strip()
    return timestamp, '\n'.join(error_lines)


events = []
overload_markers = 0
auth_unavailable_files = 0
auth_unavailable_lanes = collections.Counter()
candidates = []
for path in Path('/opt/cliproxyapi/auth/logs').glob('error-*.log'):
    try:
        st = path.stat()
    except OSError:
        continue
    candidates.append((path, st.st_mtime, st.st_size))
candidates.sort(key=lambda item: item[1], reverse=True)
scanned = 0
now_ts = time.time()
for path, mtime, size in candidates:
    # The updater prunes these after 48 hours and CPA itself keeps only the
    # newest error-logs-max-files dumps; the doctor additionally caps the
    # read count so a backlog can never repeat the 2026-09-17 doctor timeout.
    if mtime < now_ts - 7 * 86400 or scanned >= 30:
        break
    if size > 20_000_000:
        continue
    scanned += 1
    timestamp, error_text = dump_error_evidence(path.read_text(errors='replace'))
    if 'server_is_overloaded' in error_text:
        overload_markers += error_text.count('server_is_overloaded')
        events.append({'time_as_logged': timestamp,
                       'oauth_upstream': 'chatgpt.com/backend-api/codex' in error_text})
    if 'auth_unavailable' in error_text:
        auth_unavailable_files += 1
        lane = re.search(r'providers=([a-z0-9._-]+),\s*model=([a-z0-9._-]+)', error_text)
        auth_unavailable_lanes[(lane.group(1) + '/' + lane.group(2)) if lane else 'unclassified'] += 1
print(json.dumps({'retained_overload_request_files': len(events),
                  'overload_markers': overload_markers, 'events': events,
                  'scanned_error_files': scanned,
                  'auth_unavailable_retained_sample_count': auth_unavailable_files,
                  'auth_unavailable_by_lane': dict(auth_unavailable_lanes),
                  'coverage': 'newest retained error dumps only (CPA keeps newest '
                              'error-logs-max-files; updater prunes >24h): incomplete_bounded_error_dumps, '
                              'not full 24h/7d counts; markers/timestamps from response-side sections '
                              'only; not recovery proof'}))
PY
echo "==cache-usage=="
# Aggregate real business-traffic cache telemetry from the in-memory usage
# queue (usage-statistics-enabled + 3600s retention). The endpoint is a
# destructive raw-record API, so the default remains non-consuming and an
# explicit acknowledgement is required. Fetch and reduction happen in one
# process: raw records never enter a shell variable, command line, or output.
if [ "__CPA_DOCTOR_CONSUME_USAGE_QUEUE__" = "1" ] &&
   [ "__CPA_DOCTOR_USAGE_QUEUE_ACK__" = "I_UNDERSTAND_RAW_USAGE_QUEUE" ] &&
   [ -f "$DIR/management-key.txt" ]; then
  python3 - "$DIR/management-key.txt" <<'PY'
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

try:
    key = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    request = Request(
        "http://127.0.0.1:8317/v0/management/usage-queue?count=1000",
        headers={"X-Management-Key": key},
    )
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("response_too_large")
    records = json.loads(raw)
except (OSError, ValueError, TypeError, json.JSONDecodeError, HTTPError, URLError):
    print("cache_usage=UNAVAILABLE_FETCH")
    raise SystemExit
if not isinstance(records, list):
    print("cache_usage=UNAVAILABLE_MALFORMED_RESPONSE")
    raise SystemExit
models = {}
for record in records:
    if not isinstance(record, dict) or record.get("failed"):
        continue
    tokens = record.get("tokens") or {}
    model = str(record.get("model") or "unknown")
    provider = str(record.get("provider") or "").lower()
    # Bucket per (provider, model) lane: the same alias routed across
    # providers mixes incompatible token semantics (deepseek-style lanes
    # report read excluded from input; openai-style lanes report cached as
    # a subset of input) and would distort the ratio.
    lane = f"{provider}/{model}" if provider else model
    bucket = models.setdefault(lane, {"requests": 0, "input": 0, "read": 0, "cached": 0, "creation": 0, "provider": provider})
    bucket["requests"] += 1
    bucket["input"] += int(tokens.get("input_tokens") or 0)
    bucket["read"] += int(tokens.get("cache_read_tokens") or 0)
    bucket["cached"] += int(tokens.get("cached_tokens") or 0)
    bucket["creation"] += int(tokens.get("cache_creation_tokens") or 0)
summary = {}
for lane, bucket in sorted(models.items()):
    entry = {"requests": bucket["requests"], "input": bucket["input"], "read": bucket["read"], "cached": bucket["cached"], "creation": bucket["creation"]}
    # Lane-specific token semantics: deepseek-style lanes report input as
    # read + miss (read excluded from input), OpenAI/codex-style lanes report
    # cached as a subset of input. Pick the numerator accordingly or the
    # ratio exceeds 1 or halves.
    if "deepseek" in bucket["provider"] or (bucket["cached"] == 0 and bucket["read"] > 0):
        served = bucket["read"]
    else:
        served = bucket["cached"]
    if bucket["input"] > 0 and served > 0:
        entry["hit_ratio"] = round(served / bucket["input"], 4)
    summary[lane] = entry
print(json.dumps({"records": len(records), "lanes": summary,
                  "coverage": "in-memory usage queue since last consumer, retention <=3600s; explicit observer pops records; aggregate sums only, bucketed per provider/model lane"}))
PY
else
  echo "cache_usage=UNAVAILABLE_NON_CONSUMING_DOCTOR"
fi
echo "==model-substitution=="
# CLIProxyAPI >= v7.3.8 warns "codex executor: upstream served model %q for
# requested model %q (auth_index=%s)" on silent model substitution. Count
# occurrences only; the log lines themselves stay out of doctor output.
RUNNING_CPA_TAG=$(docker inspect --format '{{.Config.Image}}' cli-proxy-api 2>/dev/null | sed -nE 's#.*:(v[0-9]+\.[0-9]+\.[0-9]+)(@sha256:[0-9a-f]+)?$#\1#p')
if [ -n "$RUNNING_CPA_TAG" ] && [ "$(printf '%s\n' 'v7.3.8' "$RUNNING_CPA_TAG" | sort -V | head -n 1)" = 'v7.3.8' ]; then
  SUBSTITUTIONS_7D=$(docker logs --since 168h cli-proxy-api 2>&1 | grep -c 'upstream served model')
  echo "model_substitution_warnings_7d=$SUBSTITUTIONS_7D"
  if [ "$SUBSTITUTIONS_7D" -gt 5 ] 2>/dev/null; then
    # Elevated threshold: upstream throttles one warn per credential/model pair
    # per 10min, so >5 in 7 days means at least 6 distinct events. Not a gate,
    # but warrants manual review of quality-canary or quality-eval output.
    echo "model_substitution=WARN_SUBSTITUTION_ELEVATED"
  elif [ "$SUBSTITUTIONS_7D" -gt 0 ] 2>/dev/null; then
    echo "model_substitution=WARN_SUBSTITUTION_OBSERVED"
  else
    echo "model_substitution=OK"
  fi
else
  echo "model_substitution_warnings_7d=unavailable"
  echo "model_substitution=UNAVAILABLE_VERSION"
fi
echo "model_substitution_coverage=requires CPA >= v7.3.8 and retained container logs only; upstream throttles one warn per credential/model pair per 10min; WARN_SUBSTITUTION_ELEVATED (>5 in 7d) warrants quality-canary review; observation only, not a strict gate"
echo "==syntax=="
if bash -n "$DIR/auto-update.sh"; then echo updater=OK; else mark_fail updater; fi
if python3 -m py_compile "$DIR/cpa-health.py"; then echo health=OK; else mark_fail health; fi
if python3 -m py_compile "$DIR/cpa_policy.py"; then echo policy=OK; else mark_fail policy; fi
if docker compose -f "$DIR/compose.yml" config --quiet; then echo compose=OK; else mark_fail compose; fi
if nginx -t >/tmp/cpa-doctor-nginx-test.log 2>&1; then
  tail -n 2 /tmp/cpa-doctor-nginx-test.log
  echo nginx-syntax=OK
else
  tail -n 5 /tmp/cpa-doctor-nginx-test.log
  mark_fail nginx-syntax
fi
rm -f /tmp/cpa-doctor-nginx-test.log
if [ "$STRICT" = "1" ] && [ "$DOCTOR_FAILED" -ne 0 ]; then
  echo "DOCTOR_CONTRACT_FAILED"
  exit 1
fi
if [ "$STRICT" = "1" ]; then
  echo "DOCTOR_CONTRACT_OK"
elif [ "$DOCTOR_FAILED" -ne 0 ]; then
  # Observe mode never exits non-zero, but a failing observe run must not be
  # readable as a clean contract: give it its own negative verdict marker.
  echo "DOCTOR_CONTRACT_OBSERVE_FAILED"
else
  echo "DOCTOR_CONTRACT_OBSERVE_OK"
fi
'@

if ($Observe) {
  $doctorScript = $doctorScript.Replace("STRICT=1", "STRICT=0")
}

if (-not $Apply -and -not $RotatePath -and -not $DeactivateOAuthLuna -and
    -not $QuarantineOAuthLuna -and -not $RestoreOAuthLuna) {
  if ($ConsumeUsageQueue) {
    $doctorScript = $doctorScript.Replace("__CPA_DOCTOR_CONSUME_USAGE_QUEUE__", "1")
    $doctorScript = $doctorScript.Replace(
      "__CPA_DOCTOR_USAGE_QUEUE_ACK__",
      "I_UNDERSTAND_RAW_USAGE_QUEUE"
    )
  }
  else {
    $doctorScript = $doctorScript.Replace("__CPA_DOCTOR_CONSUME_USAGE_QUEUE__", "0")
    $doctorScript = $doctorScript.Replace("__CPA_DOCTOR_USAGE_QUEUE_ACK__", "")
  }
  $doctorScript = $doctorScript.Replace("__CPA_PROJECTION_HASH_PAIRS__", $projectionHashPairs)
  Invoke-BwgRemoteScript -Script $doctorScript
  exit 0
}

$rotateScript = @'
set -Eeuo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving backups, restarts, and rollbacks.
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE cpa_busy vps-ssh-launcher-maintenance.lock held"; exit 1; }

NGINX_CONF=/etc/nginx/conf.d/cpa-gateway.conf
BK=/root/cpa-guardrails-path-backup-$(date -u +%Y%m%dT%H%M%S.%NZ)

if ! mkdir -m 700 "$BK"; then
  echo "ROTATION_REFUSED backup_exists_or_create_failed path=$BK"
  exit 1
fi
cp -a "$NGINX_CONF" "$BK/cpa-gateway.conf"
chmod 700 "$BK"

OLD_PATH=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
if [ -z "$OLD_PATH" ] || ! grep -Eq '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  echo "ROTATION_REFUSED unexpected Nginx contract"
  exit 1
fi

NEW_PATH=$(openssl rand -hex 8)
if ! printf '%s' "$NEW_PATH" | grep -Eq '^[0-9a-f]{16}$' || [ "$NEW_PATH" = "$OLD_PATH" ]; then
  echo "ROTATION_REFUSED invalid generated path"
  exit 1
fi

if ! python3 - "$NGINX_CONF" "$OLD_PATH" "$NEW_PATH" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
old = "/" + sys.argv[2] + "/v1/"
new = "/" + sys.argv[3] + "/v1/"
text = path.read_text(encoding="utf-8")
if text.count(old) != 1:
    raise SystemExit("expected exactly one random path")
candidate = text.replace(old, new, 1)
fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
temp = Path(name)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(candidate)
    os.chmod(temp, path.stat().st_mode & 0o777)
    os.replace(temp, path)
finally:
    if temp.exists():
        temp.unlink()
PY
then
  echo "ROTATION_REFUSED path replacement"
  exit 1
fi

assert_path_route_contract() {
  active_path=${1:-$NEW_PATH}
  revoked_path=${2:-$OLD_PATH}
  server_name=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' "$NGINX_CONF")
  if [ -z "$server_name" ]; then
    return 1
  fi
  public_base="https://$server_name:8443"
  active_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$active_path/v1/models" 2>/dev/null || echo 000)
  revoked_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$revoked_path/v1/models" 2>/dev/null || echo 000)
  bare_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/v1/models" 2>/dev/null || echo 000)
  wrong_prefix=0000000000000000
  if [ "$wrong_prefix" = "$active_path" ] || [ "$wrong_prefix" = "$revoked_path" ]; then
    wrong_prefix=ffffffffffffffff
  fi
  wrong_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$wrong_prefix/v1/models" 2>/dev/null || echo 000)
  [ "$active_status" = "401" ] &&
    [ "$revoked_status" = "404" ] &&
    [ "$bare_status" = "404" ] &&
    [ "$wrong_status" = "404" ] &&
    ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]' &&
    ! ss -ltn | grep -Eq '\[::\]:8443[[:space:]]|:::8443[[:space:]]'
}

restore_path() {
  set +e
  rollback_failed=0
  cp -a "$BK/cpa-gateway.conf" "$NGINX_CONF" || rollback_failed=1
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || rollback_failed=1
  else
    rollback_failed=1
  fi
  assert_path_route_contract "$OLD_PATH" "$NEW_PATH" || rollback_failed=1
  if [ "$rollback_failed" -eq 0 ]; then
    echo "ROLLBACK_VERIFIED"
  else
    echo "ROLLBACK_FAILED"
  fi
  set -e
  return 0
}

if ! nginx -t >/tmp/cpa-path-nginx-test.log 2>&1; then
  restore_path
  echo "ROLLBACK path_nginx_syntax"
  tail -n 5 /tmp/cpa-path-nginx-test.log
  exit 1
fi
if ! systemctl reload nginx >/tmp/cpa-path-nginx-reload.log 2>&1; then
  restore_path
  echo "ROLLBACK path_nginx_reload"
  tail -n 5 /tmp/cpa-path-nginx-reload.log
  exit 1
fi

if ! assert_path_route_contract; then
  restore_path
  echo "ROLLBACK path_probe"
  exit 1
fi

echo "BACKUP_DIR=$BK"
echo "OLD_PATH_REVOKED=yes"
echo "NEW_PATH_ACTIVE=yes"
echo "NEW_PATH_NOT_PRINTED=yes"
'@

if ($RotatePath) {
  Invoke-BwgRemoteScript -Script $rotateScript -CommandTimeout 180
  exit 0
}

$deactivateOAuthLunaScript = @'
set -euo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving backups, restarts, and rollbacks.
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE cpa_busy vps-ssh-launcher-maintenance.lock held"; exit 1; }

DIR=/opt/cliproxyapi
CONFIG="$DIR/config.yaml"
AUTH_DIR="$DIR/auth"

# No backup of OAuth JSON is made: this operation intentionally removes all
# locally retained, refreshable OAuth material from the VPS.
# Bare gpt-5.6-luna and gpt-6-luna are served ONLY by the ChatGPT Plus OAuth
# auth file. ai.input.im does not serve Luna, so deleting OAuth material
# removes both Luna aliases from the catalog. config.yaml is NOT edited here,
# so there is no config rollback; recovery is a fresh device login per
# docs/runbooks/cpa-oauth-luna-slot.md. The OAuth exclusion list pins the
# same-name GPT-6 Sol/Astra routes to ai.input.im. The post-removal catalog
# contract derives from the checked-in route manifest: a hardcoded name list
# stranded silently when the 2026-09-23 route projection stopped exposing
# gpt-5.6-sol as a bare client name.
if ! docker stop cli-proxy-api >/dev/null; then
  echo "REFUSE cpa_stop_failed; OAuth files retained"
  exit 1
fi

if ! python3 - "$CONFIG" "$AUTH_DIR" <<'PY'
import json
import sys
from pathlib import Path

import yaml

config_path, auth_dir = Path(sys.argv[1]), Path(sys.argv[2])
config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
if not isinstance(config, dict) or config.get("force-model-prefix") is not True:
    raise SystemExit("REFUSE unexpected CPA routing policy")
active = []
for path in sorted(auth_dir.glob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        raise SystemExit("REFUSE unreadable auth JSON: %s" % path.name)
    if not isinstance(data, dict):
        raise SystemExit("REFUSE non-object auth JSON")
    keys = {str(key).lower() for key in data}
    if {"access_token", "refresh_token"} & keys:
        if str(data.get("type", "")).lower() != "codex":
            raise SystemExit("REFUSE unexpected OAuth auth type")
        active.append(path)
if len(active) < 1:
    raise SystemExit("REFUSE no active Codex OAuth auth to deactivate")
print("ACTIVE_OAUTH_FILES=%d" % len(active))
PY
then
  docker start cli-proxy-api >/dev/null 2>&1 || true
  echo "OAUTH_TOPOLOGY_CHECK_FAILED; OAuth files retained"
  exit 1
fi

if ! python3 - "$DIR" "$AUTH_DIR" <<'PY'
import json
import sys
from pathlib import Path

# The active auth dir MUST be scanned: it holds the live OAuth JSON that this
# transaction exists to remove; /root and backups only hold historical copies.
roots = [Path("/root"), Path(sys.argv[1]) / "backups", Path(sys.argv[2])]
removed = 0
for root in roots:
    if not root.exists():
        continue
    for path in root.rglob("*.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        keys = {str(key).lower() for key in data}
        if {"access_token", "refresh_token"} & keys:
            if str(data.get("type", "")).lower() != "codex":
                raise SystemExit("REFUSE unexpected OAuth JSON type")
            path.unlink()
            removed += 1
if removed < 1:
    raise SystemExit("REFUSE no OAuth material removed")
print(f"OAUTH_MATERIAL_REMOVED count={removed}")
PY
then
  echo "OAUTH_REMOVAL_FAILED; CPA remains stopped"
  exit 1
fi

if ! docker start cli-proxy-api >/dev/null; then
  echo "CPA_START_FAILED after OAuth removal"
  exit 1
fi

KEY=$(python3 - "$CONFIG" <<'PY'
import sys
import yaml

config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
keys = config.get("api-keys") if isinstance(config, dict) else None
if not isinstance(keys, list) or not keys or not isinstance(keys[0], str) or not keys[0]:
    raise SystemExit(1)
print(keys[0])
PY
)
READY=000
for _ in $(seq 1 30); do
  READY=$(curl --noproxy '*' -sS --max-time 5 -o /tmp/cpa-oauth-retire-catalog.json -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" http://127.0.0.1:8317/v1/models || true)
  if [ "$READY" = "200" ]; then
    break
  fi
  sleep 1
done
if [ "$READY" != "200" ] || ! python3 - /tmp/cpa-oauth-retire-catalog.json <<'PY'
import json
import sys
from base64 import b64decode

# Survival contract is derived from the checked-in route manifest (the same
# bytes the projector writes); never a literal copy, which drifts silently on
# every catalog re-projection. Semantics mirror cpa-health readiness: unknown
# IDs and surviving OAuth aliases fail closed, while a cooled-down channel may
# temporarily hide a required alias from the availability-filtered catalog --
# that signal belongs to the doctor gate, not this transaction.
manifest = json.loads(b64decode("__CPA_OAUTH_RETIRE_MANIFEST_B64__").decode("utf-8"))
providers = [p for p in manifest.get("providers", []) if isinstance(p, dict)]
provider_aliases = {
    model["alias"]
    for provider in providers
    for model in provider.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
oauth_aliases = {
    model["alias"]
    for route in manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
optional = {
    model
    for provider in providers
    for model in provider.get("optional_models", [])
    if isinstance(model, str)
}
if not provider_aliases or not oauth_aliases:
    raise SystemExit("REFUSE invalid route manifest")
catalog = json.load(open(sys.argv[1]))
ids = {
    item.get("id")
    for item in catalog.get("data", [])
    if isinstance(item, dict) and isinstance(item.get("id"), str)
}
survived = sorted(oauth_aliases & ids)
unknown = sorted(ids - provider_aliases)
if survived or unknown:
    raise SystemExit(
        "CPA_ROUTE_VERIFICATION_FAILED survived=%s unknown=%s"
        % (",".join(survived) or "none", ",".join(unknown) or "none")
    )
missing = sorted((provider_aliases - optional) - ids)
if missing:
    print("CATALOG_INCOMPLETE missing=%s" % ",".join(missing))
raise SystemExit(0)
PY
then
  rm -f /tmp/cpa-oauth-retire-catalog.json
  echo "CPA_ROUTE_VERIFICATION_FAILED"
  exit 1
fi
rm -f /tmp/cpa-oauth-retire-catalog.json

if ! python3 - "$DIR" "$AUTH_DIR" <<'PY'
import json
import sys
from pathlib import Path

remaining = 0
for root in (Path("/root"), Path(sys.argv[1]) / "backups", Path(sys.argv[2])):
    if not root.exists():
        continue
    for path in root.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and {"access_token", "refresh_token"} & {str(key).lower() for key in data}:
            remaining += 1
raise SystemExit(0 if remaining == 0 else 1)
PY
then
  echo "OAUTH_REMOVAL_VERIFICATION_FAILED"
  exit 1
fi

echo "OAUTH_SLOT_RETAINED=metadata_only"
echo "BARE_LUNA_ROUTES=oauth_removed"
echo "OAUTH_REMOVAL_VERIFIED=yes"
echo "HEALTH_NOTE=generation_defers_exit10_until_reenroll"
'@

if ($DeactivateOAuthLuna) {
  $retireScript = $deactivateOAuthLunaScript.Replace(
    "__CPA_OAUTH_RETIRE_MANIFEST_B64__",
    $providerRoutesBase64
  )
  Invoke-BwgRemoteScript -Script $retireScript -CommandTimeout 240
  exit 0
}

$quarantineScript = @'
set -Eeuo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving restarts and rollbacks.
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE cpa_busy vps-ssh-launcher-maintenance.lock held"; exit 1; }

DIR=/opt/cliproxyapi
CONFIG="$DIR/config.yaml"
AUTH_DIR="$DIR/auth"
MARKER="$DIR/oauth-quarantine.json"
MODE=__CPA_OAUTH_QUARANTINE_MODE__
BK=/root/cpa-oauth-quarantine-backup-$(date -u +%Y%m%dT%H%M%S.%NZ)

# This transaction is a reversible traffic stop for the OAuth lane, not a
# credential operation and not a risk-control "reset": the Codex OAuth JSON is
# never read, copied, deleted or replayed, background token refresh keeps
# running so the slot does not expire, and no quota/cooldown state is cleared.
# It works by removing the OAuth route aliases from the routed catalog through
# oauth-excluded-models.codex, which is the same mechanism -Apply uses in the
# opposite direction, and records the decision in a marker file that the
# semantic policy and strict doctor both read.
case "$MODE" in
  quarantine|restore) : ;;
  *) echo "REFUSE unknown quarantine mode"; exit 1 ;;
esac

if ! mkdir -m 700 "$BK"; then
  echo "REFUSE backup_exists_or_create_failed path=$BK"
  exit 1
fi
cp -a "$CONFIG" "$BK/config.yaml"
MARKER_PREEXISTED=0
if [ -f "$MARKER" ]; then
  cp -a "$MARKER" "$BK/oauth-quarantine.json"
  MARKER_PREEXISTED=1
fi
chmod 700 "$BK"

ROLLBACK_DONE=0
restore_all() {
  if [ "$ROLLBACK_DONE" -eq 1 ]; then return 0; fi
  ROLLBACK_DONE=1
  trap - EXIT INT TERM
  set +e
  # A refusal (unknown mode, already quarantined, no marker to restore, drifted
  # marker) must not cost a container restart. When config.yaml is byte-identical
  # to the backup nothing was mutated and the running container still holds the
  # pre-transaction config, so restarting would only add an outage and blur the
  # refusal signal into an apparently-verified rollback.
  config_mutated=0
  if ! cmp -s "$BK/config.yaml" "$CONFIG"; then
    config_mutated=1
  fi
  rollback_failed=0
  cp -a "$BK/config.yaml" "$CONFIG" || rollback_failed=1
  chmod 600 "$CONFIG" || rollback_failed=1
  if [ "$MARKER_PREEXISTED" -eq 1 ]; then
    cp -a "$BK/oauth-quarantine.json" "$MARKER" || rollback_failed=1
    chmod 600 "$MARKER" || rollback_failed=1
  else
    rm -f "$MARKER" || rollback_failed=1
  fi
  if [ "$config_mutated" -eq 0 ]; then
    if [ "$rollback_failed" -eq 0 ]; then
      echo "ROLLBACK_SKIPPED no_mutation"
    else
      echo "ROLLBACK_FAILED"
    fi
    set -e
    return 0
  fi
  docker restart cli-proxy-api >/dev/null 2>&1 || rollback_failed=1
  if [ -f "$DIR/cpa-health.py" ]; then
    python3 "$DIR/cpa-health.py" readiness >/dev/null 2>&1 || rollback_failed=1
  else
    rollback_failed=1
  fi
  if [ "$rollback_failed" -eq 0 ]; then
    echo "ROLLBACK_VERIFIED"
  else
    echo "ROLLBACK_FAILED"
  fi
  set -e
  return 0
}

rollback_on_exit() {
  rc=$?
  trap - EXIT INT TERM
  if [ "$rc" -ne 0 ]; then
    restore_all
    echo "ROLLBACK transaction_failed"
  fi
  exit "$rc"
}

if [ ! -f "$CONFIG" ]; then
  echo "REFUSE config.yaml missing"
  exit 1
fi
if ! grep -Eq '^[[:space:]]*force-model-prefix:[[:space:]]*true[[:space:]]*$' "$CONFIG"; then
  echo "REFUSE unexpected CPA routing policy"
  exit 1
fi
# Require exactly one active Codex OAuth credential: the quarantine must stop
# traffic while leaving a reversible, refreshable slot behind. Anything else is
# an unexpected topology and is refused before the first mutation.
if ! python3 - "$AUTH_DIR" <<'PY'
import json
import sys
from pathlib import Path

active = 0
for path in sorted(Path(sys.argv[1]).glob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        raise SystemExit("REFUSE unreadable auth JSON: %s" % path.name)
    if not isinstance(data, dict):
        raise SystemExit("REFUSE non-object auth JSON")
    keys = {str(key).lower() for key in data}
    if {"access_token", "refresh_token"} & keys:
        if str(data.get("type", "")).lower() != "codex":
            raise SystemExit("REFUSE unexpected OAuth auth type")
        active += 1
if active != 1:
    raise SystemExit("REFUSE expected exactly one active Codex OAuth credential")
print("ACTIVE_OAUTH_FILES=%d" % active)
PY
then
  echo "OAUTH_TOPOLOGY_CHECK_FAILED"
  exit 1
fi

# Do not use the quarantine transaction to repair an already-drifted config.
# The semantic policy is the same source of truth used by strict doctor and
# must pass before this transaction is allowed to mutate config.yaml. In
# restore mode it also validates the active marker before any write.
if [ ! -f "$DIR/cpa_policy.py" ]; then
  echo "REFUSE cpa_policy.py missing"
  exit 1
fi
if ! python3 "$DIR/cpa_policy.py" "$CONFIG" >/tmp/cpa-quarantine-preflight.log 2>&1; then
  echo "REFUSE baseline_policy"
  tail -n 20 /tmp/cpa-quarantine-preflight.log
  rm -f /tmp/cpa-quarantine-preflight.log
  exit 1
fi
rm -f /tmp/cpa-quarantine-preflight.log

trap rollback_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! python3 - "$CONFIG" "$MARKER" "$MODE" <<'PY'
import json
import os
import sys
import tempfile
from base64 import b64decode
from datetime import datetime, timezone
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
marker_path = Path(sys.argv[2])
mode = sys.argv[3]
manifest = json.loads(
    b64decode("__CPA_OAUTH_QUARANTINE_MANIFEST_B64__").decode("utf-8")
)
oauth_aliases = [
    model["alias"]
    for route in manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
]
if not oauth_aliases:
    raise SystemExit("REFUSE invalid route manifest")

config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
if not isinstance(config, dict) or config.get("force-model-prefix") is not True:
    raise SystemExit("REFUSE unexpected CPA routing policy")
exclusions = config.get("oauth-excluded-models")
if exclusions is None:
    exclusions = {}
if not isinstance(exclusions, dict):
    raise SystemExit("REFUSE oauth-excluded-models must be a mapping")
codex = exclusions.get("codex", [])
if not isinstance(codex, list) or not all(
    isinstance(pattern, str) and pattern.strip() for pattern in codex
):
    raise SystemExit("REFUSE oauth-excluded-models.codex must be a list of strings")

targets = {alias.lower() for alias in oauth_aliases}
present = {pattern.strip().lower() for pattern in codex}


def atomic_write(path, text, mode_bits):
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(temp, mode_bits)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


if mode == "quarantine":
    if marker_path.exists():
        raise SystemExit("REFUSE OAuth lane is already quarantined")
    previous = list(codex)
    after = list(codex) + [
        alias for alias in oauth_aliases if alias.lower() not in present
    ]
else:
    if not marker_path.exists():
        raise SystemExit("REFUSE no OAuth quarantine marker to restore")
    try:
        marker_before = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("REFUSE unreadable quarantine marker: %s" % type(exc).__name__)
    if not isinstance(marker_before, dict) or marker_before.get("state") != "quarantined":
        raise SystemExit("REFUSE unexpected quarantine marker state")
    marker_aliases = marker_before.get("aliases")
    if not isinstance(marker_aliases, list) or {
        str(alias).strip().lower() for alias in marker_aliases
    } != targets:
        raise SystemExit("REFUSE quarantine marker aliases do not match route manifest")
    previous = marker_before.get("previous_codex_exclusions")
    applied = marker_before.get("applied_codex_exclusions")
    if not isinstance(previous, list) or not all(
        isinstance(pattern, str) and pattern.strip() for pattern in previous
    ):
        raise SystemExit("REFUSE quarantine marker has no valid previous exclusions")
    if not isinstance(applied, list) or codex != applied:
        raise SystemExit("REFUSE OAuth quarantine config drift detected")
    after = list(previous)

config_after = dict(config)
config_after["oauth-excluded-models"] = dict(exclusions)
config_after["oauth-excluded-models"]["codex"] = after
candidate = yaml.safe_dump(
    config_after, allow_unicode=True, default_flow_style=False, sort_keys=False
)
if yaml.safe_load(candidate) != config_after:
    raise SystemExit("candidate YAML semantic round-trip failed")
atomic_write(config_path, candidate, 0o600)

if mode == "quarantine":
    marker = {
        "version": 1,
        "state": "quarantined",
        "aliases": sorted(oauth_aliases),
        "previous_codex_exclusions": previous,
        "applied_codex_exclusions": after,
        "since": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": "operator_requested_risk_control",
    }
    atomic_write(marker_path, json.dumps(marker, indent=2) + "\n", 0o600)
    print("QUARANTINE_APPLIED aliases=" + ",".join(sorted(oauth_aliases)))
else:
    marker_path.unlink()
    print("QUARANTINE_RELEASED aliases=" + ",".join(sorted(oauth_aliases)))
PY
then
  restore_all
  echo "ROLLBACK quarantine_state_change"
  exit 1
fi

if ! docker restart cli-proxy-api >/dev/null; then
  restore_all
  echo "ROLLBACK cpa_restart"
  exit 1
fi

KEY=$(python3 - "$CONFIG" <<'PY'
import sys
import yaml

config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
keys = config.get("api-keys") if isinstance(config, dict) else None
if not isinstance(keys, list) or not keys or not isinstance(keys[0], str) or not keys[0]:
    raise SystemExit(1)
print(keys[0])
PY
)
if [ -z "$KEY" ]; then
  restore_all
  echo "ROLLBACK missing_client_key"
  exit 1
fi

READY=000
for _ in $(seq 1 30); do
  READY=$(curl --noproxy '*' -sS --max-time 5 -o /tmp/cpa-quarantine-catalog.json -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" http://127.0.0.1:8317/v1/models || true)
  if [ "$READY" = "200" ]; then
    break
  fi
  sleep 1
done
if [ "$READY" != "200" ]; then
  rm -f /tmp/cpa-quarantine-catalog.json
  restore_all
  echo "ROLLBACK cpa_readiness status=$READY"
  exit 1
fi

if ! python3 - "$DIR" "$MODE" /tmp/cpa-quarantine-catalog.json <<'PY'
import json
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
mode = sys.argv[2]
catalog = json.load(open(sys.argv[3], encoding="utf-8"))
ids = {
    item["id"]
    for item in catalog.get("data", [])
    if isinstance(item, dict) and isinstance(item.get("id"), str)
}
manifest = json.loads((root / "cpa_provider_routes.json").read_text(encoding="utf-8"))
oauth_aliases = sorted(
    {
        model["alias"]
        for route in manifest.get("oauth_routes", [])
        if isinstance(route, dict)
        for model in route.get("models", [])
        if isinstance(model, dict) and isinstance(model.get("alias"), str)
    }
)
provider_aliases = {
    model["alias"]
    for provider in manifest.get("providers", [])
    if isinstance(provider, dict)
    for model in provider.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
optional = {
    model
    for provider in manifest.get("providers", [])
    if isinstance(provider, dict)
    for model in provider.get("optional_models", [])
    if isinstance(model, str)
}
unknown = sorted(ids - provider_aliases)
if unknown:
    raise SystemExit("CPA_ROUTE_VERIFICATION_FAILED unknown=%s" % ",".join(unknown))
if mode == "quarantine":
    survived = sorted(set(oauth_aliases) & ids)
    if survived:
        raise SystemExit(
            "CPA_ROUTE_VERIFICATION_FAILED survived=%s" % ",".join(survived)
        )
    missing = sorted((provider_aliases - optional) - ids)
    if missing:
        print("CATALOG_INCOMPLETE missing=%s" % ",".join(missing))
else:
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    exclusions = config.get("oauth-excluded-models") if isinstance(config, dict) else None
    codex = exclusions.get("codex", []) if isinstance(exclusions, dict) else []
    patterns = {
        pattern.strip().lower()
        for pattern in codex
        if isinstance(pattern, str) and pattern.strip()
    }
    still_blocked = sorted(alias for alias in oauth_aliases if alias.lower() in patterns)
    if still_blocked:
        raise SystemExit(
            "QUARANTINE_RESTORE_FAILED still_blocked=%s" % ",".join(still_blocked)
        )
    restored = sorted(set(oauth_aliases) & ids)
    print(
        "RESTORED_OAUTH_ALIASES="
        + (",".join(restored) if restored else "pending_catalog")
    )
PY
then
  rm -f /tmp/cpa-quarantine-catalog.json
  restore_all
  echo "ROLLBACK quarantine_verification"
  exit 1
fi
rm -f /tmp/cpa-quarantine-catalog.json

if ! python3 "$DIR/cpa_policy.py" "$CONFIG" >/tmp/cpa-quarantine-policy.log 2>&1; then
  restore_all
  echo "ROLLBACK quarantine_policy"
  tail -n 20 /tmp/cpa-quarantine-policy.log
  exit 1
fi

trap - EXIT INT TERM
echo "BACKUP_DIR=$BK"
echo "QUARANTINE_MODE=$MODE"
echo "OAUTH_CREDENTIAL_RETAINED=yes"
echo "QUOTA_STATE_RESET=no"
echo "READY_STATUS=$READY"
'@

if ($QuarantineOAuthLuna -or $RestoreOAuthLuna) {
  $quarantineMode = if ($QuarantineOAuthLuna) { "quarantine" } else { "restore" }
  $quarantineScript = $quarantineScript.Replace(
    "__CPA_OAUTH_QUARANTINE_MODE__",
    $quarantineMode
  ).Replace(
    "__CPA_OAUTH_QUARANTINE_MANIFEST_B64__",
    $providerRoutesBase64
  )
  Invoke-BwgRemoteScript -Script $quarantineScript -CommandTimeout 240
  exit 0
}

if ([string]::IsNullOrWhiteSpace($ProviderEnvPath)) {
  # Provider credentials live in the user profile, mirroring target.json;
  # the repo root holds no private env file.
  $ProviderEnvPath = Join-Path $env:APPDATA "vps-ssh-launcher\providers.env"
}
if (-not (Test-Path -LiteralPath $ProviderEnvPath -PathType Leaf)) {
  throw "Provider env source was not found at $ProviderEnvPath"
}
$providerSlots = @($providerRoutes.providers | ForEach-Object { [int]$_.slot })
$providerSlotSet = @{}
foreach ($slot in $providerSlots) {
  $providerSlotSet[[string]$slot] = $true
}
$providerEnvLines = [System.Collections.Generic.List[string]]::new()
foreach ($line in Get-Content -LiteralPath $ProviderEnvPath) {
  if ($line -match '^\s*(?:export\s+)?(?:BASE_URL|API_KEY)_(\d+)\s*=') {
    if ($providerSlotSet.ContainsKey($matches[1])) {
      $providerEnvLines.Add($line.Trim())
    }
  }
}
$providerEnvText = $providerEnvLines -join "`n"
$providerEnvBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($providerEnvText)
)
$applyScript = @'
set -Eeuo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving backups, restarts, and rollbacks.
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE cpa_busy vps-ssh-launcher-maintenance.lock held"; exit 1; }

DIR=/opt/cliproxyapi
# -Apply recomputes the OAuth exclusion list from the route manifest, which
# would silently re-expose a quarantined lane. Refuse instead of undoing an
# explicit risk-control decision; the operator restores the lane first.
if [ -f /opt/cliproxyapi/oauth-quarantine.json ]; then
  echo "REFUSE OAuth lane quarantine is active; run -RestoreOAuthLuna before -Apply"
  exit 1
fi
NGINX_CONF=/etc/nginx/conf.d/cpa-gateway.conf
FAIL2BAN_FILTER=/etc/fail2ban/filter.d/cpa-gateway.conf
FAIL2BAN_JAIL=/etc/fail2ban/jail.d/cpa-gateway.conf
BK=/root/cpa-guardrails-backup-$(date -u +%Y%m%dT%H%M%S.%NZ)

if ! mkdir -m 700 "$BK"; then
  echo "REFUSE backup_exists_or_create_failed path=$BK"
  exit 1
fi
cp -a "$DIR/config.yaml" "$BK/config.yaml"
cp -a "$DIR/compose.yml" "$BK/compose.yml"
cp -a "$DIR/auto-update.sh" "$BK/auto-update.sh"
if [ -f "$DIR/cpa_provider_routes.json" ]; then
  cp -a "$DIR/cpa_provider_routes.json" "$BK/cpa_provider_routes.json"
else
  : > "$BK/cpa_provider_routes.json.missing"
fi
if [ -f "$DIR/cpa-health.py" ]; then
  cp -a "$DIR/cpa-health.py" "$BK/cpa-health.py"
else
  : > "$BK/cpa-health.py.missing"
fi
if [ -f "$DIR/cpa_policy.py" ]; then
  cp -a "$DIR/cpa_policy.py" "$BK/cpa_policy.py"
else
  : > "$BK/cpa_policy.py.missing"
fi
cp -a "$NGINX_CONF" "$BK/cpa-gateway.conf"
if [ -f "$FAIL2BAN_FILTER" ]; then cp -a "$FAIL2BAN_FILTER" "$BK/cpa-gateway-filter.conf"; fi
if [ -f "$FAIL2BAN_JAIL" ]; then cp -a "$FAIL2BAN_JAIL" "$BK/cpa-gateway-jail.conf"; fi
chmod 700 "$BK"

ROLLBACK_DONE=0
restore_all() {
  if [ "$ROLLBACK_DONE" -eq 1 ]; then return 0; fi
  ROLLBACK_DONE=1
  trap - EXIT INT TERM
  set +e
  rollback_failed=0
  cp -a "$BK/config.yaml" "$DIR/config.yaml" || rollback_failed=1
  cp -a "$BK/compose.yml" "$DIR/compose.yml" || rollback_failed=1
  cp -a "$BK/auto-update.sh" "$DIR/auto-update.sh" || rollback_failed=1
  if [ -f "$BK/cpa_provider_routes.json" ]; then
    cp -a "$BK/cpa_provider_routes.json" "$DIR/cpa_provider_routes.json" || rollback_failed=1
  else
    rm -f "$DIR/cpa_provider_routes.json" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-health.py" ]; then
    cp -a "$BK/cpa-health.py" "$DIR/cpa-health.py" || rollback_failed=1
  else
    rm -f "$DIR/cpa-health.py" || rollback_failed=1
  fi
  if [ -f "$BK/cpa_policy.py" ]; then
    cp -a "$BK/cpa_policy.py" "$DIR/cpa_policy.py" || rollback_failed=1
  else
    rm -f "$DIR/cpa_policy.py" || rollback_failed=1
  fi
  cp -a "$BK/cpa-gateway.conf" "$NGINX_CONF" || rollback_failed=1
  if [ -f "$BK/cpa-gateway-filter.conf" ]; then
    cp -a "$BK/cpa-gateway-filter.conf" "$FAIL2BAN_FILTER" || rollback_failed=1
  else
    rm -f "$FAIL2BAN_FILTER" || rollback_failed=1
  fi
  if [ -f "$BK/cpa-gateway-jail.conf" ]; then
    cp -a "$BK/cpa-gateway-jail.conf" "$FAIL2BAN_JAIL" || rollback_failed=1
  else
    rm -f "$FAIL2BAN_JAIL" || rollback_failed=1
  fi
  chmod 600 "$DIR/config.yaml" || rollback_failed=1
  chmod 700 "$DIR/auto-update.sh" || rollback_failed=1
  docker compose -f "$DIR/compose.yml" config --quiet || rollback_failed=1
  docker restart cli-proxy-api >/dev/null 2>&1 || rollback_failed=1
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || rollback_failed=1
  else
    rollback_failed=1
  fi
  fail2ban-client -t >/dev/null 2>&1 || rollback_failed=1
  fail2ban-client reload --restart cpa-gateway >/dev/null 2>&1 || rollback_failed=1
  if [ -f "$DIR/cpa-health.py" ]; then
    python3 "$DIR/cpa-health.py" readiness >/dev/null 2>&1 || rollback_failed=1
  else
    rollback_failed=1
  fi
  if [ -f "$DIR/cpa_policy.py" ]; then
    python3 "$DIR/cpa_policy.py" "$DIR/config.yaml" >/dev/null 2>&1 || rollback_failed=1
  else
    rollback_failed=1
  fi
  assert_merged_nginx_route_contract || rollback_failed=1
  assert_public_route_contract || rollback_failed=1
  ss -ltn | grep -Eq '127\.0\.0\.1:8317[[:space:]]' || rollback_failed=1
  if ss -ltn | grep -Eq '\[::\]:8317[[:space:]]|:::8317[[:space:]]'; then
    rollback_failed=1
  fi
  if [ "$rollback_failed" -eq 0 ]; then
    echo "ROLLBACK_VERIFIED"
  else
    echo "ROLLBACK_FAILED"
  fi
  set -e
  return 0
}

rollback_on_exit() {
  rc=$?
  trap - EXIT INT TERM
  if [ "$rc" -ne 0 ]; then
    restore_all
    echo "ROLLBACK transaction_failed"
  fi
  exit "$rc"
}

if ! grep -Eq '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  echo "REFUSE unexpected Nginx public listener"
  exit 1
fi
RANDOM_PATH_BEFORE=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
if [ -z "$RANDOM_PATH_BEFORE" ]; then
  echo "REFUSE missing Nginx random capability path"
  exit 1
fi
if ! grep -Eq 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/' "$NGINX_CONF"; then
  echo "REFUSE unexpected Nginx route shape"
  exit 1
fi
if ! test -f /etc/logrotate.d/nginx || ! grep -Fq '/var/log/nginx/*.log' /etc/logrotate.d/nginx; then
  echo "REFUSE existing Nginx logrotate does not cover gateway logs"
  exit 1
fi
if test -e /etc/logrotate.d/cpa-gateway; then
  echo "REFUSE duplicate cpa-gateway logrotate file exists"
  exit 1
fi
MGMT_APPLY_ALLOW=$(grep -E '^[[:space:]]*allow-remote:' "$DIR/config.yaml" | head -1 | sed 's/.*:[[:space:]]*//')
MGMT_APPLY_KEY=$(grep -A2 '^remote-management:' "$DIR/config.yaml" | grep 'secret-key:' | sed 's/.*secret-key:[[:space:]]*//;s/"//g')
if [ "$MGMT_APPLY_ALLOW" = "true" ] && [ "${#MGMT_APPLY_KEY}" -lt 32 ]; then
  echo "REFUSE remote management enabled without a strong (>=32 char) key"
  exit 1
fi
if python3 - "$DIR/config.yaml" "$DIR/auth" <<'PY'
import json
import sys
from pathlib import Path
import yaml

def contains_enabled(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).replace('_', '-') == 'identity-confuse' and child is True:
                return True
            if contains_enabled(child):
                return True
    elif isinstance(value, list):
        return any(contains_enabled(child) for child in value)
    return False

config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding='utf-8'))
if contains_enabled(config):
    raise SystemExit(1)
for path in Path(sys.argv[2]).glob('*.json'):
    if contains_enabled(json.loads(path.read_text(encoding='utf-8'))):
        raise SystemExit(1)
PY
then
  :
else
  echo "REFUSE identity-confuse is enabled"
  exit 1
fi
if ! python3 - "$DIR/auth" <<'PY'
import stat
import sys
from pathlib import Path

auth = Path(sys.argv[1])
if not auth.is_dir() or stat.S_IMODE(auth.stat().st_mode) != 0o700:
    raise SystemExit(1)
for path in auth.iterdir():
    if path.is_file() and path.suffix in {'.json', '.cds'}:
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise SystemExit(1)
PY
then
  echo "REFUSE auth permissions"
  exit 1
fi
PORT_JSON=$(docker inspect --format '{{json .HostConfig.PortBindings}}' cli-proxy-api 2>/dev/null || true)
if [ -z "$PORT_JSON" ] || ! python3 - "$PORT_JSON" <<'PY'
import json
import sys

try:
    bindings = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    raise SystemExit(1)
expected = {"8317/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8317"}]}
if bindings != expected:
    raise SystemExit(1)
PY
then
  echo "REFUSE CPA host port is not loopback-only"
  exit 1
fi

assert_merged_nginx_route_contract() {
  dump_file=$(mktemp)
  if ! nginx -T >"$dump_file" 2>&1; then
    rm -f "$dump_file"
    return 1
  fi
  listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$dump_file")
  ipv6_listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+\[::\]:8443[[:space:]]+ssl;' "$dump_file")
  route_count=$(grep -Ec 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/\(\.\*\)\$' "$dump_file")
  proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8317/v1/\$1\$is_args\$args;' "$dump_file")
  fallback_count=$(grep -Ec 'location[[:space:]]*/[[:space:]]*\{|return[[:space:]]+404;' "$dump_file")
  merged_path=$(grep -oE '/[0-9a-f]{16}/v1/' "$dump_file" | head -n 1 | cut -d/ -f2)
  rm -f "$dump_file"
  if [ "$listen_count" -eq 1 ] &&
     [ "$ipv6_listen_count" -eq 0 ] &&
     [ "$route_count" -eq 1 ] &&
     [ "$proxy_count" -eq 1 ] &&
     [ "$fallback_count" -ge 2 ] &&
     [ "$merged_path" = "$RANDOM_PATH_BEFORE" ]; then
    return 0
  fi
  echo "NGINX_CONTRACT_COUNTS listen=$listen_count ipv6=$ipv6_listen_count route=$route_count proxy=$proxy_count fallback=$fallback_count path_match=$([ "$merged_path" = "$RANDOM_PATH_BEFORE" ] && echo yes || echo no)"
  return 1
}

if ! assert_merged_nginx_route_contract; then
  echo "REFUSE unexpected merged Nginx route contract"
  exit 1
fi

assert_public_route_contract() {
  server_name=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' "$NGINX_CONF")
  prefix=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
  if [ -z "$server_name" ] || [ -z "$prefix" ]; then
    return 1
  fi
  public_base="https://$server_name:8443"
  valid_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$prefix/v1/models" 2>/dev/null || echo 000)
  authenticated_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -H "Authorization: Bearer $KEY" \
    -o /dev/null -w '%{http_code}' "$public_base/$prefix/v1/models" 2>/dev/null || echo 000)
  bare_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/v1/models" 2>/dev/null || echo 000)
  wrong_prefix=0000000000000000
  if [ "$wrong_prefix" = "$prefix" ]; then wrong_prefix=ffffffffffffffff; fi
  wrong_status=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
    --resolve "$server_name:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
    "$public_base/$wrong_prefix/v1/models" 2>/dev/null || echo 000)
  [ "$valid_status" = "401" ] &&
    [ "$authenticated_status" = "200" ] &&
    [ "$bare_status" = "404" ] &&
    [ "$wrong_status" = "404" ] &&
    ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]' &&
    ! ss -ltn | grep -Eq '\[::\]:8443[[:space:]]|:::8443[[:space:]]'
}

KEY=$(python3 - "$DIR/config.yaml" <<'PY'
import sys
import yaml

config = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
keys = config.get("api-keys") if isinstance(config, dict) else None
if not isinstance(keys, list) or not keys or not isinstance(keys[0], str) or not keys[0]:
    raise SystemExit(1)
print(keys[0])
PY
)
if [ -z "$KEY" ]; then
  echo "REFUSE missing_client_key"
  exit 1
fi
# Arm before the first mutation. EXIT also covers unguarded set -e failures;
# signals exit nonzero and follow the same rollback path.
trap rollback_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! python3 - <<'PY'
from base64 import b64decode
from copy import deepcopy
import json
from pathlib import Path
import os
import tempfile
import yaml
from fnmatch import fnmatchcase
from urllib.parse import urlparse


def atomic_write(path: Path, text: str, mode: int) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


config_path = Path("/opt/cliproxyapi/config.yaml")
config_before = yaml.safe_load(config_path.read_text(encoding="utf-8"))
if not isinstance(config_before, dict):
    raise SystemExit("config.yaml is not a mapping")

if config_before.get("request-retry") not in (0, 1):
    raise SystemExit("unexpected request-retry value")
if config_before.get("max-retry-credentials") != 1:
    raise SystemExit("unexpected max-retry-credentials value")
if config_before.get("force-model-prefix") is not True:
    raise SystemExit("force-model-prefix must remain true")
if config_before.get("save-cooldown-status") is not False:
    # false since 2026-09-16: persisted cooldowns (.cds) turned transient upstream
    # capacity cooldowns into stuck catalog absences (upstream #5639/#5770); see
    # docs/runbooks/cpa-stale-cooldown-recovery.md.
    raise SystemExit("save-cooldown-status must remain false")
if not isinstance(config_before.get("routing"), dict):
    raise SystemExit("routing must be a mapping")
if not isinstance(config_before.get("codex"), dict):
    raise SystemExit("codex must be a mapping")
compatibility = config_before.get("openai-compatibility")
if not isinstance(compatibility, list):
    raise SystemExit("openai-compatibility must be a list")


def parse_env(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\\\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


env_values = parse_env(b64decode("__CPA_PROVIDER_ENV_B64__").decode("utf-8-sig"))
route_manifest = json.loads(b64decode("__CPA_PROVIDER_ROUTES_B64__").decode("utf-8"))
provider_slots = route_manifest.get("providers")
if not isinstance(provider_slots, list) or not provider_slots:
    raise SystemExit("REFUSE provider route manifest is empty")
forbidden_provider_keys = {
    "proxy",
    "proxy-url",
    "http-proxy",
    "https-proxy",
    "socks-proxy",
    "headers",
    "header",
    "custom-headers",
    "transport",
    "tls",
    "insecure-skip-verify",
    "skip-tls-verify",
}


def parse_required_slot(route):
    slot = int(route["slot"])
    expected_host = str(route["host"])
    default_path = str(route["path"])
    scheme = str(route.get("scheme", "https"))
    expected_port = route.get("port")
    allow_insecure_http = route.get("allow_insecure_http", False)
    if scheme == "http" and not (
        slot == 3
        and expected_host == "35.213.82.91"
        and expected_port == 8003
        and allow_insecure_http is True
    ):
        raise SystemExit("REFUSE HTTP is allowed only for explicitly authorized slot 3")
    if scheme not in ("https", "http") or (
        scheme == "https" and (expected_port is not None or allow_insecure_http is not False)
    ):
        raise SystemExit(f"REFUSE provider route slot {slot} has invalid transport policy")
    base_url = env_values.get(f"BASE_URL_{slot}", "")
    api_key = env_values.get(f"API_KEY_{slot}", "")
    try:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != scheme
            or parsed.hostname != expected_host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port != expected_port
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        path = parsed.path.rstrip("/")
    except (TypeError, ValueError):
        raise SystemExit(
            f"REFUSE env BASE_URL_{slot} must match the approved slot {slot} route"
        )
    if not api_key:
        raise SystemExit(f"REFUSE env API_KEY_{slot} is empty")
    expected_path = default_path.rstrip("/")
    if path != expected_path:
        raise SystemExit(
            f"REFUSE env BASE_URL_{slot} must match the approved slot {slot} route"
        )
    authority = f"{expected_host}:{expected_port}" if expected_port is not None else expected_host
    return f"{scheme}://{authority}{path}", api_key


def provider_host(entry):
    if not isinstance(entry, dict):
        return None
    return urlparse(str(entry.get("base-url", ""))).hostname


def model_entry(name):
    if not isinstance(name, dict) or set(name) != {"name", "alias"}:
        raise SystemExit("REFUSE provider route model entry is invalid")
    return {"name": name["name"], "alias": name["alias"]}


def build_provider(existing, name, base_url, api_key, models):
    provider = deepcopy(existing) if isinstance(existing, dict) else {}
    if "api-key" in provider:
        raise SystemExit(f"REFUSE {name} uses legacy api-key field")
    for raw_key in provider:
        if str(raw_key).strip().replace("_", "-") in forbidden_provider_keys:
            raise SystemExit(f"REFUSE {name} has forbidden transport override")
    entries = provider.get("api-key-entries")
    if entries is not None and (
        not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(entries[0], dict)
        or set(entries[0]) != {"api-key"}
    ):
        raise SystemExit(f"REFUSE {name} must have exactly one plain api-key entry")
    provider["name"] = name
    provider["base-url"] = base_url
    provider.pop("prefix", None)
    provider.pop("disabled", None)
    provider.pop("api-key", None)
    provider["api-key-entries"] = [{"api-key": api_key}]
    provider["models"] = [model_entry(model) for model in models]
    provider["request-retry"] = 0
    provider["disable-cooling"] = False
    provider["support-prompt-cache-key"] = False
    return provider


target_hosts = {route["host"] for route in provider_slots}
legacy_hosts = set(route_manifest.get("retired_hosts", []))
target_providers = []
remaining_compatibility = []
for item in compatibility:
    host = provider_host(item)
    name = item.get("name") if isinstance(item, dict) else None
    if host in target_hosts or host in legacy_hosts or name == "relay-8003":
        continue
    remaining_compatibility.append(item)

for route in provider_slots:
    slot = int(route["slot"])
    host = str(route["host"])
    name = str(route["name"])
    models = route["models"]
    default_path = str(route["path"])
    base_url, api_key = parse_required_slot(route)
    if str(route.get("scheme", "https")) == "http":
        print(
            f"INSECURE_HTTP_PROVIDER slot={slot} host={host} "
            "api_key_transport=cleartext"
        )
    existing = next(
        (item for item in compatibility if provider_host(item) == host),
        None,
    )
    target_providers.append(build_provider(existing, name, base_url, api_key, models))

config_after = deepcopy(config_before)
config_after["request-retry"] = 0
config_after["error-logs-max-files"] = 5
config_after["logs-max-total-size-mb"] = 32
config_after["routing"].update({
    "strategy": "fill-first",
    "session-affinity": True,
    "session-affinity-ttl": "1h",
    # Avoid concentrating a concurrent subagent fan-out on its parent's
    # credential. Parent sessions still retain their normal affinity/cache
    # locality; only child work falls back to pool distribution.
    "session-affinity-subagents": False,
})
config_after["codex"].update({
    # v7.3.7 supports a finite bootstrap ceiling. Keep overload buffering for
    # correct failover classification, but do not leave first headers
    # uncommitted indefinitely on a slow upstream.
    "stream-bootstrap-buffering": True,
    "stream-bootstrap-timeout": "20s",
})
config_after["openai-compatibility"] = remaining_compatibility + target_providers
if isinstance(config_after.get("codex-api-key"), list):
    config_after["codex-api-key"] = [
        item
        for item in config_after["codex-api-key"]
        if provider_host(item) not in target_hosts | legacy_hosts
    ]

# Keep all surviving Codex API-key lanes from competing with active provider
# aliases or retired names. The native Codex OAuth lane owns Luna; ai.input.im
# owns its bare Sol/Astra routes. Preserve every other exclusion already present.
codex_api_keys = config_after.get("codex-api-key")
if codex_api_keys is not None and not isinstance(codex_api_keys, list):
    raise SystemExit("REFUSE codex-api-key must be a list when present")
if isinstance(codex_api_keys, list):
    for index, provider in enumerate(codex_api_keys):
        if not isinstance(provider, dict):
            raise SystemExit(f"REFUSE codex-api-key[{index}] must be a mapping")
        excluded_models = provider.get("excluded-models", [])
        if not isinstance(excluded_models, list) or not all(
            isinstance(model, str) and model.strip() for model in excluded_models
        ):
            raise SystemExit(
                f"REFUSE codex-api-key[{index}].excluded-models must be a list"
            )
        excluded_normalized = {model.strip().lower() for model in excluded_models}
        for model in route_manifest.get("codex_api_key_exclusions", []):
            if model not in excluded_normalized:
                excluded_models.append(model)
                excluded_normalized.add(model)

# Keep GPT-6 Luna on the existing Codex OAuth lane while pinning the
# same-name Sol/Astra aliases to ai.input.im. Replace only the old family
# wildcard; preserve all unrelated OAuth exclusions and refuse broader rules
# that would need an unsafe expansion to make Luna visible. Exact-name
# exclusions for aliases the manifest currently declares as OAuth routes are
# stale retirements and are dropped so the alias is served again.
oauth_route_aliases = {
    str(model["alias"]).strip().lower()
    for route in route_manifest.get("oauth_routes", [])
    if isinstance(route, dict)
    for model in route.get("models", [])
    if isinstance(model, dict) and isinstance(model.get("alias"), str)
}
oauth_exclusions = config_after.get("oauth-excluded-models")
if oauth_exclusions is None:
    oauth_exclusions = {}
if not isinstance(oauth_exclusions, dict):
    raise SystemExit("REFUSE oauth-excluded-models must be a mapping")
codex_exclusions = oauth_exclusions.get("codex", [])
if not isinstance(codex_exclusions, list) or not all(
    isinstance(pattern, str) and pattern.strip() for pattern in codex_exclusions
):
    raise SystemExit("REFUSE oauth-excluded-models.codex must be a list of strings")
codex_exclusions_after = []
legacy_gpt6_wildcards = {"gpt-6*", "gpt-6-*"}
for pattern in codex_exclusions:
    normalized_pattern = pattern.strip().lower()
    if fnmatchcase("gpt-6-luna", normalized_pattern):
        if normalized_pattern in legacy_gpt6_wildcards:
            continue
        raise SystemExit(
            "REFUSE unexpected Codex OAuth exclusion blocks gpt-6-luna"
        )
    if normalized_pattern in oauth_route_aliases:
        continue
    codex_exclusions_after.append(pattern)
for model in route_manifest.get("oauth_exclusions", []):
    if model not in {pattern.strip().lower() for pattern in codex_exclusions_after}:
        codex_exclusions_after.append(model)
oauth_exclusions["codex"] = codex_exclusions_after
config_after["oauth-excluded-models"] = oauth_exclusions

allowed_top_level_changes = {
    "request-retry",
    "routing",
    "codex",
    "openai-compatibility",
    "codex-api-key",
    "oauth-excluded-models",
    "error-logs-max-files",
    "logs-max-total-size-mb",
}
before_unapproved = deepcopy(config_before)
after_unapproved = deepcopy(config_after)
for key in allowed_top_level_changes:
    before_unapproved.pop(key, None)
    after_unapproved.pop(key, None)
if before_unapproved != after_unapproved:
    raise SystemExit("config change exceeded the approved field/block set")

candidate = yaml.safe_dump(
    config_after,
    allow_unicode=True,
    default_flow_style=False,
    sort_keys=False,
)
# safe_dump rewrites the whole file; hand-written comments in config.yaml do
# not survive, so config content stays tool-owned (no hand edits).
parsed_candidate = yaml.safe_load(candidate)
if parsed_candidate != config_after:
    raise SystemExit("candidate YAML semantic round-trip failed")
atomic_write(config_path, candidate, 0o600)

updater_path = Path("/opt/cliproxyapi/auto-update.sh")
updater = updater_path.read_text(encoding="utf-8")
old_key_line = (
    "KEY=$(sed -n '/^api-keys:/{n;s/.*\\\"\\([0-9a-f]*\\)\\\".*/\\1/p}' "
    '\"$DIR/config.yaml\")'
)
new_key_line = (
    'KEY=$(grep -A1 "^api-keys:" "$DIR/config.yaml" | '
    'tail -n 1 | sed -E "s/[^0-9a-f]//g")'
)
if old_key_line in updater:
    updater = updater.replace(old_key_line, new_key_line, 1)
elif new_key_line not in updater and not (
    'KEY=$(awk \'/^api-keys:/{getline; line=$0; '
    'gsub(/[^0-9a-f]/, "", line); print line; exit}\' '
    '"$DIR/config.yaml")' in updater
    or "key = yaml.safe_load(open(sys.argv[1]))['api-keys'][0]" in updater
    or 'health() { python3 "$DIR/cpa-health.py" "$1"; }' in updater
):
    raise SystemExit("updater key extraction anchor not found")
atomic_write(updater_path, updater, 0o700)

nginx_path = Path("/etc/nginx/conf.d/cpa-gateway.conf")
nginx = nginx_path.read_text(encoding="utf-8")
required = [
    "limit_req_zone $binary_remote_addr zone=cpa_rl:1m rate=10r/s;",
    "limit_conn_zone $binary_remote_addr zone=cpa_cc:1m;",
    "limit_conn cpa_cc 6;",
    "client_max_body_size 32m;",
    "client_body_buffer_size 128k;",
    "proxy_buffering off;",
    "proxy_read_timeout 300s;",
    "proxy_send_timeout 300s;",
]
for anchor in required:
    if anchor not in nginx:
        raise SystemExit(f"expected Nginx guardrail missing: {anchor}")
new_limit_req = "limit_req zone=cpa_rl burst=10;"
legacy_limit_req = "limit_req zone=cpa_rl burst=20 nodelay;"
if new_limit_req not in nginx:
    if legacy_limit_req not in nginx:
        raise SystemExit("expected Nginx request limiter missing")
    # Accept the previously deployed limiter as an input state. The same
    # transaction below rewrites it to the queued burst policy before nginx -t.
    nginx = nginx.replace(legacy_limit_req, new_limit_req, 1)


def ensure_nginx_directive(text, directive, anchor):
    # nginx defaults both throttle statuses to 503, which re-conflates local
    # rate limiting with upstream overload and gives clients no back-off
    # signal. The 2026-09-09 hardening set them to 429, but no versioned anchor
    # asserted them, so a regeneration could silently drop them. Insert next to
    # the matching limiter when absent; never reorder an existing directive.
    if directive in text:
        return text
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if anchor in line:
            indent = line[: len(line) - len(line.lstrip())]
            lines.insert(index + 1, indent + directive + "\n")
            return "".join(lines)
    raise SystemExit("expected Nginx limiter anchor missing for " + directive)


nginx = ensure_nginx_directive(nginx, "limit_req_status 429;", new_limit_req)
nginx = ensure_nginx_directive(nginx, "limit_conn_status 429;", "limit_conn cpa_cc 6;")

route_class_map = (
    "map $uri $cpa_route_class {\n"
    "    \"~^/[0-9a-f]{16}/v1/models$\" models;\n"
    "    \"~^/[0-9a-f]{16}/v1/chat/completions$\" chat;\n"
    "    \"~^/[0-9a-f]{16}/v1/responses$\" responses;\n"
    "    default other;\n"
    "}\n"
)
if "map $uri $cpa_route_class {" not in nginx:
    nginx = route_class_map + nginx
elif route_class_map not in nginx:
    raise SystemExit("existing cpa_route_class map differs from approved redacted map")

retry_after_map = (
    "map $upstream_http_retry_after $cpa_retry_after_class {\n"
    "    \"\" absent;\n"
    "    \"~^[0-9]{1,5}$\" seconds;\n"
    "    default other;\n"
    "}\n"
)
if "map $upstream_http_retry_after $cpa_retry_after_class {" not in nginx:
    nginx = retry_after_map + nginx
elif retry_after_map not in nginx:
    raise SystemExit("existing cpa_retry_after_class map differs from approved redacted map")

log_format = (
    "log_format cpa_safe '$remote_addr method=$request_method "
    "route=$cpa_route_class "
    "status=$status request_time=$request_time "
    "upstream_status=$upstream_status "
    "upstream_time=$upstream_response_time bytes=$body_bytes_sent "
    "limit_req=$limit_req_status limit_conn=$limit_conn_status "
    "retry_after=$cpa_retry_after_class "
    "time=[$time_local] auth_status=$cpa_auth_status';\n"
)
legacy_log_format = log_format.replace(" auth_status=$cpa_auth_status", "")
without_retry_log_format = log_format.replace(
    " retry_after=$cpa_retry_after_class", ""
)
previous_log_format = without_retry_log_format.replace(
    " limit_req=$limit_req_status limit_conn=$limit_conn_status", ""
)
previous_legacy_log_format = previous_log_format.replace(
    " auth_status=$cpa_auth_status", ""
)
# Older deployed logs did not include route classification. Accept them during
# the read/replace transition, but every new projected config must use the
# redacted route class field rather than the random public path.
legacy_route_log_format = without_retry_log_format.replace(" auth_status=$cpa_auth_status", "")
previous_route_log_format = legacy_route_log_format.replace(
    " route=$cpa_route_class", ""
)
previous_unclassified_log_format = without_retry_log_format.replace(
    " route=$cpa_route_class", ""
)
# Adding auth_status requires the separately verified auth_request location.
if 'auth_request /_cpa_auth;' not in nginx:
    log_format = legacy_log_format
if "log_format cpa_safe " not in nginx:
  nginx = log_format + nginx
elif log_format not in nginx:
    old_formats = [
        without_retry_log_format,
        legacy_route_log_format,
        previous_unclassified_log_format,
        previous_route_log_format,
        previous_legacy_log_format,
        previous_log_format,
    ] if 'auth_request /_cpa_auth;' not in nginx else [
        without_retry_log_format,
        legacy_route_log_format,
        previous_unclassified_log_format,
        previous_route_log_format,
        previous_log_format,
        previous_legacy_log_format,
    ]
    for old_format in old_formats:
        if old_format in nginx:
            nginx = nginx.replace(old_format, log_format, 1)
            break
    else:
        raise SystemExit("existing cpa_safe log format differs from approved redacted format")
nginx = nginx.replace(
    "access_log /var/log/nginx/cpa_gateway.access.log;",
    "access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;",
    1,
)
if "access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;" not in nginx:
    raise SystemExit("safe access log anchor not installed")
atomic_write(nginx_path, nginx, 0o644)

nginx_logrotate_path = Path("/etc/logrotate.d/nginx")
if not nginx_logrotate_path.exists():
    raise SystemExit("existing Nginx logrotate file is missing")
nginx_logrotate = nginx_logrotate_path.read_text(encoding="utf-8")
if "/var/log/nginx/*.log" not in nginx_logrotate:
    raise SystemExit("existing Nginx logrotate does not cover gateway logs")

print(
    "CONFIG_POLICY_READY "
    f"request_retry={config_after['request-retry']}"
)
print("CODEX_OAUTH_ROUTES_READY gpt-6-luna=allowed gpt-6-sol/astra=excluded")
PY
then
  restore_all
  echo "ROLLBACK config_or_updater"
  exit 1
fi

write_base64_file() {
  encoded=$1
  path=$2
  mode=$3
  expected_sha=$4
  temp=$(mktemp "${path}.XXXXXX")
  if ! printf '%s' "$encoded" | base64 -d >"$temp"; then
    rm -f "$temp"
    return 1
  fi
  chmod "$mode" "$temp" || { rm -f "$temp"; return 1; }
  if ! mv -f "$temp" "$path"; then
    rm -f "$temp"
    return 1
  fi
  # Write-then-verify: the projected bytes must equal the repo-side payload
  # exactly; a mismatch rolls back through the caller instead of surviving as
  # silent drift for a human to catch in a later sha listing.
  if [ -n "$expected_sha" ]; then
    written_sha=$(sha256sum "$path" | awk '{print $1}')
    if [ "$written_sha" != "$expected_sha" ]; then
      echo "PROJECTION_HASH_MISMATCH path=$path want=$expected_sha got=$written_sha"
      return 1
    fi
    echo "PROJECTION_HASH_VERIFIED path=$path"
  fi
}
write_base64_file "__CPA_FAIL2BAN_FILTER_B64__" "$FAIL2BAN_FILTER" 644 "__CPA_FAIL2BAN_FILTER_SHA256__" || {
  restore_all
  echo "ROLLBACK fail2ban_filter_write"
  exit 1
}
write_base64_file "__CPA_FAIL2BAN_JAIL_B64__" "$FAIL2BAN_JAIL" 644 "__CPA_FAIL2BAN_JAIL_SHA256__" || {
  restore_all
  echo "ROLLBACK fail2ban_jail_write"
  exit 1
}
write_base64_file "__CPA_UPDATER_B64__" "$DIR/auto-update.sh" 700 "__CPA_UPDATER_SHA256__" || {
  restore_all
  echo "ROLLBACK updater_projection"
  exit 1
}
write_base64_file "__CPA_HEALTH_B64__" "$DIR/cpa-health.py" 644 "__CPA_HEALTH_SHA256__" || {
  restore_all
  echo "ROLLBACK health_projection"
  exit 1
}
write_base64_file "__CPA_PROVIDER_ROUTES_B64__" "$DIR/cpa_provider_routes.json" 644 "__CPA_PROVIDER_ROUTES_SHA256__" || {
  restore_all
  echo "ROLLBACK provider_route_projection"
  exit 1
}
write_base64_file "__CPA_POLICY_B64__" "$DIR/cpa_policy.py" 644 "__CPA_POLICY_SHA256__" || {
  restore_all
  echo "ROLLBACK policy_projection"
  exit 1
}
if ! python3 -m py_compile "$DIR/cpa-health.py"; then
  restore_all
  echo "ROLLBACK health_syntax"
  exit 1
fi
if grep -Eq '^[[:space:]]*listen[[:space:]]+\[::\]:8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_public_listener"
  exit 1
fi
if ! python3 -m py_compile "$DIR/cpa_policy.py"; then
  restore_all
  echo "ROLLBACK policy_syntax"
  exit 1
fi
if ! python3 "$DIR/cpa_policy.py" "$DIR/config.yaml" >/tmp/cpa-policy-test.log 2>&1; then
  restore_all
  echo "ROLLBACK semantic_policy"
  tail -n 20 /tmp/cpa-policy-test.log
  exit 1
fi

chmod 600 "$DIR/config.yaml"
chmod 700 "$DIR/auto-update.sh"
if [ -d "$DIR/auth/logs" ]; then
  chmod 700 "$DIR/auth/logs"
  find "$DIR/auth/logs" -maxdepth 1 -type f -name 'error-*.log' -exec chmod 600 -- {} +
fi

if ! bash -n "$DIR/auto-update.sh"; then
  restore_all
  echo "ROLLBACK updater_syntax"
  exit 1
fi
if ! docker compose -f "$DIR/compose.yml" config --quiet; then
  restore_all
  echo "ROLLBACK compose_config"
  exit 1
fi
if ! grep -Fq 'umask 077' "$DIR/compose.yml" ||
   ! grep -Fq 'exec ./CLIProxyAPI' "$DIR/compose.yml"; then
  restore_all
  echo "ROLLBACK compose_umask"
  exit 1
fi
if ! assert_merged_nginx_route_contract; then
  restore_all
  echo "ROLLBACK merged_nginx_route_contract"
  exit 1
fi
if ! grep -Fq 'access_log /var/log/nginx/cpa_gateway.access.log cpa_safe;' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK safe_access_log"
  exit 1
fi
if ! grep -Fq 'map $uri $cpa_route_class {' "$NGINX_CONF" ||
   ! grep -Fq 'route=$cpa_route_class' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK redacted_route_class_observability"
  exit 1
fi
for anchor in \
  'client_max_body_size 32m;' \
  'client_body_buffer_size 128k;' \
  'proxy_buffering off;' \
  'proxy_read_timeout 300s;' \
  'proxy_send_timeout 300s;'; do
  if ! grep -Fq "$anchor" "$NGINX_CONF"; then
    restore_all
    echo "ROLLBACK gateway_transport_contract anchor=$anchor"
    exit 1
  fi
done
for anchor in \
  'limit_req zone=cpa_rl burst=10;' \
  'limit_req_status 429;' \
  'limit_conn_status 429;'; do
  if ! grep -Fq "$anchor" "$NGINX_CONF"; then
    restore_all
    echo "ROLLBACK gateway_throttle_contract anchor=$anchor"
    exit 1
  fi
done
if ! fail2ban-client -t >/dev/null 2>&1; then
  restore_all
  echo "ROLLBACK fail2ban_syntax"
  exit 1
fi

if ! docker restart cli-proxy-api >/dev/null; then
  restore_all
  echo "ROLLBACK cpa_restart"
  exit 1
fi

READY=000
for _ in $(seq 1 30); do
  READY=$(curl --noproxy '*' -sS --max-time 5 -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" \
    http://127.0.0.1:8317/v1/models || true)
  if [ "$READY" = "200" ]; then
    break
  fi
  sleep 1
done
if [ "$READY" != "200" ]; then
  restore_all
  echo "ROLLBACK cpa_readiness status=$READY"
  exit 1
fi
if ! python3 "$DIR/cpa-health.py" readiness; then
  restore_all
  echo "ROLLBACK cpa_model_catalog_contract"
  exit 1
fi
SERVER_NAME=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' "$NGINX_CONF")
PREFIX=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
if [ -z "$SERVER_NAME" ] || [ -z "$PREFIX" ]; then
  restore_all
  echo "ROLLBACK public_route_inputs"
  exit 1
fi
PUBLIC_READY=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 \
  --resolve "$SERVER_NAME:8443:127.0.0.1" -H "Authorization: Bearer $KEY" \
  -o /dev/null -w '%{http_code}' "https://$SERVER_NAME:8443/$PREFIX/v1/models" 2>/dev/null || echo 000)
if [ "$PUBLIC_READY" != "200" ]; then
  restore_all
  echo "ROLLBACK public_authenticated_probe status=$PUBLIC_READY"
  exit 1
fi

if ! nginx -t >/tmp/cpa-nginx-test.log 2>&1; then
  restore_all
  echo "ROLLBACK nginx_syntax"
  tail -n 5 /tmp/cpa-nginx-test.log
  exit 1
fi
if ! grep -Eq '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK nginx_public_listener_changed"
  exit 1
fi
RANDOM_PATH_AFTER=$(grep -oE '/[0-9a-f]{16}/v1/' "$NGINX_CONF" | head -n 1 | cut -d/ -f2)
if [ "$RANDOM_PATH_AFTER" != "$RANDOM_PATH_BEFORE" ]; then
  restore_all
  echo "ROLLBACK nginx_random_path_changed"
  exit 1
fi
if ! grep -Eq 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/' "$NGINX_CONF"; then
  restore_all
  echo "ROLLBACK nginx_route_shape_changed"
  exit 1
fi
if ! systemctl reload nginx >/tmp/cpa-nginx-reload.log 2>&1; then
  restore_all
  echo "ROLLBACK nginx_reload"
  tail -n 5 /tmp/cpa-nginx-reload.log
  exit 1
fi
if ! fail2ban-client reload --restart cpa-gateway >/tmp/cpa-fail2ban-reload.log 2>&1; then
  restore_all
  echo "ROLLBACK fail2ban_reload"
  tail -n 5 /tmp/cpa-fail2ban-reload.log
  exit 1
fi
if ! assert_merged_nginx_route_contract; then
  restore_all
  echo "ROLLBACK merged_nginx_route_contract_after_reload"
  exit 1
fi
if ! assert_public_route_contract; then
  restore_all
  echo "ROLLBACK public_route_contract_after_reload"
  exit 1
fi
if ! ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]'; then
  restore_all
  echo "ROLLBACK nginx_public_listener_missing"
  exit 1
fi
if ss -ltn | grep -Eq '\[::\]:8443[[:space:]]|:::8443[[:space:]]'; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_public_listener_runtime"
  exit 1
fi
if ! ss -ltn | grep -Eq '127\.0\.0\.1:8317[[:space:]]'; then
  restore_all
  echo "ROLLBACK cpa_loopback_listener_missing"
  exit 1
fi
if ss -ltn | grep -Eq '\[::\]:8317[[:space:]]|:::8317[[:space:]]'; then
  restore_all
  echo "ROLLBACK unexpected_ipv6_cpa_listener_runtime"
  exit 1
fi

if ! logrotate -d /etc/logrotate.conf >/tmp/cpa-logrotate-test.log 2>&1; then
  restore_all
  echo "ROLLBACK logrotate_syntax"
  tail -n 5 /tmp/cpa-logrotate-test.log
  exit 1
fi

trap - EXIT INT TERM
echo "BACKUP_DIR=$BK"
echo "READY_STATUS=$READY"
sha256sum "$DIR/config.yaml" "$DIR/auto-update.sh" "$DIR/cpa-health.py" "$DIR/cpa_policy.py" "$DIR/cpa_provider_routes.json" "$NGINX_CONF" "$FAIL2BAN_FILTER" "$FAIL2BAN_JAIL" || \
  echo "WARNING checksum_summary_failed"
echo "==catalog_summary=="
if ! curl --noproxy '*' -sS --max-time 20 -H "Authorization: Bearer $KEY" \
  http://127.0.0.1:8317/v1/models |
  python3 -c '
import json, sys
d = json.load(sys.stdin)
ids = [item.get("id", "") for item in d.get("data", [])]
print("models=" + str(len(ids)))
print("has_deepseek=" + str(any(i.startswith("deepseek-") for i in ids)))
print("has_r1=" + str(any(i.startswith("r1/") for i in ids)))
print("has_bare_luna=" + str("gpt-5.6-luna" in ids))
print("has_bare_gpt6_luna=" + str("gpt-6-luna" in ids))
print("has_ai_input_im_bare_gpt6_sol=" + str("gpt-6-sol" in ids))
print("has_ai_input_im_bare_astra=" + str("gpt-6-astra" in ids))
print("has_ai_input_im_bare_gpt56_sol=" + str("gpt-5.6-sol" in ids))
print("has_ai_input_im_image_gpt_2_5=" + str("gpt-image-2.5" in ids))
print("has_ciii_gpt6_astra=" + str("gpt-6-astra-cii" in ids))
print("has_ciii_gpt6_sol=" + str("gpt-6-sol-cii" in ids))
print("has_slot3_gpt6_sol_91=" + str("gpt-6-sol-91" in ids))
print("has_previous_gpt56_sol_terra_aliases=" + str(bool({"gpt-5.6-sol-91", "gpt-5.6-terra-91"} & set(ids))))
print("has_ciii_retired_models=" + str(bool({"codex-auto-review", "gpt-5.5", "gpt-5.6", "gpt-reserve"} & set(ids))))
print("has_glm_5_3=" + str("glm-5.3" in ids))
print("has_glm_5_3_flash=" + str("glm-5.3-flash" in ids))
print("has_retired_glm_5_3_flashx=" + str("glm-5.3-flashx" in ids))
'; then
  echo "WARNING catalog_summary_failed"
fi
echo "GUARDRAILS_APPLIED"
'@

$applyScript = $applyScript.Replace(
  "__CPA_PROVIDER_ENV_B64__",
  $providerEnvBase64
).Replace(
  "__CPA_PROVIDER_ROUTES_B64__",
  $providerRoutesBase64
).Replace(
  "__CPA_PROVIDER_ROUTES_SHA256__",
  $providerRoutesSha256
).Replace(
  "__CPA_FAIL2BAN_FILTER_B64__",
  $fail2banFilterBase64
).Replace(
  "__CPA_FAIL2BAN_FILTER_SHA256__",
  $fail2banFilterSha256
).Replace(
  "__CPA_FAIL2BAN_JAIL_B64__",
  $fail2banJailBase64
).Replace(
  "__CPA_FAIL2BAN_JAIL_SHA256__",
  $fail2banJailSha256
).Replace(
  "__CPA_UPDATER_B64__",
  $updaterBase64
).Replace(
  "__CPA_UPDATER_SHA256__",
  $updaterSha256
).Replace(
  "__CPA_HEALTH_B64__",
  $healthBase64
).Replace(
  "__CPA_HEALTH_SHA256__",
  $healthSha256
).Replace(
  "__CPA_POLICY_B64__",
  $policyBase64
).Replace(
  "__CPA_POLICY_SHA256__",
  $policySha256
)
Invoke-BwgRemoteScript -Script $applyScript -CommandTimeout 240

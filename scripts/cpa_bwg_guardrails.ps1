param(
  [string]$Profile = "bwg",
  [string]$Config = "",
  [switch]$Apply,
  [switch]$Observe,
  [switch]$RotatePath,
  [switch]$DeactivateOAuthLuna,
  [string]$ProviderEnvPath = ""
)

$ErrorActionPreference = "Stop"

if ($Profile -ne "bwg") {
  throw "This guardrail workflow is intentionally limited to the bwg profile."
}
if (@($Apply, $Observe, $RotatePath, $DeactivateOAuthLuna | Where-Object { $_ }).Count -gt 1) {
  throw "Choose exactly one of the default strict doctor, -Observe, -Apply, -RotatePath, or -DeactivateOAuthLuna."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$connectScript = Join-Path $repoRoot "connect.ps1"
$updaterPath = Join-Path $scriptDir "remote\cpa-auto-update.sh"
$healthPath = Join-Path $scriptDir "remote\cpa-health.py"
$policyPath = Join-Path $scriptDir "remote\cpa_policy.py"
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
if (-not (Test-Path -LiteralPath $fail2banFilterPath -PathType Leaf)) {
  throw "CPA fail2ban filter source was not found at $fail2banFilterPath"
}
if (-not (Test-Path -LiteralPath $fail2banJailPath -PathType Leaf)) {
  throw "CPA fail2ban jail source was not found at $fail2banJailPath"
}

$fail2banFilterBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes(
    (Get-Content -LiteralPath $fail2banFilterPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
  )
)
$fail2banJailBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes(
    (Get-Content -LiteralPath $fail2banJailPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
  )
)
$updaterBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes(
    (Get-Content -LiteralPath $updaterPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
  )
)
$healthBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes(
    (Get-Content -LiteralPath $healthPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
  )
)
$policyBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes(
    (Get-Content -LiteralPath $policyPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
  )
)

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
if grep -Fq 'limit_req=$limit_req_status limit_conn=$limit_conn_status' /etc/nginx/conf.d/cpa-gateway.conf; then
  echo safe-limit-status=OK
else
  mark_fail safe-limit-status
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
if grep -Eq '^[[:space:]]*error-logs-max-files:[[:space:]]*5[[:space:]]*$' "$DIR/config.yaml"; then
  echo error-logs-max-files=5
else
  mark_fail error-logs-max-files
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
grep -nE "^(host|port|force-model-prefix|request-retry|max-retry-credentials|max-retry-interval|save-cooldown-status|transient-error-cooldown-seconds|error-logs-max-files|usage-statistics-enabled|routing:|  strategy:|  session-affinity:|  session-affinity-ttl:|  session-affinity-subagents:|codex:|  stream-bootstrap-buffering:|  stream-bootstrap-timeout:)" "$DIR/config.yaml" || true
echo "==models-configured=="
grep -nE "^[[:space:]]+(name|prefix|alias):" "$DIR/config.yaml" || true
echo "==files=="
stat -c "%a %U %G %s %n" "$DIR/config.yaml" "$DIR/compose.yml" "$DIR/auto-update.sh" "$DIR/cpa-health.py" /etc/nginx/conf.d/cpa-gateway.conf
sha256sum "$DIR/config.yaml" "$DIR/compose.yml" "$DIR/auto-update.sh" "$DIR/cpa-health.py" /etc/nginx/conf.d/cpa-gateway.conf
echo "==timer=="
systemctl is-enabled cliproxyapi-update.timer || true
systemctl is-active cliproxyapi-update.timer || true
systemctl show cliproxyapi-update.timer -p NextElapseUSecRealtime --value || true
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

catalog_luna = "unknown"
catalog_gpt6_luna = "unknown"
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
    catalog_luna = "present" if "gpt-5.6-luna" in ids else "absent"
    catalog_gpt6_luna = "present" if "gpt-6-luna" in ids else "absent"
except Exception:
    pass

if catalog_luna == "present" or catalog_gpt6_luna == "present":
    luna_state = "available"
elif cooldown_state == "active":
    luna_state = "active_cooldown"
elif cooldown_state == "expired":
    luna_state = "stale_cooldown_suspected"
else:
    luna_state = "unavailable_unclassified"

print(f"cds_files={len(list(auth_dir.glob('*.cds')))}")
print(f"cooldown_state={cooldown_state}")
print(f"cooldown_next_retry_after={next_retry_after}")
print(f"catalog_luna={catalog_luna}")
print(f"catalog_gpt6_luna={catalog_gpt6_luna}")
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
last_1h = collections.Counter()
five_xx_local_vs_upstream = collections.Counter()
five_xx_by_client = collections.Counter()
client_503_times = collections.defaultdict(list)
abort_request_times = []
cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
cutoff_1h = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
unparsed = 0
for line in Path('/var/log/nginx/cpa_gateway.access.log').open():
    match = re.search(
        r'^(\S+) method=\S+ status=(\d{3})(?: request_time=([0-9.]+))? .*upstream_status=([^ ]+) .*'
        r'limit_req=([^ ]+) limit_conn=([^ ]+) .*time=\[([^]]+)\]',
        line,
    )
    if not match:
        unparsed += 1
        continue
    try:
        stamp = datetime.datetime.strptime(match[7], '%d/%b/%Y:%H:%M:%S %z')
    except ValueError:
        unparsed += 1
        continue
    if stamp < cutoff:
        continue
    status = match[2]
    counts[status] += 1
    upstream[match[4]] += 1
    status_upstream[f'{status}/{match[4]}'] += 1
    limit_markers[f'{match[5]}/{match[6]}'] += 1
    if stamp >= cutoff_1h:
        last_1h[status] += 1
    if status == '499' and match[3]:
        abort_request_times.append(float(match[3]))
    if status in ('500', '502', '503'):
        # request_time separates the two failure planes: a <0.5s 503 is CPA's
        # own cooldown fast-fail (auth_unavailable), a >=3s 5xx is a real
        # upstream error passed through. IPs stay masked to /16 and the retry
        # pattern is aggregate gap stats only, never an address.
        request_time = float(match[3]) if match[3] else -1.0
        bucket = ('fast_local_lt_0_5s' if 0 <= request_time < 0.5
                  else 'mid_0_5_to_3s' if request_time < 3
                  else 'slow_upstream_ge_3s')
        five_xx_local_vs_upstream[f'{status}/{bucket}'] += 1
        octets = match[1].split('.')
        client = '.'.join(octets[:2]) + '.x.x' if len(octets) == 4 else 'masked'
        five_xx_by_client[f'{client}/{status}'] += 1
        if status == '503':
            client_503_times[client].append(stamp.timestamp())
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
print(json.dumps({'statuses': dict(counts), 'upstream_statuses': dict(upstream),
                  'status_upstream': dict(status_upstream),
                  'limit_markers': dict(limit_markers),
                  'last_1h_statuses': dict(last_1h),
                  'five_xx_local_vs_upstream': dict(five_xx_local_vs_upstream),
                  'five_xx_by_client_masked': dict(five_xx_by_client),
                  'client_503_retry_pattern': retry_pattern,
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
# queue (usage-statistics-enabled + 3600s retention). Doctor is the only
# consumer: records are popped and reduced to per-model sums; no session,
# request id, or response text is printed. Observation-grade, not a gate.
if [ "${CPA_DOCTOR_CONSUME_USAGE_QUEUE:-0}" = "1" ] && [ -f "$DIR/management-key.txt" ]; then
  # The management endpoint pops records. Keep the default doctor read-only;
  # explicit consumption is reserved for a single, intentional observer.
  CACHE_QUEUE=$(curl --max-time 5 -s -H "X-Management-Key: $(cat "$DIR/management-key.txt")" "http://127.0.0.1:8317/v0/management/usage-queue?count=1000")
  if [ -n "$CACHE_QUEUE" ]; then
    printf '%s' "$CACHE_QUEUE" | python3 -c '
import json, sys
try:
    records = json.load(sys.stdin)
except (json.JSONDecodeError, ValueError):
    records = None
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
                  "coverage": "in-memory usage queue since last consumer, retention <=3600s; doctor pops records; aggregate sums only, bucketed per provider/model lane"}))
'
  else
    echo "cache_usage=UNAVAILABLE_EMPTY_RESPONSE"
  fi
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
  if [ "$SUBSTITUTIONS_7D" -gt 0 ] 2>/dev/null; then
    echo "model_substitution=WARN_SUBSTITUTION_OBSERVED"
  else
    echo "model_substitution=OK"
  fi
else
  echo "model_substitution_warnings_7d=unavailable"
  echo "model_substitution=UNAVAILABLE_VERSION"
fi
echo "model_substitution_coverage=requires CPA >= v7.3.8 and retained container logs only; upstream throttles one warn per credential/model pair per 10min; observation only, not a strict gate"
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
echo "DOCTOR_CONTRACT_OK"
'@

if ($Observe) {
  $doctorScript = $doctorScript.Replace("STRICT=1", "STRICT=0")
}

if (-not $Apply -and -not $RotatePath -and -not $DeactivateOAuthLuna) {
  Invoke-BwgRemoteScript -Script $doctorScript
  exit 0
}

$rotateScript = @'
set -Eeuo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving backups, restarts, and rollbacks.
exec 9>/opt/cliproxyapi/auto-update.lock
flock -n 9 || { echo "REFUSE cpa_busy auto-update.lock held"; exit 1; }

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
exec 9>/opt/cliproxyapi/auto-update.lock
flock -n 9 || { echo "REFUSE cpa_busy auto-update.lock held"; exit 1; }

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
# same-name GPT-6 Sol/Astra routes to ai.input.im.
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

expected = {
    "deepseek-flash",
    "glm-5.3-flash",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
}
optional = {"gpt-6-sol"}
ids = {item.get("id") for item in json.load(open(sys.argv[1])).get("data", []) if isinstance(item, dict)}
bare = {i for i in ids if isinstance(i, str) and "/" not in i}
# OAuth removal removes both Luna aliases; the stable non-OAuth routes
# (official GLM/DeepSeek and ai.input.im Sol/Terra/Astra) must survive. The
# prepared gpt-6-sol may remain absent until it is opened upstream.
raise SystemExit(
    0
    if (
        not ({"gpt-5.6-luna", "gpt-6-luna"} & ids)
        and expected <= bare
        and (optional & ids) <= bare
    )
    else 1
)
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
  Invoke-BwgRemoteScript -Script $deactivateOAuthLunaScript -CommandTimeout 240
  exit 0
}

if ([string]::IsNullOrWhiteSpace($ProviderEnvPath)) {
  $ProviderEnvPath = Join-Path $repoRoot "- 副本.env"
}
if (-not (Test-Path -LiteralPath $ProviderEnvPath -PathType Leaf)) {
  throw "Provider env source was not found at $ProviderEnvPath"
}
$providerEnvBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes(
    (Get-Content -LiteralPath $ProviderEnvPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
  )
)

$applyScript = @'
set -Eeuo pipefail

# Same lock as auto-update.sh: the daily timer and guardrail transactions
# refuse to overlap instead of interleaving backups, restarts, and rollbacks.
exec 9>/opt/cliproxyapi/auto-update.lock
flock -n 9 || { echo "REFUSE cpa_busy auto-update.lock held"; exit 1; }

DIR=/opt/cliproxyapi
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
  [ "$listen_count" -eq 1 ] &&
    [ "$ipv6_listen_count" -eq 0 ] &&
    [ "$route_count" -eq 1 ] &&
    [ "$proxy_count" -eq 1 ] &&
    [ "$fallback_count" -ge 2 ] &&
    [ "$merged_path" = "$RANDOM_PATH_BEFORE" ]
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
provider_slots = (
    (
        1,
        "ai.input.im",
        "ai.input.im",
        ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-sol", "gpt-6-astra"),
        "/v1",
    ),
    (2, "open.bigmodel.cn", "zhipu-plan", ("glm-5.3-flash",), "/api/coding/paas/v4"),
    (3, "api.deepseek.com", "deepseek", ("deepseek-flash",), ""),
)
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


def parse_required_slot(slot, expected_host, default_path):
    base_url = env_values.get(f"BASE_URL_{slot}", "")
    api_key = env_values.get(f"API_KEY_{slot}", "")
    try:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected_host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        path = parsed.path.rstrip("/")
    except (TypeError, ValueError):
        raise SystemExit(
            f"REFUSE env BASE_URL_{slot} must be exact https://{expected_host}{default_path}"
        )
    if not api_key:
        raise SystemExit(f"REFUSE env API_KEY_{slot} is empty")
    expected_path = default_path.rstrip("/")
    if path != expected_path:
        raise SystemExit(
            f"REFUSE env BASE_URL_{slot} must be exact https://{expected_host}{default_path}"
        )
    return f"https://{expected_host}{path}", api_key


def provider_host(entry):
    if not isinstance(entry, dict):
        return None
    return urlparse(str(entry.get("base-url", ""))).hostname


def model_entry(name):
    return {"name": name, "alias": name}


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


target_hosts = {host for _, host, _, _, _ in provider_slots}
legacy_hosts = {"35.213.82.91"}
target_providers = []
remaining_compatibility = []
for item in compatibility:
    host = provider_host(item)
    name = item.get("name") if isinstance(item, dict) else None
    if host in target_hosts or host in legacy_hosts or name == "relay-8003":
        continue
    remaining_compatibility.append(item)

for slot, host, name, models, default_path in provider_slots:
    base_url, api_key = parse_required_slot(slot, host, default_path)
    existing = next(
        (item for item in compatibility if provider_host(item) == host),
        None,
    )
    target_providers.append(build_provider(existing, name, base_url, api_key, models))

config_after = deepcopy(config_before)
config_after["request-retry"] = 0
config_after["error-logs-max-files"] = 5
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

# Keep all surviving Codex API-key lanes from competing with the three exact
# GPT-6 aliases. The native Codex OAuth lane owns Luna; ai.input.im owns
# Sol/Astra. Preserve every other exclusion already present on each entry.
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
        for model in ("gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-sol", "gpt-6-astra"):
            if model not in excluded_normalized:
                excluded_models.append(model)
                excluded_normalized.add(model)

# Keep GPT-6 Luna on the existing Codex OAuth lane while pinning the
# same-name Sol/Astra aliases to ai.input.im. Replace only the old family
# wildcard; preserve all unrelated OAuth exclusions and refuse broader rules
# that would need an unsafe expansion to make Luna visible.
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
    codex_exclusions_after.append(pattern)
for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-sol", "gpt-6-astra"):
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
    "limit_req zone=cpa_rl burst=20 nodelay;",
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

log_format = (
    "log_format cpa_safe '$remote_addr method=$request_method "
    "status=$status request_time=$request_time "
    "upstream_status=$upstream_status "
    "upstream_time=$upstream_response_time bytes=$body_bytes_sent "
    "limit_req=$limit_req_status limit_conn=$limit_conn_status "
    "time=[$time_local] auth_status=$cpa_auth_status';\n"
)
legacy_log_format = log_format.replace(" auth_status=$cpa_auth_status", "")
previous_log_format = log_format.replace(
    " limit_req=$limit_req_status limit_conn=$limit_conn_status", ""
)
previous_legacy_log_format = previous_log_format.replace(
    " auth_status=$cpa_auth_status", ""
)
# Adding auth_status requires the separately verified auth_request location.
if 'auth_request /_cpa_auth;' not in nginx:
    log_format = legacy_log_format
if "log_format cpa_safe " not in nginx:
  nginx = log_format + nginx
elif log_format not in nginx:
    old_formats = [
        previous_legacy_log_format,
        previous_log_format,
    ] if 'auth_request /_cpa_auth;' not in nginx else [
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
}
write_base64_file "__CPA_FAIL2BAN_FILTER_B64__" "$FAIL2BAN_FILTER" 644 || {
  restore_all
  echo "ROLLBACK fail2ban_filter_write"
  exit 1
}
write_base64_file "__CPA_FAIL2BAN_JAIL_B64__" "$FAIL2BAN_JAIL" 644 || {
  restore_all
  echo "ROLLBACK fail2ban_jail_write"
  exit 1
}
write_base64_file "__CPA_UPDATER_B64__" "$DIR/auto-update.sh" 700 || {
  restore_all
  echo "ROLLBACK updater_projection"
  exit 1
}
write_base64_file "__CPA_HEALTH_B64__" "$DIR/cpa-health.py" 644 || {
  restore_all
  echo "ROLLBACK health_projection"
  exit 1
}
write_base64_file "__CPA_POLICY_B64__" "$DIR/cpa_policy.py" 644 || {
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
sha256sum "$DIR/config.yaml" "$DIR/auto-update.sh" "$DIR/cpa-health.py" "$DIR/cpa_policy.py" "$NGINX_CONF" "$FAIL2BAN_FILTER" "$FAIL2BAN_JAIL" || \
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
print("has_ai_input_im_bare_sol=" + str("gpt-5.6-sol" in ids))
print("has_ai_input_im_bare_terra=" + str("gpt-5.6-terra" in ids))
print("has_ai_input_im_bare_gpt6_sol=" + str("gpt-6-sol" in ids))
print("has_ai_input_im_bare_astra=" + str("gpt-6-astra" in ids))
print("has_glm=" + str("glm-5.3-flash" in ids))
'; then
  echo "WARNING catalog_summary_failed"
fi
echo "GUARDRAILS_APPLIED"
'@

$applyScript = $applyScript.Replace(
  "__CPA_PROVIDER_ENV_B64__",
  $providerEnvBase64
).Replace(
  "__CPA_FAIL2BAN_FILTER_B64__",
  $fail2banFilterBase64
).Replace(
  "__CPA_FAIL2BAN_JAIL_B64__",
  $fail2banJailBase64
).Replace(
  "__CPA_UPDATER_B64__",
  $updaterBase64
).Replace(
  "__CPA_HEALTH_B64__",
  $healthBase64
).Replace(
  "__CPA_POLICY_B64__",
  $policyBase64
)
Invoke-BwgRemoteScript -Script $applyScript -CommandTimeout 240

param(
  [string]$Profile = "bwg",
  [switch]$Apply,
  [switch]$Observe,
  [switch]$RotatePath
)

$ErrorActionPreference = "Stop"

if ($Profile -ne "bwg") {
  throw "This guardrail workflow is intentionally limited to the bwg profile."
}
if (($Apply -and $Observe) -or ($RotatePath -and ($Apply -or $Observe))) {
  throw "Choose exactly one of the default strict doctor, -Observe, -Apply, or -RotatePath."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$connectScript = Join-Path $repoRoot "connect.ps1"

if (-not (Test-Path -LiteralPath $connectScript -PathType Leaf)) {
  throw "connect.ps1 was not found at $connectScript"
}

function Invoke-BwgRemoteScript {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Script,
    [int]$CommandTimeout = 180
  )

  $payload = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($Script)
  )
  $remoteCommand = "printf %s $payload | base64 -d | bash"
  & $connectScript `
    -Profile $Profile `
    -StrictHostKeyChecking `
    -Command $remoteCommand `
    -CommandTimeout $CommandTimeout
  if ($LASTEXITCODE -ne 0) {
    throw "Remote BWG command failed with exit code $LASTEXITCODE."
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
if docker inspect --format "status={{.State.Status}} restart={{.RestartCount}} image={{.Config.Image}}" cli-proxy-api; then
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
if grep -Eq '^[[:space:]]*allow-remote:[[:space:]]*false' "$DIR/config.yaml"; then
  echo management-remote=DISABLED
else
  mark_fail management-remote
fi
if grep -RqsE 'identity-confuse[[:space:]]*:[[:space:]]*true' "$DIR/config.yaml" "$DIR/auth"; then
  mark_fail identity-confuse
else
  echo identity-confuse=ABSENT
fi
PORT_JSON=$(docker inspect --format '{{json .HostConfig.PortBindings}}' cli-proxy-api 2>/dev/null || true)
if [ -n "$PORT_JSON" ] && python3 - "$PORT_JSON" <<'PY'
import json
import sys

try:
    bindings = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    raise SystemExit(1)
items = bindings.get("8317/tcp") or []
raise SystemExit(
    0
    if any(
        item.get("HostIp") == "127.0.0.1"
        and item.get("HostPort") == "8317"
        for item in items
    )
    else 1
)
PY
then
  echo cpa-port-binding=loopback-only
else
  mark_fail cpa-port-binding
fi
echo "==nginx-merged-contract=="
NGINX_DUMP=$(mktemp)
if nginx -T >"$NGINX_DUMP" 2>&1; then
  echo merged-config=OK
  listen_count=$(grep -Ec '^[[:space:]]*listen[[:space:]]+8443[[:space:]]+ssl;' "$NGINX_DUMP")
  route_count=$(grep -Ec 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/\(\.\*\)\$' "$NGINX_DUMP")
  proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8317/v1/\$1\$is_args\$args;' "$NGINX_DUMP")
  fallback_count=$(grep -Ec 'location[[:space:]]*/[[:space:]]*\{|return[[:space:]]+404;' "$NGINX_DUMP")
  if [ "$listen_count" -eq 1 ]; then echo public-listen-count=1; else mark_fail public-listen-count; fi
  if [ "$route_count" -eq 1 ]; then echo random-route-count=1; else mark_fail random-route-count; fi
  if [ "$proxy_count" -eq 1 ]; then echo loopback-proxy-count=1; else mark_fail loopback-proxy-count; fi
  if [ "$fallback_count" -ge 2 ]; then echo fallback-404=present; else mark_fail fallback-404; fi
else
  mark_fail merged-config
fi
rm -f "$NGINX_DUMP"
echo "==public-route-contract=="
SERVER_NAME=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' /etc/nginx/conf.d/cpa-gateway.conf)
PREFIX=$(grep -oE '/[0-9a-f]{16}/v1/' /etc/nginx/conf.d/cpa-gateway.conf | head -n 1 | cut -d/ -f2)
if [ -n "$SERVER_NAME" ] && [ -n "$PREFIX" ]; then
  PUBLIC_BASE="https://$SERVER_NAME:8443"
  valid_status=$(curl -sS --connect-timeout 5 --max-time 10 --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' "$PUBLIC_BASE/$PREFIX/v1/models" 2>/dev/null || echo 000)
  bare_status=$(curl -sS --connect-timeout 5 --max-time 10 --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' "$PUBLIC_BASE/v1/models" 2>/dev/null || echo 000)
  WRONG_PREFIX=0000000000000000
  if [ "$WRONG_PREFIX" = "$PREFIX" ]; then WRONG_PREFIX=ffffffffffffffff; fi
  wrong_status=$(curl -sS --connect-timeout 5 --max-time 10 --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' "$PUBLIC_BASE/$WRONG_PREFIX/v1/models" 2>/dev/null || echo 000)
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
grep -nE "^(host|port|force-model-prefix|request-retry|max-retry-credentials|max-retry-interval|save-cooldown-status|transient-error-cooldown-seconds|usage-statistics-enabled|routing:|  strategy:|  session-affinity:|  session-affinity-ttl:)" "$DIR/config.yaml" || true
echo "==models-configured=="
grep -nE "^[[:space:]]+(name|prefix|alias):" "$DIR/config.yaml" || true
echo "==files=="
stat -c "%a %U %G %s %n" "$DIR/config.yaml" "$DIR/compose.yml" "$DIR/auto-update.sh" /etc/nginx/conf.d/cpa-gateway.conf
sha256sum "$DIR/config.yaml" "$DIR/compose.yml" "$DIR/auto-update.sh" /etc/nginx/conf.d/cpa-gateway.conf
echo "==timer=="
systemctl is-enabled cliproxyapi-update.timer || true
systemctl is-active cliproxyapi-update.timer || true
systemctl show cliproxyapi-update.timer -p NextElapseUSecRealtime --value || true
echo "==auth-modes=="
find "$DIR/auth" -maxdepth 1 -type f -printf "%m %f\n" | sort
echo "==error-counts-24h=="
for code in 401 403 408 429 500 502 503 504; do
  printf "%s=" "$code"
  docker logs --since 24h cli-proxy-api 2>&1 | grep -c "$code" || true
done
echo "==syntax=="
if bash -n "$DIR/auto-update.sh"; then echo updater=OK; else mark_fail updater; fi
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

if (-not $Apply -and -not $RotatePath) {
  Invoke-BwgRemoteScript -Script $doctorScript
  exit 0
}

$rotateScript = @'
set -Eeuo pipefail

NGINX_CONF=/etc/nginx/conf.d/cpa-gateway.conf
BK=/root/cpa-guardrails-path-backup-$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$BK"
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

restore_path() {
  cp -a "$BK/cpa-gateway.conf" "$NGINX_CONF"
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || true
  fi
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

SERVER_NAME=$(awk '/^[[:space:]]*server_name[[:space:]]/{gsub(";", "", $2); print $2; exit}' "$NGINX_CONF")
if [ -z "$SERVER_NAME" ]; then
  restore_path
  echo "ROLLBACK missing_server_name"
  exit 1
fi
PUBLIC_BASE="https://$SERVER_NAME:8443"
new_status=$(curl -sS --connect-timeout 5 --max-time 10 \
  --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
  "$PUBLIC_BASE/$NEW_PATH/v1/models" 2>/dev/null || echo 000)
old_status=$(curl -sS --connect-timeout 5 --max-time 10 \
  --resolve "$SERVER_NAME:8443:127.0.0.1" -o /dev/null -w '%{http_code}' \
  "$PUBLIC_BASE/$OLD_PATH/v1/models" 2>/dev/null || echo 000)
if [ "$new_status" != "401" ] || [ "$old_status" != "404" ]; then
  restore_path
  echo "ROLLBACK path_probe new=$new_status old=$old_status"
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

$applyScript = @'
set -euo pipefail

DIR=/opt/cliproxyapi
NGINX_CONF=/etc/nginx/conf.d/cpa-gateway.conf
BK=/root/cpa-guardrails-backup-$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$BK"
cp -a "$DIR/config.yaml" "$BK/config.yaml"
cp -a "$DIR/compose.yml" "$BK/compose.yml"
cp -a "$DIR/auto-update.sh" "$BK/auto-update.sh"
cp -a "$DIR/auth" "$BK/auth"
cp -a "$NGINX_CONF" "$BK/cpa-gateway.conf"
chmod 700 "$BK"

restore_all() {
  cp -a "$BK/config.yaml" "$DIR/config.yaml"
  cp -a "$BK/compose.yml" "$DIR/compose.yml"
  cp -a "$BK/auto-update.sh" "$DIR/auto-update.sh"
  cp -a "$BK/cpa-gateway.conf" "$NGINX_CONF"
  chmod 600 "$DIR/config.yaml"
  chmod 700 "$DIR/auto-update.sh"
  docker restart cli-proxy-api >/dev/null 2>&1 || true
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || true
  fi
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
if ! grep -Eq '^[[:space:]]*allow-remote:[[:space:]]*false' "$DIR/config.yaml"; then
  echo "REFUSE remote management is not disabled"
  exit 1
fi
if grep -RqsE 'identity-confuse[[:space:]]*:[[:space:]]*true' "$DIR/config.yaml" "$DIR/auth"; then
  echo "REFUSE identity-confuse is enabled"
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
items = bindings.get("8317/tcp") or []
raise SystemExit(
    0
    if any(
        item.get("HostIp") == "127.0.0.1"
        and item.get("HostPort") == "8317"
        for item in items
    )
    else 1
)
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
  route_count=$(grep -Ec 'location[[:space:]]+~[[:space:]]+\^/[0-9a-f]{16}/v1/\(\.\*\)\$' "$dump_file")
  proxy_count=$(grep -Ec 'proxy_pass[[:space:]]+http://127\.0\.0\.1:8317/v1/\$1\$is_args\$args;' "$dump_file")
  fallback_count=$(grep -Ec 'location[[:space:]]*/[[:space:]]*\{|return[[:space:]]+404;' "$dump_file")
  merged_path=$(grep -oE '/[0-9a-f]{16}/v1/' "$dump_file" | head -n 1 | cut -d/ -f2)
  rm -f "$dump_file"
  [ "$listen_count" -eq 1 ] &&
    [ "$route_count" -eq 1 ] &&
    [ "$proxy_count" -eq 1 ] &&
    [ "$fallback_count" -ge 2 ] &&
    [ "$merged_path" = "$RANDOM_PATH_BEFORE" ]
}

if ! assert_merged_nginx_route_contract; then
  echo "REFUSE unexpected merged Nginx route contract"
  exit 1
fi

if ! python3 - <<'PY'
from copy import deepcopy
from pathlib import Path
import os
import tempfile
import yaml


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
if config_before.get("save-cooldown-status") is not True:
    raise SystemExit("save-cooldown-status must remain true")

config_after = deepcopy(config_before)
config_after["request-retry"] = 0

compat_before = config_before.get("openai-compatibility", [])
if not isinstance(compat_before, list):
    raise SystemExit("openai-compatibility is not a list")
deepseek_entries = [
    item for item in compat_before
    if isinstance(item, dict) and item.get("name") == "deepseek"
]
if len(deepseek_entries) > 1:
    raise SystemExit("multiple deepseek providers found")
config_after["openai-compatibility"] = [
    item for item in compat_before
    if not (isinstance(item, dict) and item.get("name") == "deepseek")
]

codex_before = config_before.get("codex-api-key", [])
if not isinstance(codex_before, list):
    raise SystemExit("codex-api-key is not a list")
r2_entries = [
    item for item in codex_before
    if isinstance(item, dict) and item.get("prefix") == "r2"
]
if len(r2_entries) > 1:
    raise SystemExit("multiple r2 credentials found")
config_after["codex-api-key"] = [
    item for item in codex_before
    if not (isinstance(item, dict) and item.get("prefix") == "r2")
]

expected = deepcopy(config_before)
expected["request-retry"] = 0
expected["openai-compatibility"] = config_after["openai-compatibility"]
expected["codex-api-key"] = config_after["codex-api-key"]
if config_after != expected:
    raise SystemExit("config change exceeded the approved field/block set")

candidate = yaml.safe_dump(
    config_after,
    allow_unicode=True,
    default_flow_style=False,
    sort_keys=False,
)
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
]
for anchor in required:
    if anchor not in nginx:
        raise SystemExit(f"expected Nginx guardrail missing: {anchor}")

log_format = (
    "log_format cpa_safe '$remote_addr method=$request_method "
    "status=$status request_time=$request_time "
    "upstream_status=$upstream_status "
    "upstream_time=$upstream_response_time bytes=$body_bytes_sent';\n"
)
if "log_format cpa_safe " not in nginx:
    nginx = log_format + nginx
elif log_format not in nginx:
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
    f"removed_deepseek={len(deepseek_entries)} "
    f"removed_r2={len(r2_entries)} request_retry={config_after['request-retry']}"
)
PY
then
  restore_all
  echo "ROLLBACK config_or_updater"
  exit 1
fi

chmod 600 "$DIR/config.yaml"
chmod 700 "$DIR/auto-update.sh"

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

if ! docker restart cli-proxy-api >/dev/null; then
  restore_all
  echo "ROLLBACK cpa_restart"
  exit 1
fi

KEY=$(grep -A1 "^api-keys:" "$DIR/config.yaml" | tail -n 1 | sed -E "s/[^0-9a-f]//g")
if [ -z "$KEY" ]; then
  restore_all
  echo "ROLLBACK missing_client_key"
  exit 1
fi
READY=000
for _ in $(seq 1 30); do
  READY=$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' \
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
if ! assert_merged_nginx_route_contract; then
  restore_all
  echo "ROLLBACK merged_nginx_route_contract_after_reload"
  exit 1
fi
if ! ss -ltn | grep -Eq '0\.0\.0\.0:8443[[:space:]]'; then
  restore_all
  echo "ROLLBACK nginx_public_listener_missing"
  exit 1
fi
if ! ss -ltn | grep -Eq '127\.0\.0\.1:8317[[:space:]]'; then
  restore_all
  echo "ROLLBACK cpa_loopback_listener_missing"
  exit 1
fi

if ! logrotate -d /etc/logrotate.conf >/tmp/cpa-logrotate-test.log 2>&1; then
  restore_all
  echo "ROLLBACK logrotate_syntax"
  tail -n 5 /tmp/cpa-logrotate-test.log
  exit 1
fi

echo "BACKUP_DIR=$BK"
echo "READY_STATUS=$READY"
sha256sum "$DIR/config.yaml" "$DIR/auto-update.sh" "$NGINX_CONF"
echo "==catalog_summary=="
curl -sS --max-time 20 -H "Authorization: Bearer $KEY" \
  http://127.0.0.1:8317/v1/models |
  python3 -c '
import json, sys
d = json.load(sys.stdin)
ids = [item.get("id", "") for item in d.get("data", [])]
print("models=" + str(len(ids)))
print("has_r2=" + str(any(i.startswith("r2/") for i in ids)))
print("has_deepseek=" + str(any(i.startswith("deepseek-") for i in ids)))
print("has_r1=" + str(any(i.startswith("r1/") for i in ids)))
print("has_oauth_luna=" + str("gpt-5.6-luna" in ids))
print("has_glm=" + str("glm-5.3-flash" in ids))
'
echo "GUARDRAILS_APPLIED"
'@

Invoke-BwgRemoteScript -Script $applyScript -CommandTimeout 240

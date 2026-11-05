#requires -Version 7
# Controlled host-candidate replay for WorkBuddy's CN/INTL scheduler boundary.
# Synthetic tokens remain invalid. No current token is read; production
# config and credentials stay unchanged.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$contractModel = 'deepseek-v4.1-flash'
$command = @'
set -Eeuo pipefail
umask 077
CONTRACT_MODEL='__CONTRACT_MODEL__'
export CONTRACT_MODEL
IMAGE_REF='eceasy/cli-proxy-api:v8.0.55@sha256:afe1135e8d8752867b73d717c1ca8ed27c44bfe8c6e1a2bebf228dba98889eac'
PLUGIN_SHA=9e335b13375392e5ffa744b4c38b9975684fed784ac4aad4b5d956abf62b33c4
ROOT=$(mktemp -d /root/wb-cn-contract-XXXXXXXX)
EXPECTED_ROOT=$ROOT
API_KEY=fixture-key-$(python3 -c 'import secrets; print(secrets.token_hex(20))')
export API_KEY
cleanup() {
  cd /
  if [ -n "${pid:-}" ]; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
  if [ -n "${EXPECTED_ROOT:-}" ] &&
     [ "${EXPECTED_ROOT#/root/wb-cn-contract-}" != "$EXPECTED_ROOT" ] &&
     [ -f "$EXPECTED_ROOT/fixture-only" ]; then
rm -rf --one-file-system -- "$EXPECTED_ROOT"
  fi
}
trap cleanup EXIT
echo "FIXTURE_ROOT=$EXPECTED_ROOT"
mkdir -p "$ROOT/tmp" "$ROOT/run" "$ROOT/state" "$ROOT/auth" "$ROOT/plugins"
touch "$ROOT/fixture-only"
cp -a /root/wb-replay-artifacts/workbuddy-replay-9e335b13375392e5ffa744b4c38b9975684fed784ac4aad4b5d956abf62b33c4.so \
  "$ROOT/plugins/workbuddy.so"
[ "$(sha256sum "$ROOT/plugins/workbuddy.so" | cut -d ' ' -f 1)" = "$PLUGIN_SHA" ] || {
  echo REFUSE_PLUGIN_SHA_MISMATCH
  exit 20
}
cd "$ROOT"
python3 - <<'PY'
import json
import os
import secrets
from pathlib import Path

root = Path.cwd()

def fixture(uid, domain, region):
    return {
        'type': 'workbuddy', 'auth_kind': 'oauth', 'provider': 'workbuddy', 'logo': '',
        'account': {'uid': uid, 'nickname': f'fixture-{region}'},
        'auth': {
            'accessToken': 'fixture-api-token-' + secrets.token_hex(24),
            'refreshToken': 'fixture-session-token-' + secrets.token_hex(24),
            'domain': domain, 'region': region,
            'expiresAt': 4102444800000, 'loginPlatform': 'ide',
        },
        'disabled': False, 'note': 'fixture invalid token',
    }

(root / 'auth/workbuddy-intl-fixture.json').write_text(
    json.dumps(fixture('fixture-intl-uid', 'www.codebuddy.ai', 'intl'), separators=(',', ':')))
(root / 'auth/workbuddy-local-replay-cn-fixture.json').write_text(
    json.dumps(fixture('fixture-cn-uid', 'www.codebuddy.cn', 'cn'), separators=(',', ':')))
PY
chmod 600 "$ROOT/auth/workbuddy-intl-fixture.json" \
  "$ROOT/auth/workbuddy-local-replay-cn-fixture.json"
python3 - <<'PY'
import json
from pathlib import Path
for p in sorted(Path.cwd().glob('auth/workbuddy-*.json')):
    d=json.loads(p.read_text())
    print('FIXTURE_FILE='+p.name+' REGION='+d['auth']['region']+
          ' DOMAIN='+d['auth']['domain']+' BYTES='+str(p.stat().st_size))
PY
cat >"$ROOT/config.yaml" <<'YAML'
host: "0.0.0.0"
port: 18319
access:
  api-keys:
    - "__FIXTURE_API_KEY__"
oauth:
  auth-dir: "auth"
routing:
  strategy: "fill-first"
  retry:
    request-retry: 0
    max-retry-credentials: 0
# The synthetic INT network failure must stay request-scoped.
plugins:
  enabled: true
  dir: "plugins"
  configs:
    workbuddy:
      enabled: true
      scheduler_mode: credits
      scheduler_priority_reclaim: true
      account_max_concurrent: 1
      identity_switch_on_429: false
      checkin_auto: false
      tasks_auto: false
      lifecycle_auto: false
      token_keepalive: false
      models_cn: "deepseek-v4.1-flash"
      models_intl: "deepseek-v4.1-flash,deepseek-v4.1-flash-sg"
YAML
cd "$ROOT"
sed -i "s|__FIXTURE_API_KEY__|$API_KEY|" "$ROOT/config.yaml"
chmod 600 "$ROOT/config.yaml"
echo 'CONFIG_BEFORE_LAUNCH'
sed -n '1,18p' "$ROOT/config.yaml" | sed -E 's/(Bearer|fixture-key-)[^" ]*/<redacted>/g'
[ "$(pwd -P)" = "$EXPECTED_ROOT" ] && [ -f "$EXPECTED_ROOT/fixture-only" ] || {
  echo REFUSE_HOST_CWD_ESCAPE
  exit 20
}
echo "HOST_FIXTURE=$(pwd -P) HOST_FIXTURE_MARKER=$(stat -c %i fixture-only)"
cid=$(docker create "$IMAGE_REF")
docker cp "$cid:/CLIProxyAPI/CLIProxyAPI" "$ROOT/CLIProxyAPI"
docker rm "$cid" >/dev/null
chmod +x "$ROOT/CLIProxyAPI"
fixture_root=$ROOT
export fixture_root
unshare --mount /bin/bash -s <<'INNER'
set -Eeuo pipefail
mount --make-rprivate /
mount --bind "$fixture_root" /opt/cliproxyapi
mount --bind "$fixture_root/tmp" /tmp
mount --bind "$fixture_root/run" /run
mount --bind "$fixture_root/state" /var/lib/vps-ssh-launcher
cd /opt/cliproxyapi
[ -f fixture-only ] || { echo REFUSE_NAMESPACE_FIXTURE_MISSING; exit 20; }
echo "NAMESPACE_FIXTURE=$(pwd -P) NAMESPACE_FIXTURE_MARKER=$(stat -c %i fixture-only)"
./CLIProxyAPI -config config.yaml >runtime.log 2>&1 &
pid=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS http://127.0.0.1:18319/v1/models \
    -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 && break
  sleep 1
done
grep 'plugin registered plugin_id=workbuddy .*version=0.9.85' runtime.log >/dev/null
python3 - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

key = os.environ['API_KEY']
request = urllib.request.Request(
    'http://127.0.0.1:18319/v1/chat/completions',
    data=json.dumps({
        'model': os.environ['CONTRACT_MODEL'],
        'stream': False,
        'messages': [{'role': 'user', 'content': 'Return PONG only.'}],
    }).encode(),
    headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
)
started = time.monotonic()
try:
    with urllib.request.urlopen(request, timeout=45) as response:
        status = response.status
        raw = response.read()
except urllib.error.HTTPError as error:
    status = error.code
    raw = error.read()
print(f'CANDIDATE_REPLAY_STATUS={status} elapsed_ms={round((time.monotonic()-started)*1000)}')
body=raw.decode('utf-8', 'replace')
print('CANDIDATE_REPLAY_BODY=' + body[:1200])
raise SystemExit(0 if status == 200 and '"content":"PONG"' in body else 30)
PY
kill "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
pid=''
INNER
grep -E "6004|14012|11140|invalid_api_key|scheduler|auth" "$ROOT/runtime.log" |
  sed -E "s/(fixture-key-|fixture-invalid-)[A-Za-z0-9_-]*/<redacted>/g" |
  tail -n 80 | grep -vF 'superseded during computation' || true
grep -q 'auth_file=workbuddy-intl-fixture.json' "$ROOT/runtime.log"
echo WORKBUDDY_CN_ISOLATION_ACCEPTANCE=PASS
'@
$command = $command.Replace('__CONTRACT_MODEL__', $contractModel)
if ($command.Contains('__CONTRACT_MODEL__')) {
  throw 'Contract model placeholder was not replaced.'
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 240

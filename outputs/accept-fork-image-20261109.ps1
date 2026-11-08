#requires -Version 7
# Controlled live acceptance for the relabelled fork-image generation.
#  1. The real updater --check must resolve the fork tag and stay read-only.
#  2. The fork-image canary must refuse an image-only update by name, while a
#     plain official tag (no fork, no bind mount) must NOT be refused -- the
#     refusal has to be precise, not a blanket block.
#  3. Public full chain: nginx capability path -> admission -> CPA -> upstream,
#     one streamed request per lane, sequential, no retries.
# The client key and the capability path are read on bwg only; neither is
# echoed, and no response body is printed.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
DIR=/opt/cliproxyapi
echo '=== 1. updater --check (read-only)'
timeout 180 bash "$DIR/auto-update.sh" --check 2>&1 | tail -5

echo '=== 2. canary refusal semantics (extracted live function)'
sed -n '/^require_runtime_binary_canary() {/,/^}/p' "$DIR/auto-update.sh" > /tmp/canary-fn.sh
[ -s /tmp/canary-fn.sh ]
probe() {
  local label=$1 compose_body=$2 expect=$3
  local tmp
  tmp=$(mktemp -d /tmp/canary-XXXXXX)
  printf '%s\n' "$compose_body" > "$tmp/compose.yml"
  # shellcheck disable=SC1091
  ( set -eu; DIR="$tmp"; . /tmp/canary-fn.sh
    set +e
    out=$(require_runtime_binary_canary 2>&1); rc=$?
    echo "$label rc=$rc out=$out"
    [ "$rc" = "$expect" ] || echo "$label EXPECTED_$expect_GOT_$rc" ) || echo "$label SUBSHELL_FAILED"
  rm -rf -- "$tmp"
}
probe fork_image 'services:
  cli-proxy-api:
    image: cli-proxy-api-fork:v8.0.61-gemini-raw' 77
probe bind_mount 'services:
  cli-proxy-api:
    image: eceasy/cli-proxy-api:v8.0.61
    volumes:
      - ./CLIProxyAPI-patched:/CLIProxyAPI/CLIProxyAPI:ro' 77
probe plain_official 'services:
  cli-proxy-api:
    image: eceasy/cli-proxy-api:v8.0.62' 0

echo '=== 3. public full-chain lane singles'
API_KEY=$(python3 - <<'PY'
import yaml
from pathlib import Path
print(yaml.safe_load(Path('/opt/cliproxyapi/config.yaml').read_text())['access']['api-keys'][0])
PY
)
PREFIX=$(grep -oE '/[0-9a-f]{16}/v1/' /etc/nginx/conf.d/cpa-gateway.conf | head -1 | tr -d '/')
[ -n "$PREFIX" ]
python3 - "$API_KEY" "$PREFIX" <<'PY'
import json, ssl, sys, time, urllib.error, urllib.request
key, prefix = sys.argv[1], sys.argv[2]
ctx = ssl._create_unverified_context()
base = 'https://127.0.0.1:8443/%s/v1' % prefix
lanes = [
    ('antigravity', 'gemini-3.8-flash-high'),
    ('workbuddy', 'deepseek-v4.1-flash'),
    ('codex', 'gpt-6.1-sol'),
    ('zhipu', 'glm-5.3-flash'),
]
rc = 0
for name, model in lanes:
    body = json.dumps({'model': model, 'stream': True,
                       'messages': [{'role': 'user', 'content': 'Return the word PONG only.'}]}).encode()
    req = urllib.request.Request(base + '/chat/completions', data=body, method='POST',
                                 headers={'Authorization': 'Bearer ' + key,
                                          'Content-Type': 'application/json'})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180, context=ctx) as response:
            status = response.status
            text = response.read().decode(errors='replace')
    except urllib.error.HTTPError as exc:
        print('public_%s=%s t=%.2fs' % (name, exc.code, time.time() - started))
        rc = 30
        continue
    except OSError as exc:
        print('public_%s=transport:%s' % (name, type(exc).__name__))
        rc = 30
        continue
    print('public_%s=%s t=%.2fs pong=%s' % (name, status, time.time() - started, 'PONG' in text.upper()))
    if status != 200:
        rc = 30
raise SystemExit(rc)
PY
echo PUBLIC_LANE_ACCEPTANCE=PASS
rm -f /tmp/canary-fn.sh
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 400 -HardTimeoutSeconds 900

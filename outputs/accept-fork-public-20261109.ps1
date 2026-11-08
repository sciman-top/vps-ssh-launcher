#requires -Version 7
# Public full-chain acceptance: nginx capability path -> admission -> CPA ->
# upstream, one streamed request per lane, sequential, no retries.
# The capability path and client key are read on bwg and never echoed; no
# response body is printed, only structural facts.
#
# Text matching note: SSE chunks split words arbitrarily -- a reply of PONG
# routinely arrives as "P" then "ONG". The old check stripped each delta
# before joining, which turned PONG into "P ONG" and reported pong=false for
# a perfectly healthy lane. Concatenate raw deltas, strip once at the end.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
API_KEY=$(python3 - <<'PY'
import yaml
from pathlib import Path
print(yaml.safe_load(Path('/opt/cliproxyapi/config.yaml').read_text())['access']['api-keys'][0])
PY
)
PREFIX=$(grep -oE '/[0-9a-f]{16}/v1/' /etc/nginx/conf.d/cpa-gateway.conf | head -1 | sed -e 's|^/||' -e 's|/v1/$||')
PREFIX_LEN=$(printf '%s' "$PREFIX" | wc -c)
[ "$PREFIX_LEN" = 16 ] || { echo "PREFIX_LEN_UNEXPECTED $PREFIX_LEN"; exit 20; }
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
            raw = response.read().decode(errors='replace')
    except urllib.error.HTTPError as exc:
        print('public_%s=%s t=%.2fs' % (name, exc.code, time.time() - started))
        rc = 30
        continue
    except OSError as exc:
        print('public_%s=transport:%s' % (name, type(exc).__name__))
        rc = 30
        continue
    pieces = []
    terminal = 0
    for line in raw.splitlines():
        if not line.startswith('data: '):
            continue
        payload = line[6:].strip()
        if payload == '[DONE]':
            terminal += 1
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if event.get('type') in ('response.completed', 'response.incomplete', 'response.failed'):
            terminal += 1
        for choice in event.get('choices') or []:
            value = (choice.get('delta') or {}).get('content')
            if isinstance(value, str):
                pieces.append(value)
    text = ''.join(pieces).strip()
    saw = 'PONG' in text.upper()
    print('public_%s=%s t=%.2fs terminal=%d text_len=%d pong=%s'
          % (name, status, time.time() - started, terminal, len(text), saw))
    if status != 200 or not terminal or not saw:
        rc = 30
raise SystemExit(rc)
PY
echo PUBLIC_LANE_ACCEPTANCE=PASS
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 400 -HardTimeoutSeconds 900

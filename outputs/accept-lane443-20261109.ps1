#requires -Version 7
# Acceptance for the 443 lane -- the path the ChatGPT desktop actually uses.
#
# Why this file exists: the 8443 lane (cpa-gateway.conf) and the 443 lane
# (subscribe.conf) carry DIFFERENT capability paths and different limits.
# Verifying only 8443 proves the gateway works but says nothing about the
# desktop's real path: fq.sciman.top:443 -> xray dokodemo-door -> VLESS
# REALITY -> nginx listen 33387. This script trains on that lane.
#
# The capability path and client key are read on bwg and never echoed; no
# response body is printed, only structural facts.
#
# 2026-11-09 finding: the first version of this probe connected to
# 127.0.0.1:33387 and every lane reported transport:URLError. The vhost is
# name-based (server_name fq.sciman.top) with no default_server, so a bare-IP
# handshake is rejected with "tlsv1 unrecognized name". That is a probe defect,
# not a gateway defect -- verified by curl --resolve, which returns 200. The
# probe therefore pins the name to loopback and keeps the SNI intact instead of
# loosening the check.
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
# The 443 lane's capability path lives in subscribe.conf, not cpa-gateway.conf.
PREFIX_443=$(grep -oE '/[0-9a-f]{16}/v1/' /etc/nginx/conf.d/subscribe.conf | head -1 | sed -e 's|^/||' -e 's|/v1/$||')
PREFIX_8443=$(grep -oE '/[0-9a-f]{16}/v1/' /etc/nginx/conf.d/cpa-gateway.conf | head -1 | sed -e 's|^/||' -e 's|/v1/$||')
LEN=$(printf '%s' "$PREFIX_443" | wc -c)
[ "$LEN" = 16 ] || { echo "PREFIX_443_LEN_UNEXPECTED $LEN"; exit 20; }
if [ "$PREFIX_443" = "$PREFIX_8443" ]; then
  echo 'PREFIX_SHARED_BETWEEN_LANES=yes'
else
  echo 'PREFIX_SHARED_BETWEEN_LANES=no (expected: the lanes are independent)'
fi
python3 - "$API_KEY" "$PREFIX_443" <<'PY'
import json, socket, ssl, sys, time, urllib.error, urllib.request
key, prefix = sys.argv[1], sys.argv[2]
ctx = ssl._create_unverified_context()
# Pin the SNI host to loopback so we exercise the local nginx vhost rather
# than the public address the REALITY target would use.
_orig_getaddrinfo = socket.getaddrinfo
def _loopback(host, port, *args, **kwargs):
    if host == 'fq.sciman.top':
        host = '127.0.0.1'
    return _orig_getaddrinfo(host, port, *args, **kwargs)
socket.getaddrinfo = _loopback
# 33387 is the nginx TLS listener the xray REALITY target forwards to.
# It is a name-based vhost (server_name fq.sciman.top) with NO default_server,
# so the TLS handshake needs the real SNI: connecting to 127.0.0.1 directly
# yields "tlsv1 unrecognized name" and a bogus transport failure. Resolve the
# name to loopback instead of weakening the check.
base = 'https://fq.sciman.top:33387/%s/v1' % prefix
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
        print('lane443_%s=%s t=%.2fs' % (name, exc.code, time.time() - started))
        rc = 30
        continue
    except OSError as exc:
        print('lane443_%s=transport:%s' % (name, type(exc).__name__))
        rc = 30
        continue
    pieces, terminal = [], 0
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
    print('lane443_%s=%s t=%.2fs terminal=%d text_len=%d pong=%s'
          % (name, status, time.time() - started, terminal, len(text), saw))
    if status != 200 or not terminal or not saw:
        rc = 30
raise SystemExit(rc)
PY
echo LANE443_ACCEPTANCE=PASS
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 400 -HardTimeoutSeconds 900

#requires -Version 7
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
python3 - <<'PY'
import json, time, statistics, urllib.request, yaml

cfg = yaml.safe_load(open('/opt/cliproxyapi/config.yaml'))
k = cfg['access']['api-keys'][0]
if isinstance(k, dict):
    k = k['key']

body = {
    'model': 'beam-free',
    'messages': [
        {'role': 'user', 'content': 'From 1 count to 60, output one number per line, no other text.'}
    ],
    'stream': True,
    'max_tokens': 2048,
}
req = urllib.request.Request(
    'http://127.0.0.1:8318/v1/chat/completions',
    data=json.dumps(body).encode(),
    headers={'Authorization': 'Bearer ' + k, 'Content-Type': 'application/json'},
)
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
t0 = time.monotonic()
first_byte = None
first_text = None
gaps = []
prev = None
texts = []
count = 0
finish = False
done = False
status = None
with op.open(req, timeout=90) as r:
    status = r.status
    for line in r:
        now = time.monotonic()
        if first_byte is None:
            first_byte = now - t0
        if not line.startswith(b'data:'):
            continue
        d = line[5:].strip()
        if d == b'[DONE]':
            done = True
            break
        p = json.loads(d)
        assert not p.get('error'), p.get('error')
        count += 1
        for ch in p.get('choices', []):
            v = ch.get('delta', {}).get('content', '')
            if v:
                if first_text is None:
                    first_text = now - t0
                texts.append(v)
                if prev is not None:
                    gaps.append(now - prev)
                prev = now
            finish = finish or ch.get('finish_reason') == 'stop'
total = time.monotonic() - t0
text = ''.join(texts)
assert status == 200 and finish and done, 'STREAM_CONTRACT_FAILED'
gaps_ms = sorted(g * 1000 for g in gaps) if gaps else [0]
n_chars = len(text)
out = {
    'status': status,
    'first_byte_s': round(first_byte, 3),
    'first_text_s': round(first_text, 3),
    'events': count,
    'chars': n_chars,
    'gen_span_s': round((prev - first_text), 3) if prev and first_text else 0,
    'chars_per_s': round(n_chars / max(prev - first_text, 1e-6), 1) if prev and first_text else 0,
    'gap_ms_p50': round(gaps_ms[len(gaps_ms)//2], 1),
    'gap_ms_p90': round(gaps_ms[int(len(gaps_ms)*0.9)], 1),
    'gap_ms_max': round(gaps_ms[-1], 1),
    'total_s': round(total, 3),
}
print('SSE_PACING ' + json.dumps(out, ensure_ascii=False))
PY
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 180 -HardTimeoutSeconds 400

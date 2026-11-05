#requires -Version 7
# Exactly one streamed PONG request on the deployed flash-sg lane. The client
# key is read only on bwg and is never echoed. No retry, config or auth write.
param(
  [string]$Config
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
$command = @'
set -Eeuo pipefail
ROOT=$(mktemp -d /root/wb-pong-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$ROOT"' EXIT
API_KEY=$(python3 - <<'PY'
import yaml
from pathlib import Path

data = yaml.safe_load(Path('/opt/cliproxyapi/config.yaml').read_text())
print(data['access']['api-keys'][0])
PY
)
[ -n "$API_KEY" ]
status=$(curl --silent --show-error --max-time 90 --output "$ROOT/body" --write-out '%{http_code}' \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  --data '{"model":"deepseek-v4.1-flash-sg","stream":true,"messages":[{"role":"user","content":"Return the word PONG only."}]}' \
  http://127.0.0.1:8318/v1/chat/completions)
echo "PRODUCTION_STATUS=$status"
python3 - "$ROOT/body" <<'PY'
import json
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding='utf-8', errors='replace')
parts=[]
terminal=False
events=0
for line in text.splitlines():
    if not line.startswith('data: '):
        continue
    payload=line[6:].strip()
    if payload == '[DONE]':
        terminal=True
        continue
    try: event=json.loads(payload)
    except ValueError: continue
    events += 1
    if event.get('type') in ('response.completed', 'response.failed', 'response.incomplete'):
        terminal=True
    choice=(event.get('choices') or [{}])[0]
    parts.append(str((choice.get('delta') or {}).get('content') or ''))
body=''.join(parts)
print(f'PRODUCTION_SSE_EVENTS={events} TERMINAL={str(terminal).lower()} TEXT={body.strip()!r}')
for marker in ('6004','14012','11140','upstream_error'):
    if marker in text: print(f'PRODUCTION_SIGNAL={marker}')
raise SystemExit(0 if terminal and body.strip() == 'PONG' else 30)
PY
echo PRODUCTION_PONG_ACCEPTANCE=PASS
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 180

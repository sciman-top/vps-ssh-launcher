#!/bin/bash
# Single controlled workbuddy lane probe via admission 8318 (half-open probe
# after the 6004 model-level frequency window). Key read on-box, never echoed.
set -Eeuo pipefail
KEY=$(python3 -c "import yaml; print(yaml.safe_load(open('/opt/cliproxyapi/config.yaml', encoding='utf-8'))['access']['api-keys'][0])")
BODY='{"model":"deepseek-v4.1-flash","max_tokens":32,"messages":[{"role":"user","content":"Reply with exactly: OK"}]}'
curl -sS --noproxy '*' --max-time 240 -o /tmp/.wb.json -w "HTTP %{http_code} total=%{time_total}s\n" \
  -X POST http://127.0.0.1:8318/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "$BODY"
python3 -c "
import json
doc = json.load(open('/tmp/.wb.json'))
ch = (doc.get('choices') or [{}])[0]
print('PARSE model=' + str(doc.get('model')) + ' finish=' + str(ch.get('finish_reason')) + ' err=' + str(doc.get('error'))[:200])
"
rm -f /tmp/.wb.json

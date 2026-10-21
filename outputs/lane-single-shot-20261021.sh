#!/bin/bash
# Post-canary OAuth lane loopback single-shots via admission 8318 (one per lane),
# plus a streaming TTFB baseline on the non-OAuth GLM lane. Key read on-box, never echoed.
set -Eeuo pipefail
KEY=$(python3 -c "import yaml; print(yaml.safe_load(open('/opt/cliproxyapi/config.yaml', encoding='utf-8'))['access']['api-keys'][0])")
shot() {
  MODEL="$1"
  BODY="{\"model\":\"$MODEL\",\"max_tokens\":32,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: OK\"}]}"
  curl -sS --noproxy '*' --max-time 120 -o /tmp/.lane.json -w "HTTP %{http_code} total=%{time_total}s\n" \
    -X POST http://127.0.0.1:8318/v1/chat/completions \
    -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "$BODY"
  python3 -c "
import json
doc = json.load(open('/tmp/.lane.json'))
ch = (doc.get('choices') or [{}])[0]
print('PARSE model=' + str(doc.get('model')) + ' finish=' + str(ch.get('finish_reason')) + ' err=' + str(doc.get('error'))[:160])
"
}
echo '== antigravity gemini-3.8-flash-high'
shot gemini-3.8-flash-high
sleep 3
echo '== workbuddy deepseek-v4.1-flash'
shot deepseek-v4.1-flash
sleep 3
echo '== zhipu glm-5.3-flash streaming TTFB'
BODY='{"model":"glm-5.3-flash","max_tokens":48,"stream":true,"messages":[{"role":"user","content":"Count from 1 to 20, digits separated by spaces."}]}'
curl -sS --noproxy '*' --max-time 120 -o /tmp/.lane_sse.txt -w "HTTP %{http_code} ttfb=%{time_starttransfer}s total=%{time_total}s size=%{size_download}B\n" \
  -X POST http://127.0.0.1:8318/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "$BODY"
echo "sse_events=$(grep -c '^data:' /tmp/.lane_sse.txt || true)"
rm -f /tmp/.lane.json /tmp/.lane_sse.txt

#!/bin/bash
# Bounded protocol discovery: max two candidate paths, plain curl, one shot each.
KEY=$(python3 -c "
import yaml
config = yaml.safe_load(open('/opt/cliproxyapi/config.yaml', encoding='utf-8'))
entry = next(e for e in config['api-keys']['openai-compatibility'] if str(e.get('base-url','')).rstrip('/') == 'https://opencode.ai/zen/go/v1')
print(entry['keys'][0]['api-key'])
")
BODY='{"model":"claude-haiku-5-5","max_tokens":64,"messages":[{"role":"user","content":"Reply with exactly: OK"}]}'
for PATH_CAND in "https://opencode.ai/zen/go/v1/messages" "https://opencode.ai/zen/go/anthropic/v1/messages"; do
  echo "== POST $PATH_CAND"
  curl -sS --noproxy '*' --max-time 120 -o /tmp/.zenresp.json -w "HTTP %{http_code}\n" \
    -X POST "$PATH_CAND" \
    -H "Authorization: Bearer $KEY" \
    -H "anthropic-version: 2023-06-01" \
    -H "x-opencode-session: cpa-bwg-gateway-01" \
    -H "Content-Type: application/json" \
    -d "$BODY"
  head -c 300 /tmp/.zenresp.json; echo
  CODE=$(python3 -c "
import json
try:
    doc = json.load(open('/tmp/.zenresp.json'))
except Exception:
    print('unparsed'); raise SystemExit
if doc.get('type') == 'message' and doc.get('role') == 'assistant':
    print('ANTHROPIC_MESSAGE_OK model=' + str(doc.get('model')) + ' stop=' + str(doc.get('stop_reason')))
else:
    print('other type=' + str(doc.get('type')) + ' err=' + str(doc.get('error'))[:120])
")
  echo "PARSE $CODE"
  if echo "$CODE" | grep -q ANTHROPIC_MESSAGE_OK; then rm -f /tmp/.zenresp.json; exit 0; fi
done
rm -f /tmp/.zenresp.json
exit 1

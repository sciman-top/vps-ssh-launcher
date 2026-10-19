#!/bin/bash
# One-shot: go messages path with Anthropic-native x-api-key auth header.
KEY=$(python3 -c "
import yaml
config = yaml.safe_load(open('/opt/cliproxyapi/config.yaml', encoding='utf-8'))
entry = next(e for e in config['api-keys']['openai-compatibility'] if str(e.get('base-url','')).rstrip('/') == 'https://opencode.ai/zen/go/v1')
print(entry['keys'][0]['api-key'])
")
curl -sS --noproxy '*' --max-time 120 -o /tmp/.zm2.json -w "HTTP %{http_code}\n" \
  -X POST "https://opencode.ai/zen/go/v1/messages" \
  -H "x-api-key: $KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "x-opencode-session: cpa-bwg-gateway-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-5-5","max_tokens":64,"messages":[{"role":"user","content":"Reply with exactly: OK"}]}'
python3 -c "
import json
doc = json.load(open('/tmp/.zm2.json'))
if doc.get('type') == 'message':
    text = ''.join(p.get('text','') for b in doc.get('content',[]) if isinstance(b,dict) and b.get('type')=='text' for p in [b])
    print('ANTHROPIC_MESSAGE_OK model=%s stop=%s text=%r usage=%s' % (
        doc.get('model'), doc.get('stop_reason'), text[:60], json.dumps(doc.get('usage',{}))))
else:
    print('other type=%s err=%s' % (doc.get('type'), str(doc.get('error'))[:160]))
"
rm -f /tmp/.zm2.json

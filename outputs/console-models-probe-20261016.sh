#!/bin/bash
# Free catalog read: is the slot-2 key valid on the console inference surface?
KEY=$(python3 -c "
import yaml
config = yaml.safe_load(open('/opt/cliproxyapi/config.yaml', encoding='utf-8'))
entry = next(e for e in config['api-keys']['openai-compatibility'] if str(e.get('base-url','')).rstrip('/') == 'https://opencode.ai/zen/go/v1')
print(entry['keys'][0]['api-key'])
")
for URL in "https://opencode.ai/inference/v1/models" "https://opencode.ai/zen/go/v1/models"; do
  echo "== GET $URL"
  CODE=$(curl -sS --noproxy '*' --max-time 60 -o /tmp/.cm.json -w "%{http_code}" \
    -H "Authorization: Bearer $KEY" "$URL")
  echo "HTTP $CODE"
  if [ "$CODE" = "200" ]; then
    python3 -c "
import json
doc = json.load(open('/tmp/.cm.json'))
ids = sorted(i.get('id','') for i in doc.get('data',[]) if isinstance(i,dict))
claude = [i for i in ids if 'claude' in i]
print('total=%d claude_ids=%s' % (len(ids), claude))
"
  else
    head -c 200 /tmp/.cm.json; echo
  fi
done
rm -f /tmp/.cm.json

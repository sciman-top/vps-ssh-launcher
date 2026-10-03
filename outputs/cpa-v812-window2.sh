#!/usr/bin/env bash
# Wait for the ai.input.im sol generation path to recover, restore the catalog
# view via container restart, then run the v8.0.12 canary. Bounded: 40x300s.
set -u
for i in $(seq 1 24); do
  OUT=$(python3 - <<'PY'
import json, urllib.request, urllib.error, yaml
cfg = yaml.safe_load(open('/opt/cliproxyapi/config.yaml'))
p = [x for x in cfg.get('openai-compatibility', []) if 'ai.input.im' in str(x.get('base-url', ''))][0]
k = p['api-key-entries'][0]['api-key']
b = json.dumps({'model': 'gpt-6.1-sol', 'messages': [{'role': 'user', 'content': 'Reply OK'}], 'max_tokens': 8}).encode()
rq = urllib.request.Request(p['base-url'].rstrip('/') + '/chat/completions', data=b, headers={'Authorization': 'Bearer ' + k, 'Content-Type': 'application/json'})
try:
    r = urllib.request.urlopen(rq, timeout=30)
    print('UP_OK')
except urllib.error.HTTPError as e:
    print('UP_FAIL', e.code)
except Exception as e:
    print('UP_FAIL', type(e).__name__)
PY
)
  echo "$(date -u +%FT%TZ) attempt=$i $OUT"
  case "$OUT" in
    UP_OK)
      docker restart cli-proxy-api >/dev/null
      sleep 12
      FULL=$(python3 -c "import json,urllib.request,yaml;cfg=yaml.safe_load(open('/opt/cliproxyapi/config.yaml'));k=cfg['api-keys'][0];rq=urllib.request.Request('http://127.0.0.1:8317/v1/models',headers={'Authorization':'Bearer '+k});d=json.load(urllib.request.urlopen(rq));ids={m['id'] for m in d['data']};print('FULL' if 'gpt-6.1-sol-input' in ids else 'INCOMPLETE')")
      echo "$(date -u +%FT%TZ) catalog=$FULL"
      if [ "$FULL" = "FULL" ]; then
        bash /root/cpa-canary-v812.sh
        exit $?
      fi
      ;;
  esac
  sleep 300
done
echo 'WINDOW_TIMEOUT after 24 attempts'
exit 42

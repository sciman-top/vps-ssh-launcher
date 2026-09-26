DIR=/opt/cliproxyapi
python3 "$DIR/cpa-health.py" generation
echo "NON_OAUTH_GENERATION_RC=$?"
KEY=$(python3 -c "import yaml;print(yaml.safe_load(open('$DIR/config.yaml'))['api-keys'][0])")
if [ -z "$KEY" ]; then echo "LUNA_PROBE=skipped_missing_key"; exit 0; fi
CODE=$(curl --noproxy '*' -sS --max-time 45 -o /tmp/luna-probe.json -w '%{http_code}' -X POST -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{"model":"gpt-6-luna","messages":[{"role":"user","content":"ping"}],"max_tokens":1}' http://127.0.0.1:8317/v1/chat/completions) || CODE=curl_failed
echo "LUNA_HTTP_STATUS=$CODE"
python3 - <<'PYEOF'
import json
try:
    data = json.load(open('/tmp/luna-probe.json', encoding='utf-8'))
except Exception:
    print('LUNA_ERROR=unreadable')
    raise SystemExit(0)
err = data.get('error') if isinstance(data, dict) else None
if isinstance(err, dict):
    print('LUNA_ERROR_CODE=%s' % err.get('code'))
    print('LUNA_ERROR_TYPE=%s' % err.get('type'))
else:
    print('LUNA_ERROR=none')
PYEOF
rm -f /tmp/luna-probe.json

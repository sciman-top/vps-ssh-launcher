#!/usr/bin/env bash
# Zhipu developer-role override acceptance (2026-11-03).
# Desktop-shaped single-shot: /v1/responses wire carrying a developer item,
# the exact ingredient zhipu rejected with 1214 before the payload rule.
# Key stays server-side; output masked.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
KEY=$(python3 -c "import yaml;c=yaml.safe_load(open('$DIR/config.yaml'));print(c['access']['api-keys'][0])")

echo "=== live payload block (masked) ==="
python3 - <<'PY'
import json, yaml
c = yaml.safe_load(open('/opt/cliproxyapi/config.yaml'))
block = (c.get('requests') or {}).get('payload') or {}
print(json.dumps(block, ensure_ascii=False)[:500])
PY

echo "=== desktop-shape single-shot (responses wire, developer item) ==="
printf '{"model":"glm-5.3-flash","stream":true,"instructions":"You are a helpful assistant.","input":[{"role":"developer","content":"mode: acceptance-probe"},{"role":"user","content":"Reply with exactly: PONG"}]}' > /tmp/probe-body.json
resp=$(curl -s -N -m 90 -o /tmp/probe-out.txt -w "%{http_code} %{time_total}" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  --data @/tmp/probe-body.json http://127.0.0.1:8318/v1/responses)
echo "PROBE_HTTP_TIME=$resp"
echo "PROBE_EVENTS=$(grep -c '^data:' /tmp/probe-out.txt || true)"
grep -q 'response.completed\|chat.completion' /tmp/probe-out.txt && echo PROBE_COMPLETED_OK || echo PROBE_COMPLETED_CHECK
grep -o 'PONG' /tmp/probe-out.txt | head -1
rm -f /tmp/probe-body.json /tmp/probe-out.txt

echo "=== journal window since probe ==="
SINCE=$(date -u -d '-3 minutes' '+%Y-%m-%d %H:%M:%S')
journalctl --no-pager -S "$SINCE" 2>/dev/null | grep 'upstream_result' | grep -i 'glm' | tail -3 | sed -E 's/[A-Za-z0-9_-]{25,}/<redacted>/g' | cut -c1-200

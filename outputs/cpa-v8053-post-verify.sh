#!/usr/bin/env bash
# v8.0.53 window post-verify (2026-11-03): single-shot SSE probes via
# admission 8318 (antigravity chat shape + zhipu responses shape carrying a
# developer item, proving the payload override chain end-to-end on the new
# host), journal window since the swap, and the AuthGrantFingerprint
# re-baseline watch (round-15 delta: api_key/session_token fallback).
# Keys stay server-side; output is masked.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
SINCE="${SWAP_SINCE:-2026-11-03 15:10:51}"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

echo "=== SSE single-shot gemini-3.8-flash-high via admission 8318 ==="
KEY=$(python3 -c "import yaml;c=yaml.safe_load(open('$DIR/config.yaml'));print(c['access']['api-keys'][0])")
printf '{"model":"gemini-3.8-flash-high","stream":true,"messages":[{"role":"user","content":"Reply with exactly: PONG"}]}' > /tmp/probe-body.json
resp=$(curl -s -N -m 90 -o /tmp/probe-out.txt -w "%{http_code} %{time_total}" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  --data @/tmp/probe-body.json http://127.0.0.1:8318/v1/chat/completions)
echo "PROBE_HTTP_TIME=$resp"
echo "PROBE_EVENTS=$(grep -c '^data:' /tmp/probe-out.txt || true)"
grep -q 'response.completed\|chat.completion' /tmp/probe-out.txt && echo PROBE_COMPLETED_OK || echo PROBE_COMPLETED_CHECK
grep -o 'PONG' /tmp/probe-out.txt | head -1
rm -f /tmp/probe-body.json /tmp/probe-out.txt

echo "=== responses-wire developer-item single-shot glm-5.3-flash (desktop shape) ==="
printf '{"model":"glm-5.3-flash","stream":true,"instructions":"You are a helpful assistant.","input":[{"role":"developer","content":"mode: post-swap-probe"},{"role":"user","content":"Reply with exactly: PONG"}]}' > /tmp/probe-body2.json
resp2=$(curl -s -N -m 90 -o /tmp/probe-out2.txt -w "%{http_code} %{time_total}" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  --data @/tmp/probe-body2.json http://127.0.0.1:8318/v1/responses)
echo "PROBE_HTTP_TIME=$resp2"
grep -q 'response.completed\|chat.completion' /tmp/probe-out2.txt && echo PROBE_COMPLETED_OK || echo PROBE_COMPLETED_CHECK
grep -o 'PONG' /tmp/probe-out2.txt | head -1
rm -f /tmp/probe-body2.json /tmp/probe-out2.txt

echo "=== grant re-baseline watch (round-15 delta) ==="
docker logs --since 10m cli-proxy-api 2>&1 | grep -ciE 'duplicate|already (registered|adopted)|grant.*conflict' || true
docker logs --since 10m cli-proxy-api 2>&1 | grep -iE 'grant' | head -3 | cut -c1-160 || true

echo "=== journal window since swap ($SINCE) ==="
lane_rejects=$(journalctl --no-pager -S "$SINCE" 2>/dev/null | grep 'upstream_result' | grep -c 'lane_reject=true' || true)
echo "LANE_REJECTS_SINCE_SWAP=$lane_rejects"
journalctl --no-pager -S "$SINCE" 2>/dev/null | grep 'upstream_result' | grep -oE 'sse_last_event=[a-z_.]+' | sort | uniq -c | head -5
journalctl --no-pager -S "$SINCE" 2>/dev/null | grep 'upstream_result' | grep -oE 'status=[0-9]+' | sort | uniq -c | head -8
journalctl --no-pager -S "$SINCE" 2>/dev/null | grep -E '1214|角色信息' | grep -c 'upstream_result' || true

#!/usr/bin/env bash
# v8.0.52 window post-verify: tokeninfo both antigravity credentials (aicode),
# single-shot SSE probe on gemini-3.8-flash-high via admission 8318, journal
# window since the swap. Keys stay server-side; output is masked.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

echo "=== tokeninfo x2 (both antigravity credentials) ==="
cd "$DIR/auth"
python3 - <<'PY'
import json, os, urllib.request, urllib.parse
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
n = 0
for f in sorted(os.listdir('.')):
    if not f.endswith('.json'):
        continue
    with open(f) as fh:
        d = json.load(fh)
    if d.get('type') != 'antigravity':
        continue
    n += 1
    tok = d.get('access_token', '')
    req = urllib.request.Request('https://oauth2.googleapis.com/tokeninfo?' +
                                 urllib.parse.urlencode({'access_token': tok}))
    with opener.open(req, timeout=10) as r:
        info = json.load(r)
    scopes = info.get('scope', '').split(' ')
    email = info.get('email', '?')
    masked = email[:2] + '***' + email.split('@')[-1]
    print('cred=%d AICODE_PRESENT=%s account=%s exp=%s' %
          (n, 'https://www.googleapis.com/auth/aicode' in scopes, masked, info.get('expires_in')))
print('antigravity_credentials_checked=%d' % n)
PY

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

echo "=== journal window since swap ==="
SINCE="2026-11-03 12:14:37"
lane_rejects=$(journalctl --no-pager -S "$SINCE" 2>/dev/null | grep 'upstream_result' | grep -c 'lane_reject=true' || true)
echo "LANE_REJECTS_SINCE_SWAP=$lane_rejects"
antigravity_lines=$(journalctl --no-pager -S "$SINCE" 2>/dev/null | grep -c 'lane=antigravity' || true)
echo "ANTIGRAVITY_LINES_SINCE_SWAP=$antigravity_lines"
journalctl --no-pager -S "$SINCE" 2>/dev/null | grep 'upstream_result' | grep -oE 'sse_last_event=[a-z_.]+' | sort | uniq -c | head -5
journalctl --no-pager -S "$SINCE" 2>/dev/null | grep 'upstream_result' | grep -oE 'status=[0-9]+' | sort | uniq -c | head -8

#!/usr/bin/env bash
# v8.0.52 window: inject antigravity callback #1, verify written credential +
# aicode scope via tokeninfo, then start login container #2.
# Callback URL arrives via CB_URL env (never written to this file).
set -Eeuo pipefail
umask 077
echo "---inject---"
http=$(curl -s -o /tmp/cb-body.txt -w "%{http_code}" "$CB_URL")
echo "http=$http"
head -c 200 /tmp/cb-body.txt; echo
rm -f /tmp/cb-body.txt
sleep 4
echo "---container state---"
docker ps -a --format "{{.Names}} {{.Status}}" | grep cpa-antigravity-login || echo "CONTAINER_GONE_CLEAN"
echo "---fresh antigravity credential---"
cd /opt/cliproxyapi/auth
python3 - <<'PY'
import json, os, time
cands = []
for f in os.listdir('.'):
    if not f.endswith('.json'):
        continue
    try:
        with open(f) as fh:
            d = json.load(fh)
    except Exception:
        continue
    if d.get('type') == 'antigravity':
        cands.append((os.path.getmtime(f), f))
cands.sort()
mt, newest = cands[-1]
print('newest_age_sec=%d files_total=%d' % (int(time.time() - mt), len(cands)))
with open(newest) as fh:
    d = json.load(fh)
tok = d.get('access_token', '')
print('has_access_token=%s' % bool(tok))
with open('/tmp/tok.txt', 'w') as out:
    out.write(tok)
PY
chmod 600 /tmp/tok.txt
echo "---tokeninfo scope check---"
curl -sG https://oauth2.googleapis.com/tokeninfo --data-urlencode "access_token@/tmp/tok.txt" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
scopes = d.get('scope', '').split(' ')
print('AICODE_PRESENT=%s' % ('https://www.googleapis.com/auth/aicode' in scopes))
email = d.get('email', '?')
print('account=%s***%s' % (email[:2], email.split('@')[-1] if '@' in email else ''))
print('expires_in=%s' % d.get('expires_in'))
" || echo TOKENINFO_FAIL
rm -f /tmp/tok.txt
echo "---start login container #2---"
IMG='eceasy/cli-proxy-api:v8.0.52@sha256:5d5bab318ac048c0f88570c6ffb852ef3e42e9acc9bfcf346c524de7a2468131'
docker rm -f cpa-antigravity-login >/dev/null 2>&1 || true
sleep 1
docker run --rm -d --name cpa-antigravity-login \
  --entrypoint /bin/sh -w /CLIProxyAPI \
  -p 127.0.0.1:51121:51121 \
  -v /opt/cliproxyapi/auth:/root/.cli-proxy-api \
  -v /opt/cliproxyapi/config.yaml:/CLIProxyAPI/config.yaml:ro \
  "$IMG" \
  -c "umask 077 && exec ./CLIProxyAPI -antigravity-login -no-browser -config /CLIProxyAPI/config.yaml" >/dev/null
sleep 6
echo "---AUTH_URL_2_BEGIN---"
docker logs cpa-antigravity-login 2>&1 | grep -oE 'https://accounts\.google\.com[^ ]*' | head -1
echo "---AUTH_URL_2_END---"

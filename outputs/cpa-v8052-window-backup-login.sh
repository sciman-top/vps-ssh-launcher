#!/usr/bin/env bash
# v8.0.52 upgrade window: backup + auth baseline + start antigravity re-login #1.
# One-shot transaction script (2026-11-03). Run on bwg via base64 transport.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
IMG='eceasy/cli-proxy-api:v8.0.52@sha256:5d5bab318ac048c0f88570c6ffb852ef3e42e9acc9bfcf346c524de7a2468131'
BK="$DIR/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-from-v8.0.51-to-v8.0.52-plugin-0.9.84-unchanged"
mkdir -m 700 "$BK"
cp -a "$DIR/compose.yml" "$DIR/config.yaml" "$DIR/plugins/workbuddy.so" "$BK/"
cd "$DIR/auth"
BK="$BK" python3 - <<'PY'
import json, shutil, os
bk = os.environ['BK']
n = 0
for f in sorted(os.listdir('.')):
    if not f.endswith('.json'):
        continue
    with open(f) as fh:
        d = json.load(fh)
    if d.get('type') == 'antigravity':
        shutil.copy2(f, os.path.join(bk, f))
        n += 1
print('antigravity_files_backed_up=%d' % n)
PY
sha256sum -- ./*.json > "$BK/credentials.sha256"
sha256sum -- ./*.json > /tmp/auth-before.sha256
chmod 600 /tmp/auth-before.sha256
echo "BACKUP_OK dir=$BK files=$(wc -l < "$BK/credentials.sha256")"
docker rm -f cpa-antigravity-login >/dev/null 2>&1 || true
docker run --rm -d --name cpa-antigravity-login \
  --entrypoint /bin/sh -w /CLIProxyAPI \
  -p 127.0.0.1:51121:51121 \
  -v "$DIR/auth:/root/.cli-proxy-api" \
  -v "$DIR/config.yaml:/CLIProxyAPI/config.yaml:ro" \
  "$IMG" \
  -c "umask 077 && exec ./CLIProxyAPI -antigravity-login -no-browser -config /CLIProxyAPI/config.yaml" >/dev/null
sleep 6
echo "---AUTH_URL_BEGIN---"
docker logs cpa-antigravity-login 2>&1 | grep -oE 'https://accounts\.google\.com[^ ]*' | head -1
echo "---AUTH_URL_END---"

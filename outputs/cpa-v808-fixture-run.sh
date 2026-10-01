#!/usr/bin/env bash
# Fixture acceptance of the v8.0.7 binary in a disposable mount+net namespace.
# Production processes, mounts and network are untouched; fixture binds a
# mktemp dir over /opt/cliproxyapi inside the namespace only.
set -Eeuo pipefail
FIX=$(mktemp -d /tmp/cpa-accept-v808.XXXXXX)
echo "FIXTURE_ROOT=$FIX"
cid=$(docker create 'eceasy/cli-proxy-api:v8.0.8@sha256:7d7203c06b4f5fd844adbc8cc8be1965e92f7e292ef169146cb9321812587a65')
docker cp "$cid":/CLIProxyAPI/CLIProxyAPI "$FIX/CLIProxyAPI"
docker rm "$cid" >/dev/null
chmod 700 "$FIX/CLIProxyAPI"
echo "== fixture binary sha (prefix)"
sha256sum "$FIX/CLIProxyAPI" | cut -c1-16
D=/opt/cliproxyapi
cp "$D"/cpa-health.py "$D"/cpa_policy.py "$D"/cpa_provider_routes.json "$D"/auto-update.sh "$FIX/"
mv /tmp/cpa-acceptance-v808.py "$FIX/cpa-acceptance.py"
mv /tmp/cpa-update-acceptance-v808.py "$FIX/cpa-update-acceptance.py"
chmod 700 "$FIX"/*
touch "$FIX/FIXTURE_ONLY"
set +e
unshare --mount --net --fork bash -c "mount --bind '$FIX' /opt/cliproxyapi && ip link set lo up && exec python3 /opt/cliproxyapi/cpa-acceptance.py"
rc=$?
set -e
echo "ACCEPTANCE_EXIT=$rc"
echo "== leftover fixture processes (absolute /opt/cliproxyapi path = leak)"
pgrep -af 'CLIProxyAPI' || true
if pgrep -f '/opt/cliproxyapi/CLIProxyAPI' >/dev/null; then
  echo "CLEANUP_DEFERRED fixture process still alive root=$FIX"
else
  rm -rf "$FIX"
  echo "CLEANUP_OK root=$FIX"
fi

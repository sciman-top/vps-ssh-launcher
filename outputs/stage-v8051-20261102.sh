set -Eeuo pipefail
umask 077
IMAGE=eceasy/cli-proxy-api:v8.0.51@sha256:15d13889e1a39a688be306dfbf2f6cf9003e99cd913b5070fa463f8ab2aa0031
V=/root/cpa-v8051-plugin-0984-20261102
test ! -e "$V"
mkdir -m 700 "$V"
timeout 600 docker pull "$IMAGE" >"$V/pull.log" 2>&1
CID=$(docker create --network none "$IMAGE")
docker cp "$CID:/CLIProxyAPI/CLIProxyAPI" "$V/CLIProxyAPI"
docker rm "$CID" >/dev/null
cp /opt/cliproxyapi/cpa-health.py /opt/cliproxyapi/auto-update.sh /opt/cliproxyapi/cpa_provider_routes.json "$V/"
mkdir "$V/plugins" "$V/tmp" "$V/run" "$V/state"
cp /root/cpa-workbuddy-0984-20261102-build/plugins/workbuddy/workbuddy.so "$V/plugins/workbuddy.so"
touch "$V/FIXTURE_ONLY"
# The plugin is unchanged in this transaction: the staged artifact must be
# byte-identical to the live plugin (0.9.84, bbf57c0a...).
LIVE_SO=$(sha256sum /opt/cliproxyapi/plugins/workbuddy.so | cut -d ' ' -f 1)
STAGED_SO=$(sha256sum "$V/plugins/workbuddy.so" | cut -d ' ' -f 1)
test "$LIVE_SO" = "$STAGED_SO"
grep -qF bbf57c0ad33a6de33507de9c20fedc505edfa93fecbee05507834255f1e53368 <<<"$STAGED_SO"
sha256sum "$V/CLIProxyAPI" "$V/plugins/workbuddy.so"
ldd "$V/CLIProxyAPI" | head -10
echo STAGE_OK

set -Eeuo pipefail
umask 077
IMAGE=eceasy/cli-proxy-api:v8.0.50@sha256:21f823945675a80b219bddc632a3716079b5a6fcecf32c840cad29dba5754e84
V=/root/cpa-v8050-plugin-0984-20261102
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
sha256sum "$V/CLIProxyAPI" "$V/plugins/workbuddy.so"
ldd "$V/CLIProxyAPI" | head -10

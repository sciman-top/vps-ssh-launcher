set -Eeuo pipefail
umask 077
IMAGE=eceasy/cli-proxy-api:v8.0.49@sha256:20925aef8323b3f2539605aa048bb59ff17cc9761cba311e2eb1e1723b1ff42e
V=/root/cpa-v8049-plugin-0982-20261101
test ! -e "$V"
mkdir -m 700 "$V"
timeout 600 docker pull "$IMAGE" >"$V/pull.log" 2>&1
CID=$(docker create --network none "$IMAGE")
docker cp "$CID:/CLIProxyAPI/CLIProxyAPI" "$V/CLIProxyAPI"
docker rm "$CID" >/dev/null
cp /opt/cliproxyapi/cpa-health.py /opt/cliproxyapi/auto-update.sh /opt/cliproxyapi/cpa_provider_routes.json "$V/"
mkdir "$V/plugins" "$V/tmp" "$V/run" "$V/state"
cp /root/cpa-workbuddy-0982-20261031/plugins/workbuddy/workbuddy.so "$V/plugins/"
touch "$V/FIXTURE_ONLY"
sha256sum "$V/CLIProxyAPI" "$V/plugins/workbuddy.so"
ldd "$V/CLIProxyAPI" | head -10

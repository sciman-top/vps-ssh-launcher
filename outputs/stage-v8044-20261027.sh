set -Eeuo pipefail
umask 077
IMAGE=eceasy/cli-proxy-api:v8.0.44@sha256:be71a92720e8ce9aa51fc24a51bf100af29f59cbd6b5f0d0bbfa4c7ca90e83a2
V=/root/cpa-v8044-isolated-20261027
test ! -e "$V"
mkdir -m 700 "$V"
timeout 600 docker pull "$IMAGE" >"$V/pull.log" 2>&1
CID=$(docker create --network none "$IMAGE")
docker cp "$CID:/CLIProxyAPI/CLIProxyAPI" "$V/CLIProxyAPI"
docker rm "$CID" >/dev/null
cp /opt/cliproxyapi/cpa-health.py /opt/cliproxyapi/auto-update.sh /opt/cliproxyapi/cpa_provider_routes.json "$V/"
mkdir "$V/plugins" "$V/tmp" "$V/run" "$V/state"
cp /opt/cliproxyapi/plugins/workbuddy.so "$V/plugins/"
touch "$V/FIXTURE_ONLY"
sha256sum "$V/CLIProxyAPI" "$V/plugins/workbuddy.so"
ldd "$V/CLIProxyAPI" | head -10

set -Eeuo pipefail
umask 077
IMAGE=eceasy/cli-proxy-api:v8.0.48@sha256:c198886a86b57f49cb2508a68b6170e23b2d509c648934af5d5dd827cf6faa16
V=/root/cpa-v8048-plugin-0981-20261030
test ! -e "$V"
mkdir -m 700 "$V"
timeout 600 docker pull "$IMAGE" >"$V/pull.log" 2>&1
CID=$(docker create --network none "$IMAGE")
docker cp "$CID:/CLIProxyAPI/CLIProxyAPI" "$V/CLIProxyAPI"
docker rm "$CID" >/dev/null
cp /opt/cliproxyapi/cpa-health.py /opt/cliproxyapi/auto-update.sh /opt/cliproxyapi/cpa_provider_routes.json "$V/"
mkdir "$V/plugins" "$V/tmp" "$V/run" "$V/state"
cp /root/cpa-workbuddy-0981-20261028/plugins/workbuddy/workbuddy.so "$V/plugins/"
touch "$V/FIXTURE_ONLY"
sha256sum "$V/CLIProxyAPI" "$V/plugins/workbuddy.so"
ldd "$V/CLIProxyAPI" | head -10

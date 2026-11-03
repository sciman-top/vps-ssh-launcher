#!/usr/bin/env bash
# v8.0.53 window: isolated plugin load proof (2026-11-03).
# Replicates plugin-load-v8051-20261102.sh discipline: fixture tree with no
# credentials, binary extracted from the staged v8.0.53 image (exact bytes
# that will run), unshare mount+net isolation, assert workbuddy 0.9.84
# registers with pins and zero panic/fatal.
set -Eeuo pipefail
umask 077
V=/root/cpa-v8053-plugin-0984-20261103
rm -rf "$V"
mkdir -p "$V/tmp" "$V/run" "$V/state" "$V/auth" "$V/plugins"
touch "$V/FIXTURE_ONLY"
cid=$(docker create eceasy/cli-proxy-api:v8.0.53)
docker cp "$cid:/CLIProxyAPI/CLIProxyAPI" "$V/CLIProxyAPI"
docker rm "$cid" >/dev/null
chmod +x "$V/CLIProxyAPI"
cp -a /opt/cliproxyapi/plugins/workbuddy.so "$V/plugins/workbuddy.so"
cat >"$V/config.yaml" <<'YAML'
host: "0.0.0.0"
port: 8317
auth-dir: "auth"
plugins:
  enabled: true
  dir: "plugins"
  configs:
    workbuddy:
      enabled: true
      scheduler_mode: credits
      account_max_concurrent: 1
      identity_switch_on_429: false
      checkin_auto: false
      tasks_auto: false
      lifecycle_auto: false
      token_keepalive: false
      models_cn: "deepseek-v4.1-flash"
      models_intl: "deepseek-v4.1-flash"
YAML
chmod 600 "$V/config.yaml"
echo "---binary provenance---"
sha256sum "$V/CLIProxyAPI"
ldd "$V/CLIProxyAPI" 2>&1 | head -5
unshare --mount --net bash -c '
set -Eeuo pipefail
mount --make-rprivate /
V=/root/cpa-v8053-plugin-0984-20261103
mount --bind "$V" /opt/cliproxyapi
mount --bind "$V/tmp" /tmp
mount --bind "$V/run" /run
mount --bind "$V/state" /var/lib/vps-ssh-launcher
ip link set lo up
test -z "$(ip route show default)"
cd /opt/cliproxyapi
./CLIProxyAPI -config config.yaml >plugin-load.log 2>&1 &
pid=$!
trap "kill $pid 2>/dev/null || true; wait $pid 2>/dev/null || true" EXIT
sleep 4
kill -0 "$pid"
grep "CLIProxyAPI Version: v8.0.53" plugin-load.log
grep "plugin registered plugin_id=workbuddy .*version=0.9.84" plugin-load.log
grep "account_max_concurrent=1 applied" plugin-load.log
! grep -Ei "panic|fatal|failed to load" plugin-load.log
echo ISOLATED_PLUGIN_LOAD=PASS
'

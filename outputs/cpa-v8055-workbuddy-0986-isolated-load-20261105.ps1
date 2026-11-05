#requires -Version 7
# Load the official WorkBuddy 0.9.86 artifact with the production CPA v8.0.55
# binary inside a private, credential-free mount/network namespace.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
IMAGE='eceasy/cli-proxy-api:v8.0.55@sha256:afe1135e8d8752867b73d717c1ca8ed27c44bfe8c6e1a2bebf228dba98889eac'
PLUGIN=/root/cpa-multi-plugins-0986-20261105/src/plugins/workbuddy/workbuddy.so
PLUGIN_SHA=04ed2703e32e3826257fbd0b78973b9583ad6f816b0b893bd555d5825df8d51d
V=$(mktemp -d /root/cpa-v8055-workbuddy-0986-XXXXXXXX)
cleanup() {
  case "$V" in /root/cpa-v8055-workbuddy-0986-*) rm -rf --one-file-system -- "$V" ;; esac
}
trap cleanup EXIT
[ -s "$PLUGIN" ]
[ "$(sha256sum "$PLUGIN" | cut -d ' ' -f 1)" = "$PLUGIN_SHA" ] || {
  echo REFUSE_PLUGIN_SHA
  exit 20
}
docker image inspect "$IMAGE" >/dev/null
mkdir -p "$V/tmp" "$V/run" "$V/state" "$V/auth" "$V/plugins"
touch "$V/FIXTURE_ONLY"
cid=$(docker create "$IMAGE")
docker cp "$cid:/CLIProxyAPI/CLIProxyAPI" "$V/CLIProxyAPI"
docker rm "$cid" >/dev/null
chmod +x "$V/CLIProxyAPI"
cp -a "$PLUGIN" "$V/plugins/workbuddy.so"
cat >"$V/config.yaml" <<'YAML'
server:
  host: "0.0.0.0"
  port: 8317
oauth:
  auth-dir: "auth"
plugins:
  enabled: true
  dir: "plugins"
  configs:
    workbuddy:
      enabled: true
      scheduler_mode: credits
      scheduler_priority_reclaim: true
      account_max_concurrent: 1
      identity_switch_on_429: false
      checkin_auto: false
      tasks_auto: false
      lifecycle_auto: false
      token_keepalive: false
      models_cn: "deepseek-v4.1-flash"
      models_intl: "deepseek-v4.1-flash,deepseek-v4.1-flash-sg,kimi-k2.8-preview"
YAML
chmod 600 "$V/config.yaml"
sha256sum "$V/CLIProxyAPI" "$V/plugins/workbuddy.so"
ldd "$V/CLIProxyAPI" 2>&1 | head -5
fixture_root=$V
export fixture_root
unshare --mount --net /bin/bash -s <<'INNER'
set -Eeuo pipefail
mount --make-rprivate /
mount --bind "$fixture_root" /opt/cliproxyapi
mount --bind "$fixture_root/tmp" /tmp
mount --bind "$fixture_root/run" /run
mount --bind "$fixture_root/state" /var/lib/vps-ssh-launcher
mount -t tmpfs -o size=64m,mode=0700 none /root
ip link set lo up
test -z "$(ip route show default)"
cd /opt/cliproxyapi
./CLIProxyAPI -config config.yaml >plugin-load.log 2>&1 &
pid=$!
trap "kill $pid 2>/dev/null || true; wait $pid 2>/dev/null || true" EXIT
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS http://127.0.0.1:8317/healthz >/dev/null 2>&1 && break
  sleep 1
done
grep 'CLIProxyAPI Version: v8.0.55' /opt/cliproxyapi/plugin-load.log
grep 'plugin registered plugin_id=workbuddy .*version=0.9.86' /opt/cliproxyapi/plugin-load.log
grep 'scheduler_priority_reclaim=true applied' /opt/cliproxyapi/plugin-load.log
grep 'account_max_concurrent=1 applied' /opt/cliproxyapi/plugin-load.log
! grep -Ei 'panic|fatal|failed to load' plugin-load.log
echo ISOLATED_WORKBUDDY_0986_LOAD=PASS
INNER
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 240

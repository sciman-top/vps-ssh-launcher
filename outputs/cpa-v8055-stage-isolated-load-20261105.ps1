#requires -Version 7
# Stage CPA v8.0.55 on bwg and prove the live WorkBuddy plugin (intl-shared
# 0.9.85 build) still loads against the new host in a no-credential,
# no-default-network namespace. No production file or container is changed.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
TAG='eceasy/cli-proxy-api:v8.0.55'
LIVE_PLUGIN=/opt/cliproxyapi/plugins/workbuddy.so
LIVE_PLUGIN_SHA=264722fa43b9da592fa3138ba29eeef35e7299ea54ff59beae41263c06e92f53
V=$(mktemp -d /root/cpa-v8055-load-XXXXXXXX)
trap 'echo "FIXTURE_DIR=$V"' EXIT
docker pull "$TAG"
digest=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$TAG" | grep -o 'sha256:[0-9a-f]*' | head -1)
[ -n "$digest" ] || { echo DIGEST_READBACK_FAILED; exit 20; }
IMAGE_REF="$TAG@$digest"
echo "STAGE_DIGEST=$digest"
docker image inspect "$IMAGE_REF" >/dev/null || { echo DIGEST_REF_INVALID; exit 20; }
[ -s "$LIVE_PLUGIN" ]
[ "$(sha256sum "$LIVE_PLUGIN" | cut -d' ' -f1)" = "$LIVE_PLUGIN_SHA" ] || { echo LIVE_PLUGIN_SHA_MISMATCH; exit 20; }
mkdir -p "$V/tmp" "$V/run" "$V/state" "$V/auth" "$V/plugins"
touch "$V/FIXTURE_ONLY"
cid=$(docker create "$IMAGE_REF")
docker cp "$cid:/CLIProxyAPI/CLIProxyAPI" "$V/CLIProxyAPI"
docker rm "$cid" >/dev/null
chmod +x "$V/CLIProxyAPI"
cp -a "$LIVE_PLUGIN" "$V/plugins/workbuddy.so"
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
      scheduler_priority_reclaim: true
      account_max_concurrent: 1
      identity_switch_on_429: false
      checkin_auto: false
      tasks_auto: false
      lifecycle_auto: false
      token_keepalive: false
      models_cn: "deepseek-v4.1-flash"
      models_intl: "deepseek-v4.1-flash,deepseek-v4.1-flash-sg"
YAML
chmod 600 "$V/config.yaml"
echo '---binary provenance---'
sha256sum "$V/CLIProxyAPI"
ldd "$V/CLIProxyAPI" 2>&1 | head -5
unshare --mount --net bash -c '
set -Eeuo pipefail
mount --make-rprivate /
V='"$V"'
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
sleep 6
kill -0 "$pid"
grep "CLIProxyAPI Version: v8.0.55" plugin-load.log
grep "plugin registered plugin_id=workbuddy .*version=0.9.85" plugin-load.log
grep "scheduler_priority_reclaim=true applied" plugin-load.log
grep "account_max_concurrent=1 applied" plugin-load.log
! grep -Ei "panic|fatal|failed to load" plugin-load.log
echo ISOLATED_PLUGIN_LOAD=PASS
'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

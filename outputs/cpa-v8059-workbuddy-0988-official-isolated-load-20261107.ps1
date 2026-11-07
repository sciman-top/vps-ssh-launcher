#requires -Version 7
# Prove the official cpa-multi-plugins v0.8.96 WorkBuddy .so (0.9.88,
# sha 6b2b2284) loads against the v8.0.59 host in a no-credential,
# no-default-network namespace before the production adoption swap. The
# staged artifact was already verified against the release zip SHA; no
# production file or container is changed.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
IMAGE='eceasy/cli-proxy-api:v8.0.59@sha256:40dda818164c1012154e18999d35d9cf14d936bd74eabdd62e22284e8898d50d'
STAGED=/root/cpa-multi-plugins-0988-official-20261107/workbuddy.so
STAGED_SHA=6b2b2284ddd35a4461f39130fe95772c86744b59f36406a96d509c7dec8b9135
V=$(mktemp -d /root/cpa-v8059-0988official-load-XXXXXXXX)
trap 'echo "FIXTURE_DIR=$V"' EXIT
[ -s "$STAGED" ]
[ "$(sha256sum "$STAGED" | cut -d' ' -f1)" = "$STAGED_SHA" ] || { echo STAGED_SHA_MISMATCH; exit 20; }
mkdir -p "$V/tmp" "$V/run" "$V/state" "$V/auth" "$V/plugins"
touch "$V/FIXTURE_ONLY"
cid=$(docker create "$IMAGE")
docker cp "$cid:/CLIProxyAPI/CLIProxyAPI" "$V/CLIProxyAPI"
docker rm "$cid" >/dev/null
chmod +x "$V/CLIProxyAPI"
cp -a "$STAGED" "$V/plugins/workbuddy.so"
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
      models_cn: "deepseek-v4.1-flash,hy4free"
      models_intl: "deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free"
YAML
chmod 600 "$V/config.yaml"
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
grep "CLIProxyAPI Version: v8.0.59" plugin-load.log
grep "plugin registered plugin_id=workbuddy .*version=0.9.88" plugin-load.log
grep "scheduler_priority_reclaim=true applied" plugin-load.log
grep "account_max_concurrent=1 applied" plugin-load.log
! grep -Ei "panic|fatal|failed to load" plugin-load.log
echo ISOLATED_OFFICIAL_0988_LOAD=PASS
'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

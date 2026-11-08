#requires -Version 7
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
NEW=/root/cpa-v8061-gemini-raw-build/CLIProxyAPI-v8061-gemini-raw
NEW_SHA=31f2f9d96ac8240356bb75984114d04d67de7d9f2c65a9aa061b207c724f0be5
V=$(mktemp -d /root/cpa-v8061-forkload-XXXXXXXX)
trap 'echo "FIXTURE_DIR=$V"' EXIT
[ "$(sha256sum "$NEW" | cut -d' ' -f1)" = "$NEW_SHA" ] || { echo NEW_SHA_MISMATCH; exit 20; }
mkdir -p "$V/tmp" "$V/run" "$V/state" "$V/auth" "$V/plugins"
touch "$V/FIXTURE_ONLY"
cp -a "$NEW" "$V/CLIProxyAPI"
chmod 755 "$V/CLIProxyAPI"
cp -a /opt/cliproxyapi/config.yaml "$V/config.yaml"
chmod 600 "$V/config.yaml"
cp -a /root/cpa-multi-plugins-v0.8.97-staging/workbuddy.so "$V/plugins/workbuddy.so"
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
./CLIProxyAPI -config config.yaml >forkload.log 2>&1 &
pid=$!
sleep 12
kill -0 "$pid"
grep -m1 "CLIProxyAPI Version: v8.0.61" forkload.log
grep -m1 "plugin registered plugin_id=workbuddy .*version=0.9.89" forkload.log
grep -Ec "scheduler_priority_reclaim=true applied|account_max_concurrent=1 applied" forkload.log
! grep -Ei "panic|fatal|failed to load" forkload.log
kill "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
echo ISOLATED_V8061_FORK_LOAD=PASS
'
echo "--- patch marker in binary"
strings -a "$NEW" | grep -c 'response.reasoning_text.delta' || true
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 180 -HardTimeoutSeconds 600

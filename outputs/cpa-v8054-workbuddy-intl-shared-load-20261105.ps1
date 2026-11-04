#requires -Version 7
# Load the reviewed WorkBuddy 0.9.85 INTL shared-window artifact with the
# production CPA v8.0.54 binary in a no-credential mount/network namespace.
# No production file or container is changed by this script.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
IMAGE_REF='eceasy/cli-proxy-api:v8.0.54@sha256:66e51ccba4786099f85f40439ba17f975dc5e9468def96ba914d3da39041d03a'
PLUGIN=/root/cpa-workbuddy-0985-intl-shared-20261105-v2/src/plugins/workbuddy/workbuddy.so
PLUGIN_SHA=264722fa43b9da592fa3138ba29eeef35e7299ea54ff59beae41263c06e92f53
V=$(mktemp -d /root/cpa-v8054-workbuddy-intl-shared-XXXXXXXX)
trap 'echo "FIXTURE_DIR=$V"' EXIT
docker pull "$IMAGE_REF"
repo_digest=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE_REF" | grep -F 'sha256:66e51ccba4786099f85f40439ba17f975dc5e9468def96ba914d3da39041d03a' | head -1)
[ -n "$repo_digest" ] || { echo DIGEST_READBACK_FAILED; exit 20; }
echo "DIGEST_READBACK=OK $repo_digest"
[ -s "$PLUGIN" ]
[ "$(sha256sum "$PLUGIN" | cut -d' ' -f1)" = "$PLUGIN_SHA" ] || { echo PLUGIN_SHA_MISMATCH; exit 20; }
patch_marker=$(
  strings -a "$PLUGIN" |
    grep -m1 -F 'WorkBuddy INTL shared upstream rate-limit window' ||
    true
)
[ -n "$patch_marker" ] || {
  echo PATCH_MARKER_MISSING
  exit 20
}
mkdir -p "$V/tmp" "$V/run" "$V/state" "$V/auth" "$V/plugins"
touch "$V/FIXTURE_ONLY"
cid=$(docker create "$IMAGE_REF")
docker cp "$cid:/CLIProxyAPI/CLIProxyAPI" "$V/CLIProxyAPI"
docker rm "$cid" >/dev/null
chmod +x "$V/CLIProxyAPI"
cp -a "$PLUGIN" "$V/plugins/workbuddy.so"
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
      models_intl: "deepseek-v4.1-flash"
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
grep "CLIProxyAPI Version: v8.0.54" plugin-load.log
grep "plugin registered plugin_id=workbuddy .*version=0.9.85" plugin-load.log
grep "scheduler_priority_reclaim=true applied" plugin-load.log
grep "account_max_concurrent=1 applied" plugin-load.log
! grep -Ei "panic|fatal|failed to load" plugin-load.log
echo ISOLATED_PLUGIN_LOAD=PASS
'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

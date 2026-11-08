#requires -Version 7
# Stage CLIProxyAPI v8.0.61 plus the official cpa-multi-plugins v0.8.97
# WorkBuddy .so (0.9.89) on bwg and prove the pair loads in a
# no-credential, no-default-network namespace. Nothing in production changes:
# the tag is pulled, the artifact extracted to a staging dir, and the host
# binary plus the plugin are exercised under unshare.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
TAG='eceasy/cli-proxy-api:v8.0.61'
EXPECTED_DIGEST=sha256:fc714b4339f20b59ee4781eb0f35a9e563aa357daece3081b0e266caeb11187e
LIVE_PLUGIN=/opt/cliproxyapi/plugins/workbuddy.so
LIVE_PLUGIN_SHA=6b2b2284ddd35a4461f39130fe95772c86744b59f36406a96d509c7dec8b9135
RELEASE_TAG=v0.8.97
ZIP_URL="https://github.com/mmqz/cpa-multi-plugins/releases/download/$RELEASE_TAG/cpa-multi-plugins-linux-amd64.zip"
ZIP_SHA=7c20744782cd674cab86123c33738cd948b1adb81dfe6967213d5fcf41bd25f4
NEW_PLUGIN_SHA=f2c678ff42a0cc9c21057473e0e7530754d9a058355b9e28190f52d930ad5de2
STAGE=/root/cpa-multi-plugins-$RELEASE_TAG-staging
V=$(mktemp -d /root/cpa-v8061-load-XXXXXXXX)
trap 'echo "FIXTURE_DIR=$V"' EXIT
docker pull "$TAG"
digest=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$TAG" | grep -o 'sha256:[0-9a-f]*' | head -1)
[ -n "$digest" ] || { echo DIGEST_READBACK_FAILED; exit 20; }
[ "$digest" = "$EXPECTED_DIGEST" ] || { echo "DIGEST_UNEXPECTED got=$digest"; exit 20; }
IMAGE_REF="$TAG@$digest"
docker image inspect "$IMAGE_REF" >/dev/null || { echo DIGEST_REF_INVALID; exit 20; }
echo "STAGE_DIGEST=$digest"

mkdir -m 700 -p "$STAGE"
NEW_SRC="$STAGE/workbuddy.so"
if [ ! -s "$NEW_SRC" ] || [ "$(sha256sum "$NEW_SRC" | cut -d' ' -f1)" != "$NEW_PLUGIN_SHA" ]; then
  ZIP="$STAGE/cpa-multi-plugins-linux-amd64.zip"
  curl --fail --silent --show-error --location --output "$ZIP" "$ZIP_URL"
  [ "$(sha256sum "$ZIP" | cut -d' ' -f1)" = "$ZIP_SHA" ] || { echo REFUSE_ZIP_SHA; exit 20; }
  python3 - "$ZIP" "$STAGE" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    member = next(
        name
        for name in archive.namelist()
        if name == "workbuddy.so" or name.endswith("/workbuddy.so")
    )
    with archive.open(member) as source, open(sys.argv[2] + "/workbuddy.so", "wb") as sink:
        sink.write(source.read())
PY
  [ "$(sha256sum "$NEW_SRC" | cut -d' ' -f1)" = "$NEW_PLUGIN_SHA" ] || {
    echo REFUSE_NEW_PLUGIN_SHA; exit 20;
  }
  chmod 700 "$NEW_SRC"
fi
echo "STAGED_PLUGIN_SHA=$NEW_PLUGIN_SHA"

mkdir -p "$V/tmp" "$V/run" "$V/state" "$V/auth" "$V/plugins"
touch "$V/FIXTURE_ONLY"
cid=$(docker create "$IMAGE_REF")
docker cp "$cid:/CLIProxyAPI/CLIProxyAPI" "$V/CLIProxyAPI"
docker rm "$cid" >/dev/null
chmod +x "$V/CLIProxyAPI"
cp -a "$NEW_SRC" "$V/plugins/workbuddy.so"
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
echo '---binary provenance---'
sha256sum "$V/CLIProxyAPI"
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
sleep 8
kill -0 "$pid"
grep "CLIProxyAPI Version: v8.0.61" plugin-load.log
grep "plugin registered plugin_id=workbuddy .*version=0.9.89" plugin-load.log
grep "scheduler_priority_reclaim=true applied" plugin-load.log
grep "account_max_concurrent=1 applied" plugin-load.log
! grep -Ei "panic|fatal|failed to load" plugin-load.log
echo ISOLATED_V8061_WB0989_LOAD=PASS
'
echo "LIVE_PLUGIN_SHA_NOW=$(sha256sum "$LIVE_PLUGIN" | cut -d' ' -f1) expected=$LIVE_PLUGIN_SHA"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

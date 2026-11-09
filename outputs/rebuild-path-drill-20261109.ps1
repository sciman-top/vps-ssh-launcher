#requires -Version 7
# Since no registry is being built, the rebuild path IS the recovery path.
# Verify it end to end with a dry run against the live base digest plus a
# tar round-trip, so "we can always rebuild" is demonstrated, not assumed.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -uo pipefail
DIR=/opt/cliproxyapi
LEDGER=$(cat "$DIR/fork-binary.sha256")
IMAGE=cli-proxy-api-fork:v8.0.61-gemini-raw
echo "ledger=$LEDGER"

echo '=== A. the documented rebuild inputs are all still resolvable'
docker image inspect eceasy/cli-proxy-api:v8.0.61@sha256:fc714b4339f20b59ee4781eb0f35a9e563aa357daece3081b0e266caeb11187e >/dev/null 2>&1 \
  && echo base_image_present=yes || echo base_image_present=no
[ -s /root/cpa-v8061-gemini-raw-build/CLIProxyAPI-v8061-gemini-raw ] \
  && echo fork_artifact_present=yes || echo fork_artifact_present=no
[ -s /root/cpa-multi-plugins-v0.8.97-staging/workbuddy.so ] \
  && echo plugin_artifact_present=yes || echo plugin_artifact_present=no

echo '=== B. image labels still declare provenance'
docker image inspect "$IMAGE" --format 'base={{index .Config.Labels "org.opencontainers.image.base.digest"}}'
docker image inspect "$IMAGE" --format 'fork={{index .Config.Labels "org.opencontainers.image.fork-binary.sha256"}}'

echo '=== C. tar round-trip (the documented cross-host path), then discard'
V=$(mktemp -d /root/tar-drill-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$V"' EXIT
if docker save "$IMAGE" -o "$V/image.tar" 2>/dev/null; then
  echo "save_ok=yes size_mib=$(du -m "$V/image.tar" | cut -f1)"
  sha256sum "$V/image.tar" | cut -d' ' -f1 > "$V/image.tar.sha256"
  echo "manifest_sha=$(cat "$V/image.tar.sha256")"
  docker load -i "$V/image.tar" >/dev/null 2>&1 && echo load_ok=yes || echo load_ok=no
else
  echo save_ok=no
fi
rm -f "$V/image.tar"

echo '=== D. runtime unchanged by all of the above'
echo "image=$(docker inspect --format '{{.Image}}' cli-proxy-api)"
echo "binary=$(docker exec cli-proxy-api sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)"
df -h / | tail -1
echo REBUILD_PATH_DRILL_DONE
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 400 -HardTimeoutSeconds 900

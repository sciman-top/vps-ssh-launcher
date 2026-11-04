#requires -Version 7
# Build WorkBuddy 0.9.85 plus the reviewed INTL shared-model-window patch.
# Runs the complete Go checks and a Linux amd64 C-shared build in a container
# on bwg. This script never touches /opt/cliproxyapi or credentials.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$patchPath = Join-Path $repoRoot 'scripts/patches/workbuddy-v0.8.93-intl-shared-window.patch'
$patchText = [Text.Encoding]::UTF8.GetString(
  [IO.File]::ReadAllBytes($patchPath)
).Replace("`r`n", "`n").Replace("`r", "`n")
$patchPayload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($patchText))
$patchSha = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($patchText))
).ToLowerInvariant()
$command = @'
set -Eeuo pipefail
umask 077
EXPECTED_COMMIT=c326248ef6fd3dbeee9e16c53adf279ffa5904cf
BUILD_ROOT=/root/cpa-workbuddy-0985-intl-shared-20261105-v2
PATCH_SHA=__PATCH_SHA__
[ ! -e "$BUILD_ROOT" ] || {
  echo "REFUSE build_root_exists path=$BUILD_ROOT"
  exit 20
}
mkdir -m 700 "$BUILD_ROOT"
git clone --branch v0.8.93 --depth 1 \
  https://github.com/mmqz/cpa-multi-plugins.git "$BUILD_ROOT/src"
ACTUAL_COMMIT=$(git -C "$BUILD_ROOT/src" rev-parse HEAD)
[ "$ACTUAL_COMMIT" = "$EXPECTED_COMMIT" ] || {
  echo "REFUSE commit=$ACTUAL_COMMIT expected=$EXPECTED_COMMIT"
  exit 20
}
[ "$(tr -d '\r\n' <"$BUILD_ROOT/src/plugins/workbuddy/VERSION")" = "0.9.85" ] || {
  echo "REFUSE workbuddy VERSION mismatch"
  exit 20
}
printf '%s' '__PATCH_PAYLOAD__' | base64 -d >"$BUILD_ROOT/intl-shared-window.patch"
[ "$(sha256sum "$BUILD_ROOT/intl-shared-window.patch" | awk '{print $1}')" = "$PATCH_SHA" ] || {
  echo 'REFUSE patch_sha_mismatch'
  exit 20
}
git -C "$BUILD_ROOT/src" apply --check "$BUILD_ROOT/intl-shared-window.patch"
git -C "$BUILD_ROOT/src" apply "$BUILD_ROOT/intl-shared-window.patch"
docker run --rm --network host --cpus=1 --memory=1024m \
  -v "$BUILD_ROOT/src/plugins/workbuddy":/src -w /src \
  -e CGO_ENABLED=1 -e GOOS=linux -e GOARCH=amd64 \
  golang:1.26-bookworm bash -c '
    set -Eeuo pipefail
    clean=$(gofmt -l *.go)
    if [ -n "$clean" ]; then
      echo "REFUSE gofmt_dirty"
      printf "%s\n" "$clean"
      exit 21
    fi
    go vet ./...
    go test -count=1 ./...
    go build -buildmode=c-shared -trimpath \
      -ldflags "-s -w -X main.version=0.9.85" -o workbuddy.so .
    readelf -h workbuddy.so | sed -n "1,20p"
    sha256sum workbuddy.so
    ls -l workbuddy.so
  '
ARTIFACT="$BUILD_ROOT/src/plugins/workbuddy/workbuddy.so"
ARTIFACT_SHA=$(sha256sum "$ARTIFACT" | awk '{print $1}')
{
  printf 'SOURCE_COMMIT=%s\n' "$ACTUAL_COMMIT"
  printf 'PATCH_SHA=%s\n' "$PATCH_SHA"
  printf 'ARTIFACT_SHA=%s\n' "$ARTIFACT_SHA"
  printf 'ARTIFACT=%s\n' "$ARTIFACT"
} | tee "$BUILD_ROOT/manifest.txt"
echo BUILD_OK
'@
$command = $command.Replace("__PATCH_SHA__", $patchSha).
  Replace("__PATCH_PAYLOAD__", $patchPayload)
if ($command -match "__[A-Z_]+__") {
  throw "Unfilled WorkBuddy 0.9.85 build placeholder."
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1800

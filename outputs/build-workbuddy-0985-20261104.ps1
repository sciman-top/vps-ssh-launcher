#requires -Version 7
# Build WorkBuddy 0.9.85 from the reviewed v0.8.93 upstream commit.
# Runs formatting, vet, the complete Go test suite, and a Linux amd64
# c-shared build in an isolated golang container on bwg. This script does
# not touch /opt/cliproxyapi, the CPA container, config, or credentials.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
EXPECTED_COMMIT=c326248ef6fd3dbeee9e16c53adf279ffa5904cf
BUILD_ROOT=$(mktemp -d /root/cpa-multi-plugins-0893-XXXXXXXX)
trap 'echo "BUILD_DIR=$BUILD_ROOT"' EXIT
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
printf 'SOURCE_COMMIT=%s\n' "$ACTUAL_COMMIT"
printf 'BUILD_ARTIFACT=%s\n' "$BUILD_ROOT/src/plugins/workbuddy/workbuddy.so"
echo BUILD_OK
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1800

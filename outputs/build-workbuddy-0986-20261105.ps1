#requires -Version 7
# Build official cpa-multi-plugins v0.8.94 / WorkBuddy 0.9.86 on BWG.
# No production file, container, config or credential is accessed.
param(
  [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
EXPECTED_COMMIT=6dccdfb9f0cef599324c420a24057ea605970789
BUILD_ROOT=/root/cpa-multi-plugins-0986-20261105
MODE='__MODE__'
case "$MODE" in dry-run|apply) ;; *) echo REFUSE_INVALID_MODE; exit 20;; esac
[ ! -e "$BUILD_ROOT" ] || {
  echo REFUSE_BUILD_ROOT_EXISTS path="$BUILD_ROOT"
  exit 20
}
mkdir -m 700 "$BUILD_ROOT"
git clone --branch v0.8.94 --depth 1 https://github.com/mmqz/cpa-multi-plugins.git "$BUILD_ROOT/src"
ACTUAL_COMMIT=$(git -C "$BUILD_ROOT/src" rev-parse HEAD)
[ "$ACTUAL_COMMIT" = "$EXPECTED_COMMIT" ] || {
  echo REFUSE_COMMIT expected="$EXPECTED_COMMIT" actual="$ACTUAL_COMMIT"
  exit 20
}
[ "$(tr -d '\r\n' <"$BUILD_ROOT/src/plugins/workbuddy/VERSION")" = 0.9.86 ] || {
  echo REFUSE_WORKBUDDY_VERSION
  exit 20
}
echo SOURCE_COMMIT="$ACTUAL_COMMIT" VERSION=0.9.86
if [ "$MODE" = dry-run ]; then
  echo "BUILD_DRY_RUN_OK build_root=$BUILD_ROOT source_commit=$ACTUAL_COMMIT no_build=true"
  exit 0
fi
docker run --rm --network host --cpus=1 --memory=1024m \
  -v "$BUILD_ROOT/src/plugins/workbuddy":/src -w /src \
  -e CGO_ENABLED=1 -e GOOS=linux -e GOARCH=amd64 \
  golang:1.26-bookworm bash -c '
    set -Eeuo pipefail
    clean=$(gofmt -l *.go)
    if [ -n "$clean" ]; then
      echo REFUSE_GOFMT_DIRTY
      printf "%s\n" "$clean"
      exit 21
    fi
    go vet ./...
    go test -count=1 ./...
    go build -buildmode=c-shared -trimpath \
      -ldflags "-s -w -X main.version=0.9.86" -o workbuddy.so .
    sha256sum workbuddy.so
    readelf -h workbuddy.so | sed -n "1,20p"
  '
ARTIFACT="$BUILD_ROOT/src/plugins/workbuddy/workbuddy.so"
[ -s "$ARTIFACT" ]
ARTIFACT_SHA=$(sha256sum "$ARTIFACT" | cut -d ' ' -f 1)
{
  printf 'SOURCE_COMMIT=%s\n' "$ACTUAL_COMMIT"
  printf 'WORKBUDDY_VERSION=0.9.86\n'
  printf 'ARTIFACT_SHA=%s\n' "$ARTIFACT_SHA"
  printf 'ARTIFACT=%s\n' "$ARTIFACT"
} | tee "$BUILD_ROOT/manifest.txt"
echo WORKBUDDY_0986_BUILD_OK
'@
$command = $command.Replace('__MODE__', $(if ($Apply) { 'apply' } else { 'dry-run' }))
if ($command -match '__MODE__') {
  throw 'Unfilled WorkBuddy 0.9.86 build mode.'
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1800

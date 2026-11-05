#requires -Version 7
# Build a temporary, fixture-only local replay variant of WorkBuddy 0.9.85.
# This is for scheduler-path verification, never production deployment.
param(
  [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$patchPath = Join-Path $repoRoot 'scripts/patches/workbuddy-v0.8.93-cn-local-replay.patch'
$patchText = [Text.Encoding]::UTF8.GetString(
  [IO.File]::ReadAllBytes($patchPath)
).Replace("`r`n", "`n").Replace("`r", "`n")
$payload = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($patchText)
)
$command = @'
set -Eeuo pipefail
umask 077
SOURCE=/root/cpa-workbuddy-0985-intl-shared-20261105-v2/src
ARTIFACT_SHA=9e335b13375392e5ffa744b4c38b9975684fed784ac4aad4b5d956abf62b33c4
ART=/root/wb-replay-artifacts
WORK=$(mktemp -d /root/wb-replay-build-XXXXXXXX)
BUILD_SRC="$WORK/src"
PATCH="$WORK/workbuddy-cn-replay-verification.patch"
MODE='__MODE__'
PATCH_PAYLOAD='__PATCH_PAYLOAD__'
cleanup() {
  if [ -n "${WORK:-}" ] && [ "${WORK#/root/wb-replay-build-}" != "$WORK" ]; then
    rm -rf --one-file-system -- "$WORK"
  fi
}
trap cleanup EXIT
case "$MODE" in dry-run|apply) ;; *) echo REFUSE_INVALID_MODE; exit 20;; esac
case "$SOURCE" in /root/cpa-workbuddy-0985-intl-shared-20261105-v2/src) ;; *) echo REFUSE_SOURCE_PATH; exit 20;; esac
[ -d "$SOURCE/.git" ] || { echo REFUSE_SOURCE_MISSING; exit 20; }
[ "$(git -C "$SOURCE" rev-parse HEAD)" = c326248ef6fd3dbeee9e16c53adf279ffa5904cf ] || {
  echo REFUSE_COMMIT_DRIFT; exit 20
}
STATUS_SHA=$(git -C "$SOURCE" status --porcelain | sort | sha256sum | cut -d ' ' -f 1)
[ "$STATUS_SHA" = 343b6061937f559cc0a1d5d7bd72847d2095841122dffb4fff0ba74b8a31d355 ] || {
  echo REFUSE_SOURCE_STATUS_DRIFT status_sha="$STATUS_SHA"; exit 20
}
printf '%s' "$PATCH_PAYLOAD" | base64 -d >"$PATCH"
chmod 600 "$PATCH"
echo PATCH_BYTES=$(wc -c <"$PATCH") PATCH_LINES=$(wc -l <"$PATCH")
git -C "$SOURCE" apply --check "$PATCH" || { rm -f -- "$PATCH"; echo REFUSE_PATCH_CHECK; exit 20; }
echo PATCH_CHECK_OK
if [ "$MODE" = dry-run ]; then
  echo BUILD_DRY_RUN_OK no_source_write=true no_artifact_write=true
  exit 0
fi
cp -a "$SOURCE" "$BUILD_SRC"
git -C "$BUILD_SRC" apply "$PATCH" || { echo REFUSE_PATCH_APPLY; exit 20; }
docker run --rm --network host --cpus=1 --memory=1024m \
  -v "$BUILD_SRC/plugins/workbuddy":/src -w /src \
  -e CGO_ENABLED=1 -e GOOS=linux -e GOARCH=amd64 \
  golang:1.26-bookworm bash -c '
    set -Eeuo pipefail
    [ -z "$(gofmt -l *.go)" ]
    go test -count=1 ./...
    go build -buildmode=c-shared -trimpath \
      -ldflags "-s -w -X main.version=0.9.85" -o workbuddy-replay.so .
    sha256sum workbuddy-replay.so
  '
BUILT="$BUILD_SRC/plugins/workbuddy/workbuddy-replay.so"
ACTUAL_SHA=$(sha256sum "$BUILT" | cut -d ' ' -f 1)
[ "$ACTUAL_SHA" = "$ARTIFACT_SHA" ] || {
  echo "REFUSE_ARTIFACT_SHA actual=$ACTUAL_SHA expected=$ARTIFACT_SHA"
  exit 20
}
mkdir -m 700 -p "$ART"
install -m 700 "$BUILT" "$ART/workbuddy-replay-$ARTIFACT_SHA.so"
[ "$(sha256sum "$ART/workbuddy-replay-$ARTIFACT_SHA.so" | cut -d ' ' -f 1)" = "$ARTIFACT_SHA" ]
[ "$(git -C "$SOURCE" status --porcelain | sort | sha256sum | cut -d ' ' -f 1)" = "$STATUS_SHA" ]
echo "WORKBUDDY_CN_LOCAL_REPLAY_BUILD_OK artifact=$ART/workbuddy-replay-$ARTIFACT_SHA.so source_status_unchanged=true"
'@
$command = $command.Replace('__PATCH_PAYLOAD__', $payload).
  Replace('__MODE__', $(if ($Apply) { 'apply' } else { 'dry-run' }))
if ($command -match '__(PATCH_PAYLOAD|MODE)__') {
  throw 'Unfilled build placeholder.'
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1800

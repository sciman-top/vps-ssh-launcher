#requires -Version 7
# Build the reviewed WorkBuddy 0.9.87 candidate from the pinned v0.8.94
# source. A fresh temporary copy receives the checked-in patch, then gofmt,
# vet and tests pass before the C-shared build. No production plugin, config,
# container or credential is accessed.
param(
  [switch]$Apply,
  [string]$Config
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$resolvedConfig = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
$patchPath = Join-Path $repoRoot 'scripts/patches/workbuddy-v0.8.94-canonical-6004-registry.patch'
$patchText = [Text.Encoding]::UTF8.GetString(
  [IO.File]::ReadAllBytes($patchPath)
).Replace("`r`n", "`n").Replace("`r", "`n")
$patchB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($patchText))
$patchHash = [Security.Cryptography.SHA256]::HashData(
  [Text.Encoding]::UTF8.GetBytes($patchText)
)
$patchSha256 = [BitConverter]::ToString($patchHash).Replace('-', '').ToLowerInvariant()
$mode = if ($Apply) { 'apply' } else { 'dry-run' }
$command = @'
set -Eeuo pipefail
umask 077
SRC=/root/cpa-multi-plugins-0986-20261105/src
ROOT=/root/cpa-workbuddy-candidate-0987
EXPECTED_COMMIT=6dccdfb9f0cef599324c420a24057ea605970789
MODE='__MODE__'
PATCH_B64='__PATCH_B64__'
FINAL_PATCH_SHA='__PATCH_SHA__'
case "$MODE" in dry-run|apply) ;; *) echo REFUSE_INVALID_MODE; exit 20;; esac
case "$SRC" in /root/cpa-multi-plugins-0986-20261105/src) ;; *) echo REFUSE_SOURCE_PATH; exit 20;; esac
case "$ROOT" in /root/cpa-workbuddy-candidate-0987) ;; *) echo REFUSE_CANDIDATE_PATH; exit 20;; esac
[ -d "$SRC/.git" ] && [ -d "$ROOT/.git" ] || { echo REFUSE_SOURCE_MISSING; exit 20; }
[ "$(git -C "$SRC" rev-parse HEAD)" = "$EXPECTED_COMMIT" ] || { echo REFUSE_SOURCE_COMMIT_DRIFT; exit 20; }
[ "$(git -C "$ROOT" rev-parse HEAD)" = "$EXPECTED_COMMIT" ] || { echo REFUSE_CANDIDATE_COMMIT_DRIFT; exit 20; }
[ -z "$(git -C "$SRC" status --porcelain)" ] || { echo REFUSE_SOURCE_DIRTY; exit 20; }
mkdir -m 700 -p "$ROOT/review"
printf '%s' "$PATCH_B64" | base64 -d >"$ROOT/review/canonical.patch"
chmod 600 "$ROOT/review/canonical.patch"
ACTUAL_PATCH_SHA=$(sha256sum "$ROOT/review/canonical.patch" | cut -d ' ' -f1)
[ "$ACTUAL_PATCH_SHA" = "$FINAL_PATCH_SHA" ] || {
  echo "PATCH_RECEIPT_MISMATCH actual=$ACTUAL_PATCH_SHA expected=$FINAL_PATCH_SHA"
  exit 20
}
echo PATCH_SHA="$ACTUAL_PATCH_SHA"
git -C "$SRC" apply --check "$ROOT/review/canonical.patch"
if [ "$MODE" = dry-run ]; then
  echo "BUILD_DRY_RUN_OK source_commit=$EXPECTED_COMMIT patch_applyable=true candidate_changed=true"
  exit 0
fi
FRESH=$(mktemp -d /root/wb-0987-build-XXXXXXXX)
cleanup() {
  case "$FRESH" in /root/wb-0987-build-*) rm -rf --one-file-system -- "$FRESH" ;; esac
}
trap cleanup EXIT
cp -a "$SRC" "$FRESH/src"
git -C "$FRESH/src" apply "$ROOT/review/canonical.patch"
for expected in \
  canonical_auth_key.go model_ratelimit.go model_ratelimit_test.go \
  models.go scheduler.go scheduler_test.go; do
  [ -s "$FRESH/src/plugins/workbuddy/$expected" ] ||
    { echo REFUSE_FRESH_FILE_MISSING "$expected"; exit 20; }
done
FRESH_DIR="$FRESH/src/plugins/workbuddy"
RUNTIME="$ROOT/plugins/workbuddy"
grep -Fq 'modelRateLimitErrorFor(c.ID, "", reqModel)' "$ROOT/plugins/workbuddy/scheduler.go"
grep -Fq 'modelRateLimitErrorFor(req.AuthID, uid, model.ID)' "$ROOT/plugins/workbuddy/models.go"
git -C "$SRC" diff --no-index --quiet -- "$SRC/plugins/workbuddy/scheduler.go" "$FRESH_DIR/scheduler.go" &&
  { echo REFUSE_FRESH_PATCH_DID_NOT_CHANGE; exit 20; }
docker run --rm --network host --cpus=1 --memory=1024m \
  -v "$RUNTIME":/out -v "$FRESH_DIR":/src -w /src \
  -e CGO_ENABLED=1 -e GOOS=linux -e GOARCH=amd64 \
  golang:1.26-bookworm bash -c '
    set -Eeuo pipefail
    gofmt -w *.go
    [ -z "$(gofmt -l *.go)" ]
    go vet ./...
    go test -count=1 ./...
    echo FRESH_SOURCE_TESTS_OK
    go build -buildmode=c-shared -trimpath \
      -ldflags "-s -w -X main.version=0.9.87" -o workbuddy-0987-canonical.so .
    sha256sum workbuddy-0987-canonical.so
    readelf -h workbuddy-0987-canonical.so | sed -n "1,20p"
    install -m 700 workbuddy-0987-canonical.so /out/workbuddy-0987-canonical.so
  '
ARTIFACT="$ROOT/plugins/workbuddy/workbuddy-0987-canonical.so"
[ -s "$ARTIFACT" ] || { echo REFUSE_ARTIFACT_MISSING; exit 20; }
ARTIFACT_SHA=$(sha256sum "$ARTIFACT" | cut -d ' ' -f1)
{
  echo "SOURCE_COMMIT=$EXPECTED_COMMIT"
  echo 'WORKBUDDY_VERSION=0.9.87'
  echo "ARTIFACT_SHA=$ARTIFACT_SHA"
  echo "ARTIFACT=$ARTIFACT"
} | tee "$ROOT/review/artifact-manifest.txt"
echo WORKBUDDY_0987_CANONICAL_BUILD_OK
'@
$command = $command.Replace('__MODE__', $mode).Replace('__PATCH_B64__', $patchB64).Replace('__PATCH_SHA__', $patchSha256)
if ($command -match '__(MODE|PATCH_B64|PATCH_SHA)__') {
  throw 'Unfilled WorkBuddy 0.9.87 build placeholder.'
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $resolvedConfig -Profile bwg -Command $command `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1800 -ViaTempFile

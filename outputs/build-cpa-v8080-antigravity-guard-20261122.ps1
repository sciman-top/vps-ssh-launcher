#requires -Version 7
# Build CLIProxyAPI v8.0.80 + gemini-raw + agy-effort + antigravity daily cap
# on bwg in /var/tmp. Nothing in production is touched; outputs binary to
# /root/cpa-v8080-build/.
param(
  [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$upstreamTag = 'v8.0.80'
$upstreamCommit = 'f5c5681a8361c9da53e93c60b6507816eee7468b'
$geminiPortPath = Join-Path $repoRoot 'outputs/cpa-gemini-raw-v8.0.79.tar.gz'
$agyPortPath = Join-Path $repoRoot 'outputs/cpa-agy-effort-v8.0.80.tar.gz'
$guardPortPath = Join-Path $repoRoot 'outputs/cpa-antigravity-guard-v8.0.80.tar.gz'
$geminiSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $geminiPortPath).Hash.ToLowerInvariant()
$agySha = (Get-FileHash -Algorithm SHA256 -LiteralPath $agyPortPath).Hash.ToLowerInvariant()
$guardSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $guardPortPath).Hash.ToLowerInvariant()
$geminiB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($geminiPortPath))
$agyB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($agyPortPath))
$guardB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($guardPortPath))
$mode = if ($DryRun) { 'dry-run' } else { 'apply' }

$command = @'
set -Eeuo pipefail
umask 077
MODE='__MODE__'
VERSION_TAG='__TAG__'
UPSTREAM_COMMIT='__COMMIT__'
GEMINI_SHA='__GEMINISHA__'
AGY_SHA='__AGYSHA__'
GUARD_SHA='__GUARDSHA__'
BUILD_IMAGE='golang:1.26.10-bookworm'
STAGE=/root/cpa-v8080-build
BUILD_ROOT=/var/tmp/cpa-fork-build-${VERSION_TAG}-guard
SRC="$BUILD_ROOT/src"
TARBALL="$BUILD_ROOT/CLIProxyAPI-$VERSION_TAG.tar.gz"
OUT="$STAGE/CLIProxyAPI-8.0.80-gemini-raw-agy-effort-daily-cap"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

mkdir -m 700 -p "$STAGE" "$BUILD_ROOT"
printf '%s' '__GEMINI_B64__' | base64 -d >"$STAGE/gemini-raw.tar.gz"
printf '%s' '__AGY_B64__' | base64 -d >"$STAGE/agy-effort.tar.gz"
printf '%s' '__GUARD_B64__' | base64 -d >"$STAGE/antigravity-guard.tar.gz"
[ "$(sha256sum "$STAGE/gemini-raw.tar.gz" | cut -d' ' -f1)" = "$GEMINI_SHA" ] || { log 'GEMINI_PORT_SHA_MISMATCH'; exit 20; }
[ "$(sha256sum "$STAGE/agy-effort.tar.gz" | cut -d' ' -f1)" = "$AGY_SHA" ] || { log 'AGY_PORT_SHA_MISMATCH'; exit 20; }
[ "$(sha256sum "$STAGE/antigravity-guard.tar.gz" | cut -d' ' -f1)" = "$GUARD_SHA" ] || { log 'GUARD_PORT_SHA_MISMATCH'; exit 20; }
log "OVERLAYS_OK gemini=$GEMINI_SHA agy=$AGY_SHA guard=$GUARD_SHA"

TAG_COMMIT=$(curl --fail --silent --show-error --location --max-time 30 \
  "https://api.github.com/repos/router-for-me/CLIProxyAPI/git/ref/tags/$VERSION_TAG" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["object"]["sha"])')
[ "$TAG_COMMIT" = "$UPSTREAM_COMMIT" ] || { log "TAG_COMMIT_MISMATCH got=$TAG_COMMIT want=$UPSTREAM_COMMIT"; exit 20; }
log "TAG_COMMIT_OK $VERSION_TAG $UPSTREAM_COMMIT"

if [ ! -s "$TARBALL" ]; then
  curl --fail --silent --show-error --location --max-time 240 \
    --output "$TARBALL.tmp" \
    "https://github.com/router-for-me/CLIProxyAPI/archive/$UPSTREAM_COMMIT.tar.gz" || {
      rm -f "$TARBALL.tmp"; log 'SOURCE_DOWNLOAD_FAILED'; exit 20;
    }
  mv "$TARBALL.tmp" "$TARBALL"
fi
TARBALL_SHA=$(sha256sum "$TARBALL" | cut -d' ' -f1)
TREE="CLIProxyAPI-$UPSTREAM_COMMIT"
log "SOURCE_TARBALL_SHA=$TARBALL_SHA"

MODULE=$(tar -xzf "$TARBALL" -O "$TREE/go.mod" | head -1 | tr -d '\r'; true)
[ "$MODULE" = 'module github.com/router-for-me/CLIProxyAPI/v8' ] || { log "BAD_MODULE $MODULE"; exit 20; }

rm -rf "$SRC"
mkdir -m 700 "$SRC"
tar -xzf "$TARBALL" -C "$SRC" --strip-components=1

# Verify allowed overlay paths only before extracting
GEMINI_DIR="$STAGE/gemini-check"; rm -rf "$GEMINI_DIR"; mkdir -m 700 "$GEMINI_DIR"
tar -xzf "$STAGE/gemini-raw.tar.gz" -C "$GEMINI_DIR"
GEMINI_BAD=$(cd "$GEMINI_DIR" && find . -type f ! -path "./internal/translator/gemini/openai/responses/*" | head -5)
[ -z "$GEMINI_BAD" ] || { log "GEMINI_PATH_ESCAPE $GEMINI_BAD"; exit 20; }
AGY_DIR="$STAGE/agy-check"; rm -rf "$AGY_DIR"; mkdir -m 700 "$AGY_DIR"
tar -xzf "$STAGE/agy-effort.tar.gz" -C "$AGY_DIR"
AGY_BAD=$(cd "$AGY_DIR" && find . -type f ! -path "./internal/runtime/executor/helps/model_capabilities.go" ! -path "./internal/runtime/executor/helps/thinking_test.go" ! -path "./internal/thinking/apply.go" ! -path "./internal/thinking/apply_envelope_effort_test.go" | head -5)
[ -z "$AGY_BAD" ] || { log "AGY_PATH_ESCAPE $AGY_BAD"; exit 20; }
GUARD_DIR="$STAGE/guard-check"; rm -rf "$GUARD_DIR"; mkdir -m 700 "$GUARD_DIR"
tar -xzf "$STAGE/antigravity-guard.tar.gz" -C "$GUARD_DIR"
GUARD_BAD=$(cd "$GUARD_DIR" && find . -type f ! -path "./internal/config/config_types.go" ! -path "./internal/runtime/executor/antigravity_daily_cap.go" ! -path "./internal/runtime/executor/antigravity_daily_cap_test.go" ! -path "./internal/runtime/executor/antigravity_executor_execute.go" ! -path "./internal/runtime/executor/antigravity_executor_stream.go" | head -5)
[ -z "$GUARD_BAD" ] || { log "GUARD_PATH_ESCAPE $GUARD_BAD"; exit 20; }

PRISTINE_TRANS=$(sha256sum "$SRC/internal/translator/gemini/openai/responses/gemini_openai-responses_response.go" | cut -d' ' -f1)
PRISTINE_APPLY=$(sha256sum "$SRC/internal/thinking/apply.go" | cut -d' ' -f1)
PRISTINE_MCAP=$(sha256sum "$SRC/internal/runtime/executor/helps/model_capabilities.go" | cut -d' ' -f1)
PRISTINE_CFG=$(sha256sum "$SRC/internal/config/config_types.go" | cut -d' ' -f1)
PRISTINE_EXEC=$(sha256sum "$SRC/internal/runtime/executor/antigravity_executor_execute.go" | cut -d' ' -f1)
PRISTINE_STREAM=$(sha256sum "$SRC/internal/runtime/executor/antigravity_executor_stream.go" | cut -d' ' -f1)

( cd "$SRC" && tar -xzf "$STAGE/gemini-raw.tar.gz" && tar -xzf "$STAGE/agy-effort.tar.gz" && tar -xzf "$STAGE/antigravity-guard.tar.gz" )

PATCHED_TRANS=$(sha256sum "$SRC/internal/translator/gemini/openai/responses/gemini_openai-responses_response.go" | cut -d' ' -f1)
PATCHED_APPLY=$(sha256sum "$SRC/internal/thinking/apply.go" | cut -d' ' -f1)
PATCHED_MCAP=$(sha256sum "$SRC/internal/runtime/executor/helps/model_capabilities.go" | cut -d' ' -f1)
PATCHED_CFG=$(sha256sum "$SRC/internal/config/config_types.go" | cut -d' ' -f1)
PATCHED_EXEC=$(sha256sum "$SRC/internal/runtime/executor/antigravity_executor_execute.go" | cut -d' ' -f1)
PATCHED_STREAM=$(sha256sum "$SRC/internal/runtime/executor/antigravity_executor_stream.go" | cut -d' ' -f1)
[ "$PATCHED_TRANS" != "$PRISTINE_TRANS" ] || { log 'GEMINI_NO_EFFECT'; exit 20; }
[ "$PATCHED_APPLY" != "$PRISTINE_APPLY" ] || { log 'AGY_APPLY_NO_EFFECT'; exit 20; }
[ "$PATCHED_MCAP" != "$PRISTINE_MCAP" ] || { log 'AGY_MCAP_NO_EFFECT'; exit 20; }
[ "$PATCHED_CFG" != "$PRISTINE_CFG" ] || { log 'GUARD_CFG_NO_EFFECT'; exit 20; }
[ "$PATCHED_EXEC" != "$PRISTINE_EXEC" ] || { log 'GUARD_EXEC_NO_EFFECT'; exit 20; }
[ "$PATCHED_STREAM" != "$PRISTINE_STREAM" ] || { log 'GUARD_STREAM_NO_EFFECT'; exit 20; }
log "PATCHED_SHAS trans=$PATCHED_TRANS apply=$PATCHED_APPLY mcap=$PATCHED_MCAP cfg=$PATCHED_CFG exec=$PATCHED_EXEC stream=$PATCHED_STREAM"

docker image inspect "$BUILD_IMAGE" >/dev/null 2>&1 || { log "BUILD_IMAGE_MISSING $BUILD_IMAGE"; exit 20; }
if [ "$MODE" = 'dry-run' ]; then
  log "DRY_RUN_OK tag=$VERSION_TAG commit=$UPSTREAM_COMMIT"
  exit 0
fi

mkdir -m 700 -p /var/tmp/cpa-gomodcache
docker run --rm --cpus=2 --memory=1900m \
  -v "$SRC":/src -v /var/tmp/cpa-gomodcache:/go/pkg/mod -w /src \
  -e CGO_ENABLED=1 -e GOFLAGS=-mod=mod -e GOTOOLCHAIN=local "$BUILD_IMAGE" bash -c '
set -Eeuo pipefail
go version
go vet ./internal/translator/gemini/openai/responses/ ./internal/thinking/ ./internal/runtime/executor/helps/ ./internal/config/ ./internal/runtime/executor/
go test -count=1 ./internal/config/ ./internal/runtime/executor/
' 2>&1 | tail -30
log 'UNIT_TESTS_OK'

BUILD_DATE=$(date -u +%FT%TZ)
docker run --rm --cpus=2 --memory=1900m \
  -v "$SRC":/src -v /var/tmp/cpa-gomodcache:/go/pkg/mod -w /src \
  -e CGO_ENABLED=1 -e GOFLAGS=-mod=mod -e GOTOOLCHAIN=local "$BUILD_IMAGE" bash -c '
set -Eeuo pipefail
go build -buildvcs=false -trimpath \
  -ldflags="-s -w -X main.Version='"$VERSION_TAG"' -X main.Commit='"$UPSTREAM_COMMIT"'+gemini-raw+agy-effort+daily-cap -X main.BuildDate='"$BUILD_DATE"'" \
  -o /src/CLIProxyAPI.out ./cmd/server/
'
install -m 755 "$SRC/CLIProxyAPI.out" "$OUT"
NEW_SHA=$(sha256sum "$OUT" | cut -d' ' -f1)
log "BUILT_SHA=$NEW_SHA"
log "BUILT_PATH=$OUT"
rm -rf "$BUILD_ROOT" /var/tmp/cpa-gomodcache
log "BUILD_ROOT_CLEANED"
'@
$command = $command.Replace('__MODE__', $mode)
$command = $command.Replace('__TAG__', $upstreamTag)
$command = $command.Replace('__COMMIT__', $upstreamCommit)
$command = $command.Replace('__GEMINISHA__', $geminiSha)
$command = $command.Replace('__AGYSHA__', $agySha)
$command = $command.Replace('__GUARDSHA__', $guardSha)
$command = $command.Replace('__GEMINI_B64__', $geminiB64)
$command = $command.Replace('__AGY_B64__', $agyB64)
$command = $command.Replace('__GUARD_B64__', $guardB64)
if ($command -match '__[A-Z_]{3,}__') { throw 'Unfilled placeholder.' }
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 900 -HardTimeoutSeconds 3600

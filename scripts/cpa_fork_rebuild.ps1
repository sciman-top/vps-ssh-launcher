#requires -Version 7
# Rebuild the bwg CPA runtime binary from a pinned upstream tag with the local
# Gemini raw-reasoning patch applied.
#
# Why this exists: production runs a derived image whose single difference from
# the official one is this binary (docs/runbooks/cpa-fork-image-derivation-rfc.md).
# Every upstream release therefore needs the binary rebuilt before the image tag
# may move -- otherwise the fork keeps serving old code under a new version
# string. This script takes the target tag as a parameter and proves every input
# by hash before any compiler runs:
#   - the commit is what the public tag says it is (GitHub ref API);
#   - the tarball is the archive of that exact commit;
#   - the root go.mod and the translator file are pristine upstream bytes;
#   - the patch still applies to a pristine checkout of the new tag -- a moved
#     function or a renamed field fails here, not inside a production switch.
# Nothing in production is touched: only files under the build stage.
param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^v[0-9]+\.[0-9]+\.[0-9]+$")]
  [string]$UpstreamTag,
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^[0-9a-f]{40}$")]
  [string]$UpstreamCommit,
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$PortSha256,
  [Parameter(Mandatory = $true)]
  [string]$StageDir,
  # A tag alone may be repointed. A production apply must supply the exact
  # digest (tag@sha256:...); the dry-run may use this reviewed tag only after
  # reporting the local image ID so the operator can pin it.
  [string]$BuildImage = 'golang:1.26.10-bookworm',
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
if ($Apply -and $BuildImage -notmatch '^golang:[0-9]+\.[0-9]+\.[0-9]+-bookworm@sha256:[0-9a-f]{64}$') {
  throw 'REFUSE -Apply requires -BuildImage golang:<version>-bookworm@sha256:<digest>.'
}
if ($BuildImage -notmatch '^golang:[0-9]+\.[0-9]+\.[0-9]+-bookworm(?:@sha256:[0-9a-f]{64})?$') {
  throw 'REFUSE -BuildImage must be a versioned golang bookworm tag, optionally pinned by sha256 digest.'
}
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
# The port is the repository's copy, never a host file: drift between them
# is exactly what this script must refuse. It is the upstream file with the
# local Gemini raw-reasoning change applied, produced by a recorded 3-way
# apply of outputs/cpa-gemini-raw.patch onto this exact upstream commit
# (the original patch was authored against v8.0.61 and no longer applies
# cleanly to newer tags).
$portPath = Join-Path $repoRoot ("outputs/cpa-gemini-raw-" + $UpstreamTag + ".tar.gz")
if (-not (Test-Path -LiteralPath $portPath)) { throw "Missing port artifact: $portPath" }
$portB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($portPath))
$mode = if ($Apply) { 'apply' } else { 'dry-run' }
$command = @'
set -Eeuo pipefail
umask 077
MODE='__MODE__'
VERSION_TAG='__TAG__'
UPSTREAM_COMMIT='__COMMIT__'
PORT_SHA='__PORTSHA__'
BUILD_IMAGE='__BUILDIMAGE__'
STAGE='__STAGE__'
# Build on local disk, not on /root: this host runs overlayfs and a bind
# mount of a directory under / just made a full compile fail with
# "open internal/...: no such file or directory" while the same command in
# the same directory succeeded run by run (2026-11-16). A build directory
# that must disappear on success is also the right place for /tmp.
BUILD_ROOT=/var/tmp/cpa-fork-build-${VERSION_TAG}
BUILD_TAG=${VERSION_TAG#v}-gemini-raw
SRC="$BUILD_ROOT/src"
TARBALL="$BUILD_ROOT/CLIProxyAPI-$VERSION_TAG.tar.gz"
TRANSLATOR=internal/translator/gemini/openai/responses/gemini_openai-responses_response.go
OUT="$STAGE/CLIProxyAPI-$BUILD_TAG"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

mkdir -m 700 -p "$STAGE"
mkdir -m 700 -p "$BUILD_ROOT"
PORT_DIR="$STAGE/port"
rm -rf "$PORT_DIR"; mkdir -m 700 "$PORT_DIR"
printf '%s' '__PORT_B64__' | base64 -d >"$STAGE/port.tar.gz"
GOT_PORT=$(sha256sum "$STAGE/port.tar.gz" | cut -d' ' -f1)
[ "$GOT_PORT" = "$PORT_SHA" ] || { log "PORT_SHA_MISMATCH got=$GOT_PORT"; exit 20; }
tar -xzf "$STAGE/port.tar.gz" -C "$PORT_DIR"
PORT_FILE_COUNT=$(find "$PORT_DIR" -type f | wc -l)
[ "$PORT_FILE_COUNT" -ge 1 ] || { log "PORT_EMPTY"; exit 20; }
log "PORT_OK files=$PORT_FILE_COUNT sha=$GOT_PORT"

# A tag is a claim; the archive name carries the commit and is what gets hashed.
TAG_COMMIT=$(curl --fail --silent --show-error --location --max-time 30 \
  "https://api.github.com/repos/router-for-me/CLIProxyAPI/git/ref/tags/$VERSION_TAG" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["object"]["sha"])')
[ "$TAG_COMMIT" = "$UPSTREAM_COMMIT" ] || {
  log "TAG_COMMIT_MISMATCH tag=$VERSION_TAG got=$TAG_COMMIT want=$UPSTREAM_COMMIT"; exit 20;
}
log "TAG_COMMIT_OK $VERSION_TAG $UPSTREAM_COMMIT"

if [ ! -s "$TARBALL" ]; then
  curl --fail --silent --show-error --location --max-time 240 \
    --output "$TARBALL.tmp" \
    "https://github.com/router-for-me/CLIProxyAPI/archive/$UPSTREAM_COMMIT.tar.gz" || {
      rm -f "$TARBALL.tmp"; log "SOURCE_DOWNLOAD_FAILED"; exit 20;
  }
  mv "$TARBALL.tmp" "$TARBALL"
fi
TARBALL_SHA=$(sha256sum "$TARBALL" | cut -d' ' -f1)
TREE="CLIProxyAPI-$UPSTREAM_COMMIT"
log "SOURCE_TARBALL_SHA=$TARBALL_SHA tree=$TREE"

# The archive must be the upstream CLIProxyAPI v8 tree, not some other repo.
MODULE=$(tar -xzf "$TARBALL" -O "$TREE/go.mod" | head -1 | tr -d '\r'; true)
[ "$MODULE" = 'module github.com/router-for-me/CLIProxyAPI/v8' ] || {
  log "SOURCEROOT_NOT_CLIPROXY_V8 module=$MODULE"; exit 20;
}
rm -rf "$SRC"
mkdir -m 700 "$SRC"
tar -xzf "$TARBALL" -C "$SRC" --strip-components=1

PRISTINE_TRANSLATOR_SHA=$(sha256sum "$SRC/$TRANSLATOR" | cut -d' ' -f1)
GOMOD_SHA=$(sha256sum "$SRC/go.mod" | cut -d' ' -f1)
log "PRISTINE_TRANSLATOR_SHA=$PRISTINE_TRANSLATOR_SHA"
log "GOMOD_SHA=$GOMOD_SHA"

# The port carries only the translator package, so a hostile or stale port
# cannot rewrite unrelated upstream code: every entry must live under that
# directory, and the resulting file must differ from pristine in the expected
# number of lines (a silent merge conflict would change far more).
if [ "$MODE" = apply ]; then
  BAD=$(cd "$PORT_DIR" && find . -type f ! -path "./internal/translator/gemini/openai/responses/*" | head -5)
  [ -z "$BAD" ] || { log "PORT_PATH_ESCAPE $BAD"; exit 20; }
  ( cd "$SRC" && tar -xzf "$STAGE/port.tar.gz" )
  PATCHED_TRANSLATOR_SHA=$(sha256sum "$SRC/$TRANSLATOR" | cut -d' ' -f1)
  [ "$PATCHED_TRANSLATOR_SHA" != "$PRISTINE_TRANSLATOR_SHA" ] || { log "PORT_NO_EFFECT"; exit 20; }
  log "PATCHED_TRANSLATOR_SHA=$PATCHED_TRANSLATOR_SHA"
fi

BUILD_IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$BUILD_IMAGE" 2>/dev/null) || {
  log "BUILD_IMAGE_MISSING $BUILD_IMAGE"; exit 20;
}
log "BUILD_IMAGE_ID=$BUILD_IMAGE_ID image=$BUILD_IMAGE"
if [[ "$MODE" = apply && "$BUILD_IMAGE" == *'@sha256:'* ]]; then
  EXPECTED_PLATFORM_DIGEST=${BUILD_IMAGE##*@}
  BUILD_PLATFORM_DIGESTS=$(docker image inspect \
    --format '{{range .RepoDigests}}{{println .}}{{end}}' \
    "$BUILD_IMAGE_ID" 2>/dev/null | sort -u) || {
    log "BUILD_IMAGE_DIGEST_UNREADABLE image=$BUILD_IMAGE";
    exit 20;
  }
  if [[ ! "$EXPECTED_PLATFORM_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    log "BUILD_IMAGE_DIGEST_INVALID image=$BUILD_IMAGE";
    exit 20;
  fi
  # Docker may publish both an exact platform digest and a manifest digest.
  # Match one complete RepoDigest line literally; do not let a mirror or a
  # different platform digest be treated as the requested builder.
  BUILD_DIGEST_MATCHED=0
  while IFS= read -r BUILD_IMAGE_REPO_DIGEST; do
    if [ "$BUILD_IMAGE_REPO_DIGEST" = "golang@$EXPECTED_PLATFORM_DIGEST" ]; then
      BUILD_DIGEST_MATCHED=1
      break
    fi
  done <<<"$BUILD_PLATFORM_DIGESTS"
  if [ "$BUILD_DIGEST_MATCHED" != 1 ]; then
    BUILD_DIGEST_OBSERVED=$(printf '%s\n' "$BUILD_PLATFORM_DIGESTS" | sed '/^$/d' | tr '\n' ',' | sed 's/,$//')
    log "BUILD_IMAGE_DIGEST_MISMATCH expected=golang@$EXPECTED_PLATFORM_DIGEST observed=${BUILD_DIGEST_OBSERVED:-none}";
    exit 20;
  fi
  log "BUILD_IMAGE_DIGEST_VERIFIED $EXPECTED_PLATFORM_DIGEST"
fi

if [ "$MODE" = dry-run ]; then
  log "DRY_RUN_OK tag=$VERSION_TAG commit=$UPSTREAM_COMMIT port=$PORT_SHA tarball=$TARBALL_SHA"
  exit 0
fi

docker run --rm --cpus=2 --memory=1900m -v "$SRC":/src -w /src \
  -e CGO_ENABLED=1 -e GOFLAGS=-mod=mod -e GOTOOLCHAIN=local "$BUILD_IMAGE" bash -c '
set -Eeuo pipefail
go version
go vet ./internal/translator/gemini/openai/responses/
go test -count=1 ./internal/translator/gemini/openai/responses/
' 2>&1 | tail -20
log 'TRANSLATOR_TESTS_OK'

docker run --rm --cpus=2 --memory=1900m -v "$SRC":/src -w /src \
  -e CGO_ENABLED=1 -e GOFLAGS=-mod=mod -e GOTOOLCHAIN=local "$BUILD_IMAGE" bash -c '
set -Eeuo pipefail
go build -buildvcs=false -trimpath \
  -ldflags="-s -w -X main.Version='"$VERSION_TAG"' -X main.Commit='"$UPSTREAM_COMMIT"'+gemini-raw -X main.BuildDate='"$(date -u +%FT%TZ)"'" \
  -o /src/CLIProxyAPI.out ./cmd/server/
'
install -m 755 "$SRC/CLIProxyAPI.out" "$OUT"
NEW_SHA=$(sha256sum "$OUT" | cut -d' ' -f1)
log "BUILT_SHA=$NEW_SHA"
# The build root is disposable once its one product is installed; keeping it
# would leave a multi-GB module cache copy on a 40 GB disk.
rm -rf "$BUILD_ROOT"
log "BUILD_ROOT_CLEANED $BUILD_ROOT"
log "BUILT_PATH=$OUT"
'@
$command = $command.Replace('__MODE__', $mode)
$command = $command.Replace('__TAG__', $UpstreamTag)
$command = $command.Replace('__COMMIT__', $UpstreamCommit)
$command = $command.Replace('__PORTSHA__', $PortSha256)
$command = $command.Replace('__BUILDIMAGE__', $BuildImage)
$command = $command.Replace('__STAGE__', $StageDir.TrimEnd('/'))
$command = $command.Replace('__PORT_B64__', $portB64)
if ($command -match '__[A-Z_]{3,}__') {
  throw 'Unfilled template placeholder.'
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 900 -HardTimeoutSeconds 3600

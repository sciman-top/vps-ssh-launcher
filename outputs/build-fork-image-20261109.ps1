#requires -Version 7
# Build the reproducible fork-derived CPA image on bwg.
#
# Why this exists: the 2026-11-09 convergence ledger told the next operator to
# "replay the build from this table", but the build was performed by hand and
# never committed. That makes the derivation unreproducible exactly when it
# matters (upstream publishes v8.0.62, or the host is replaced). This script is
# the executable form of that table.
#
# Contract:
#   - the base image is pinned by index digest, never a bare tag;
#   - OCI provenance labels record the base and the fork binary digest, so
#     "docker image inspect" answers "what is this" without reading prose;
#   - the produced binary must hash to scripts/remote/fork-binary.sha256;
#   - nothing in production is touched unless -Apply is passed, and even then
#     only a local image tag is written (no container restart).
param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^v[0-9]+\.[0-9]+\.[0-9]+$")]
  [string]$UpstreamTag,
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^sha256:[0-9a-f]{64}$")]
  [string]$BaseDigest,
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$BinarySha256,
  [Parameter(Mandatory = $true)]
  [string]$ForkBinaryPath,
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$mode = if ($Apply) { 'apply' } else { 'dry-run' }
$command = @'
set -Eeuo pipefail
umask 077
MODE='__MODE__'
UPSTREAM_TAG='__TAG__'
BASE_DIGEST='__BASE__'
BINARY_SHA='__BINARY__'
FORK_BINARY_SRC='__FORKSBIN__'
STAGE=/root/cpa-fork-image-build
CONTEXT="$STAGE/context"
IMAGE="cli-proxy-api-fork:${UPSTREAM_TAG}-gemini-raw"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

mkdir -m 700 -p "$CONTEXT"
BASE_REF="eceasy/cli-proxy-api:${UPSTREAM_TAG}@${BASE_DIGEST}"
if ! docker image inspect "$BASE_REF" >/dev/null 2>&1; then
  log "PULL_BASE $BASE_REF"
  docker pull "$BASE_REF"
fi
# A tag can move; a digest cannot. Assert what we actually got.
GOT_DIGEST=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$BASE_REF" | sed -n 's/.*@\(sha256:[0-9a-f]*\)$/\1/p' | head -1)
[ "$GOT_DIGEST" = "$BASE_DIGEST" ] || { log "BASE_DIGEST_MISMATCH got=${GOT_DIGEST:-none}"; exit 20; }
log "BASE_DIGEST_OK $BASE_DIGEST"

# The fork binary is NOT the binary inside the base image: the base ships the
# official build (verified 2026-11-09: base carries f822140a..., the fork
# carries 31f2f9d9...). It is produced by the separate rebuild transaction
# (outputs/build-cpa-v8061-gemini-raw-20261108.ps1) and consumed here.
# Read-back scratch space. The trap cleans it on every exit path, including
# the failure branches below, so a bad build never leaves a 75 MB artifact in
# /root waiting for a human to notice.
V=$(mktemp -d /root/fork-image-stage-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$V"' EXIT

[ -s "$FORK_BINARY_SRC" ] || { log "FORK_BINARY_MISSING src=$FORK_BINARY_SRC"; exit 20; }
GOT_SHA=$(sha256sum "$FORK_BINARY_SRC" | cut -d' ' -f1)
[ "$GOT_SHA" = "$BINARY_SHA" ] || { log "FORK_BINARY_SHA_MISMATCH got=$GOT_SHA want=$BINARY_SHA"; exit 20; }
log "FORK_BINARY_SHA_OK $GOT_SHA"
install -m 755 "$FORK_BINARY_SRC" "$CONTEXT/CLIProxyAPI-fork"

cat >"$CONTEXT/Dockerfile" <<'DOCKER'
FROM __BASE_REF__
# The only difference from the official image is the entrypoint binary: this
# fork carries the local Gemini raw-reasoning patch while the patch remains
# outside upstream. Provenance labels make "what is this image" answerable from
# the image itself instead of from a prose ledger.
LABEL org.opencontainers.image.base.name="eceasy/cli-proxy-api" \
      org.opencontainers.image.base.digest="__BASE_DIGEST__" \
      org.opencontainers.image.derived-by="vps-ssh-launcher/outputs/build-fork-image-20261109.ps1" \
      org.opencontainers.image.fork-binary.sha256="__BINARY_SHA__" \
      org.opencontainers.image.upstream.tag="__UPSTREAM_TAG__"
COPY --chmod=0755 CLIProxyAPI-fork /CLIProxyAPI/CLIProxyAPI
DOCKER
sed -i "s|__BASE_REF__|$BASE_REF|; s|__BASE_DIGEST__|$BASE_DIGEST|; s|__BINARY_SHA__|$BINARY_SHA|; s|__UPSTREAM_TAG__|$UPSTREAM_TAG|" "$CONTEXT/Dockerfile"
log "CONTEXT_READY image=$IMAGE"

if [ "$MODE" = dry-run ]; then
  log "DRY_RUN_OK would_build=$IMAGE base=$BASE_REF binary=$BINARY_SHA"
  exit 0
fi

log "BUILD_BEGIN $IMAGE"
docker build --pull=false --no-cache --label "org.opencontainers.image.created=$(date -u +%FT%TZ)" -t "$IMAGE" "$CONTEXT"

# Read back what the image actually carries: a tag is not evidence.
cid2=$(docker create "$IMAGE")
docker cp "$cid2:/CLIProxyAPI/CLIProxyAPI" "$V/from-built"
docker rm "$cid2" >/dev/null
BUILT_SHA=$(sha256sum "$V/from-built" | cut -d' ' -f1)
[ "$BUILT_SHA" = "$BINARY_SHA" ] || { log "BUILT_BINARY_SHA_MISMATCH got=$BUILT_SHA"; exit 20; }
log "BUILT_BINARY_SHA_OK $BUILT_SHA"

IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$IMAGE")
BASE_LABEL=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.base.digest"}}' "$IMAGE")
FORK_LABEL=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.fork-binary.sha256"}}' "$IMAGE")
[ "$BASE_LABEL" = "$BASE_DIGEST" ] || { log "LABEL_BASE_MISMATCH got=${BASE_LABEL:-none}"; exit 20; }
[ "$FORK_LABEL" = "$BINARY_SHA" ] || { log "LABEL_FORK_MISMATCH got=${FORK_LABEL:-none}"; exit 20; }
log "LABELS_OK base=$BASE_LABEL fork=$FORK_LABEL"
log "BUILD_OK image=$IMAGE image_id=$IMAGE_ID"
echo "NEXT_STEP=set compose image to $IMAGE inside a drain transaction (see outputs/cpa-v8061-converge-transaction-20261108.sh for the shape)"
'@
$command = $command.Replace('__MODE__', $mode).Replace('__TAG__', $UpstreamTag).Replace('__BASE__', $BaseDigest).Replace('__BINARY__', $BinarySha256).Replace('__FORKSBIN__', $ForkBinaryPath)
if ($command -match '__MODE__' -or $command -match '__FORKSBIN__') { throw 'Unfilled placeholder.' }
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1800

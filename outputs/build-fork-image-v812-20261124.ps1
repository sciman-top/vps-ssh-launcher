#requires -Version 7
# Build the reproducible fork-derived CPA image on bwg for the v8.1.2
# five-overlay generation (gemini-raw + agy-effort + daily-cap +
# crossauth-replay + sig-iso). Binary was built locally (WSL Ubuntu-24.04,
# golang 1.26.10, CGO_ENABLED=1) because the bwg host was under the
# build-lane memory floor; the glibc gate is the bookworm smoke that must
# precede this script. Same contract as build-fork-image-20261123.ps1:
# base pinned by digest, OCI provenance labels, binary hash asserted,
# nothing in production touched without -Apply.
param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^v[0-9]+\.[0-9]+\.[0-9]+$")]
  [string]$UpstreamTag = 'v8.1.2',
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^sha256:[0-9a-f]{64}$")]
  [string]$BaseDigest = 'sha256:37733f2a1eaa029e3915a37087b617f85535fe1b25534c8d2cb7f5152e23b02b',
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$BinarySha256 = '197894122e72960b367e37a8c6a1f770d20892c371057f6add5bb16a6f47f95a',
  [Parameter(Mandatory = $true)]
  [string]$ForkBinaryPath = '/root/cpa-v812-build/CLIProxyAPI-8.1.2-gemini-raw-agy-effort-daily-cap-authiso-sigiso',
  [string]$ImageSuffix = 'gemini-raw-daily-cap-authiso-sigiso',
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
IMAGE_SUFFIX='__SUFFIX__'
STAGE=/root/cpa-fork-image-build-v812
CONTEXT="$STAGE/context"
IMAGE="cli-proxy-api-fork:${UPSTREAM_TAG}-${IMAGE_SUFFIX}"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

mkdir -m 700 -p "$CONTEXT"
BASE_REF="eceasy/cli-proxy-api:${UPSTREAM_TAG}@${BASE_DIGEST}"
if ! docker image inspect "$BASE_REF" >/dev/null 2>&1; then
  log "PULL_BASE $BASE_REF"
  docker pull "$BASE_REF"
fi
GOT_DIGEST=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$BASE_REF" | sed -n 's/.*@\(sha256:[0-9a-f]*\)$/\1/p' | head -1)
[ "$GOT_DIGEST" = "$BASE_DIGEST" ] || { log "BASE_DIGEST_MISMATCH got=${GOT_DIGEST:-none}"; exit 20; }
log "BASE_DIGEST_OK $BASE_DIGEST"

V=$(mktemp -d /root/fork-image-stage-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$V"' EXIT

[ -s "$FORK_BINARY_SRC" ] || { log "FORK_BINARY_MISSING src=$FORK_BINARY_SRC"; exit 20; }
GOT_SHA=$(sha256sum "$FORK_BINARY_SRC" | cut -d' ' -f1)
[ "$GOT_SHA" = "$BINARY_SHA" ] || { log "FORK_BINARY_SHA_MISMATCH got=$GOT_SHA want=$BINARY_SHA"; exit 20; }
log "FORK_BINARY_SHA_OK $GOT_SHA"

# glibc gate: the binary was linked on Ubuntu 24.04 (glibc 2.39) while the
# runtime base is bookworm (glibc 2.36). Any referenced symbol above 2.36 or
# an exec failure must stop the transaction here, before any image exists.
GLIBC_VIOLATIONS=$(docker run --rm --entrypoint /bin/sh -v "$FORK_BINARY_SRC":/x:ro "eceasy/cli-proxy-api:${UPSTREAM_TAG}" \
  -c 'ldd /x 2>/dev/null | grep -oE "GLIBC_2\.(3[7-9]|[4-9][0-9])[0-9.]*" | sort -u' || true)
[ -z "$GLIBC_VIOLATIONS" ] || { log "GLIBC_SYMBOL_ABOVE_2_36 $GLIBC_VIOLATIONS"; exit 20; }
docker run --rm --entrypoint /bin/sh -v "$FORK_BINARY_SRC":/x:ro "eceasy/cli-proxy-api:${UPSTREAM_TAG}" \
  -c '/x -h >/dev/null 2>&1 || /x --help >/dev/null 2>&1' || { log 'BOOKWORM_SMOKE_FAILED'; exit 20; }
log 'BOOKWORM_SMOKE_OK'

install -m 755 "$FORK_BINARY_SRC" "$CONTEXT/CLIProxyAPI-fork"

cat >"$CONTEXT/Dockerfile" <<'DOCKER'
FROM __BASE_REF__
LABEL org.opencontainers.image.base.name="eceasy/cli-proxy-api" \
      org.opencontainers.image.base.digest="__BASE_DIGEST__" \
      org.opencontainers.image.derived-by="vps-ssh-launcher/outputs/build-fork-image-v812-20261124.ps1" \
      org.opencontainers.image.fork-binary.sha256="__BINARY_SHA__" \
      org.opencontainers.image.upstream.tag="__UPSTREAM_TAG__" \
      org.opencontainers.image.build.site="local-wsl-ubuntu-24.04"
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
echo "NEXT_STEP=swap transaction (outputs/cpa-v812-sigiso-swap-transaction-20261124.ps1)"
'@
$command = $command.Replace('__MODE__', $mode).Replace('__TAG__', $UpstreamTag).Replace('__BASE__', $BaseDigest).Replace('__BINARY__', $BinarySha256).Replace('__FORKSBIN__', $ForkBinaryPath).Replace('__SUFFIX__', $ImageSuffix)
if ($command -match '__MODE__' -or $command -match '__TAG__' -or $command -match '__BASE__' -or $command -match '__BINARY__' -or $command -match '__FORKSBIN__' -or $command -match '__SUFFIX__') { throw 'Unfilled placeholder.' }
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1800

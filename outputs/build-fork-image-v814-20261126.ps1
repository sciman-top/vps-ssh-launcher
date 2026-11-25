#requires -Version 7
# Build the fork-derived CPA image for the v8.1.4 five-overlay generation
# (gemini-raw + agy-effort + daily-cap + crossauth-replay + sig-iso). The two
# in-fork #7966 fixes of the v8.1.2 generation are gone: upstream fe571afe
# replaced emit-once, and upstream's own v8.1.4 test asserts the pairing order
# the seen-condition overlay used to reverse. Binary built locally in WSL.
param(
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
$UpstreamTag = 'v8.1.4'
$BaseDigest = 'sha256:32fb7dfc272bf63daee4dcaaa679095aad431ceed31416f98ea55f3d1d7db77a'
$ImageSuffix = 'gemini-raw-daily-cap-authiso-sigiso'
$command = @'
set -Eeuo pipefail
umask 077
MODE='__MODE__'
UPSTREAM_TAG='v8.1.4'
BASE_DIGEST='__BASE__'
BINARY_SHA='__BINARY__'
FORK_BINARY_SRC='__FORKSBIN__'
IMAGE_SUFFIX='__SUFFIX__'
OVERLAYS='gemini-raw+agy-effort+daily-cap+crossauth-replay+sig-iso'
STAGE=/root/cpa-fork-image-build-v814
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

[ -s "$FORK_BINARY_SRC" ] || { log "FORK_BINARY_MISSING src=$FORK_BINARY_SRC"; exit 20; }
GOT_SHA=$(sha256sum "$FORK_BINARY_SRC" | cut -d' ' -f1)
[ "$GOT_SHA" = "$BINARY_SHA" ] || { log "FORK_BINARY_SHA_MISMATCH got=$GOT_SHA want=$BINARY_SHA"; exit 20; }
log "FORK_BINARY_SHA_OK $GOT_SHA"

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
      org.opencontainers.image.derived-by="vps-ssh-launcher/outputs/build-fork-image-v814-20261126.ps1" \
      org.opencontainers.image.fork-binary.sha256="__BINARY_SHA__" \
      org.opencontainers.image.upstream.tag="v8.1.4" \
      org.opencontainers.image.fork.overlays="__OVERLAYS__" \
      org.opencontainers.image.build.site="local-wsl-ubuntu-24.04"
COPY --chmod=0755 CLIProxyAPI-fork /CLIProxyAPI/CLIProxyAPI
DOCKER
sed -i "s|__BASE_REF__|$BASE_REF|; s|__BASE_DIGEST__|$BASE_DIGEST|; s|__BINARY_SHA__|$BINARY_SHA|; s|__OVERLAYS__|$OVERLAYS|" "$CONTEXT/Dockerfile"
log "CONTEXT_READY image=$IMAGE"

if [ "$MODE" = dry-run ]; then
  log "DRY_RUN_OK would_build=$IMAGE base=$BASE_REF binary=$BINARY_SHA"
  exit 0
fi

log "BUILD_BEGIN $IMAGE"
docker build --pull=false --no-cache --label "org.opencontainers.image.created=$(date -u +%FT%TZ)" -t "$IMAGE" "$CONTEXT"

V=$(mktemp -d /root/fork-image-stage-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$V"' EXIT
cid2=$(docker create "$IMAGE")
docker cp "$cid2:/CLIProxyAPI/CLIProxyAPI" "$V/from-built"
docker rm "$cid2" >/dev/null
BUILT_SHA=$(sha256sum "$V/from-built" | cut -d' ' -f1)
[ "$BUILT_SHA" = "$BINARY_SHA" ] || { log "BUILT_BINARY_SHA_MISMATCH got=$BUILT_SHA"; exit 20; }
log "BUILT_BINARY_SHA_OK $BUILT_SHA"

IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$IMAGE")
FORK_LABEL=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.fork-binary.sha256"}}' "$IMAGE")
OVERLAY_LABEL=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.fork.overlays"}}' "$IMAGE")
[ "$FORK_LABEL" = "$BINARY_SHA" ] || { log "LABEL_FORK_MISMATCH got=${FORK_LABEL:-none}"; exit 20; }
[ "$OVERLAY_LABEL" = "$OVERLAYS" ] || { log "LABEL_OVERLAYS_MISMATCH got=${OVERLAY_LABEL:-none}"; exit 20; }
log "LABELS_OK overlays=$OVERLAY_LABEL"
log "BUILD_OK image=$IMAGE image_id=$IMAGE_ID"
echo "NEXT_STEP=swap transaction with OLD=v8.1.2-gemini-raw-daily-cap-authiso-sigiso-7966fix"
'@
$command = $command.Replace('__MODE__', $mode).Replace('__BASE__', $BaseDigest).Replace('__BINARY__', $BinarySha256).Replace('__FORKSBIN__', $ForkBinaryPath).Replace('__SUFFIX__', $ImageSuffix)
if ($command -match '__MODE__' -or $command -match '__BINARY__' -or $command -match '__FORKSBIN__' -or $command -match '__SUFFIX__' -or $command -match '__BASE__') { throw 'Unfilled placeholder.' }
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1800

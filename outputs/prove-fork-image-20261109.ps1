#requires -Version 7
# Prove the fork-image deployment really runs the fork binary. No production
# traffic or file is touched: the probe reads the live image, extracts its
# binary, and compares it byte-for-byte with the running container.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LIVE_IMAGE=$(docker inspect --format '{{.Config.Image}}' cli-proxy-api)
LEDGER_SHA=$(cat "$DIR/fork-binary.sha256" 2>/dev/null || true)
V=$(mktemp -d /root/fork-image-proof-XXXXXXXX)
trap 'echo "FIXTURE_DIR=$V"' EXIT
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
log "LIVE_IMAGE=$LIVE_IMAGE"
log "HOST_LEDGER_SHA=$LEDGER_SHA"

MOUNTS=$(docker inspect cli-proxy-api --format '{{range .Mounts}}{{.Destination}} {{end}}')
case " $MOUNTS " in
  *" /CLIProxyAPI/CLIProxyAPI "*) echo 'BIND_MOUNT_PRESENT_UNEXPECTED'; exit 20 ;;
  *) log 'NO_BINARY_BIND_MOUNT=OK' ;;
esac

cid=$(docker create "$LIVE_IMAGE")
docker cp "$cid:/CLIProxyAPI/CLIProxyAPI" "$V/from-image"
docker rm "$cid" >/dev/null
IMAGE_SHA=$(sha256sum "$V/from-image" | cut -d' ' -f1)
log "IMAGE_BINARY_SHA=$IMAGE_SHA"
if [ -n "$LEDGER_SHA" ] && [ "$IMAGE_SHA" != "$LEDGER_SHA" ]; then
  log "IMAGE_BINARY_LEDGER_MISMATCH"
  exit 20
fi

RUNNING_SHA=$(docker exec cli-proxy-api sha256sum /CLIProxyAPI/CLIProxyAPI | cut -d' ' -f1)
log "RUNNING_BINARY_SHA=$RUNNING_SHA"
[ "$RUNNING_SHA" = "$IMAGE_SHA" ] || { log 'RUNNING_BINARY_DIFFERS_FROM_IMAGE'; exit 20; }

BASE_NAME=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.base.name"}}' "$LIVE_IMAGE" 2>/dev/null || true)
BASE_DIGEST=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.base.digest"}}' "$LIVE_IMAGE" 2>/dev/null || true)
log "LABEL base_name=--${BASE_NAME}-- base_digest=--${BASE_DIGEST}--"

VERSION_LINE=$(docker exec cli-proxy-api /CLIProxyAPI/CLIProxyAPI --version 2>&1 | head -1 || true)
log "VERSION_PROBE=$VERSION_LINE"

echo FORK_IMAGE_PROOF=PASS
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 180 -HardTimeoutSeconds 600

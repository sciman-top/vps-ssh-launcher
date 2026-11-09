#requires -Version 7
# Complete the four rollback sets the 2026-11-09 audit found restorable only
# in name.
#
# Three "from-v8.0.59-to-v8.0.61-wb0989" attempts captured compose.yml but no
# binary, and the fork-image-compose backup captured compose.yml only. Their
# compose files mount ./CLIProxyAPI-patched, so restoring any of them without
# that file brings back the official image with the Gemini raw-reasoning patch
# silently dropped.
#
# The binary they need is the one that was live at the time: 330b7a55, which
# is still on disk in the converge-era backups. This copies it in and records
# provenance, so each set becomes a real rollback set.
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$mode = if ($Apply) { 'apply' } else { 'dry-run' }
$command = @'
set -Eeuo pipefail
MODE='__MODE__'
DIR=/opt/cliproxyapi
SOURCE_SHA=330b7a559ad3956bdbe090190ec822982ca26eb8817839bfe32d31a1724ed17f
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

# Locate one verified copy of the era binary to use as the source.
SOURCE=''
for c in "$DIR"/backups/*converge-v8.0.61*/CLIProxyAPI-patched; do
  [ -f "$c" ] || continue
  if [ "$(sha256sum "$c" | cut -d' ' -f1)" = "$SOURCE_SHA" ]; then SOURCE=$c; break; fi
done
[ -n "$SOURCE" ] || { echo NO_VERIFIED_SOURCE; exit 20; }
log "SOURCE=$SOURCE sha=$SOURCE_SHA"

fixed=0
for d in "$DIR"/backups/*from-v8.0.59-to-v8.0.61-wb0989 "$DIR"/backups/*fork-image-compose-20261109; do
  d=${d%/}
  [ -f "$d/compose.yml" ] || continue
  grep -q 'CLIProxyAPI-patched:/CLIProxyAPI/CLIProxyAPI' "$d/compose.yml" || continue
  if [ -f "$d/CLIProxyAPI-patched" ]; then
    got=$(sha256sum "$d/CLIProxyAPI-patched" | cut -d' ' -f1)
    log "ALREADY_PRESENT $(basename "$d") sha=$got"
    continue
  fi
  log "WOULD_COMPLETE $(basename "$d")"
  fixed=$((fixed+1))
  if [ "$MODE" = apply ]; then
    install -m 700 "$SOURCE" "$d/CLIProxyAPI-patched.new"
    mv -f "$d/CLIProxyAPI-patched.new" "$d/CLIProxyAPI-patched"
    got=$(sha256sum "$d/CLIProxyAPI-patched" | cut -d' ' -f1)
    [ "$got" = "$SOURCE_SHA" ] || { log "BACKFILL_SHA_MISMATCH $(basename "$d") $got"; exit 20; }
    printf 'binary_source=converge-era-backup\nbinary_sha256=%s\ncompleted_at=%s\n' "$SOURCE_SHA" "$(date -u +%FT%TZ)" > "$d/CLIProxyAPI-patched.provenance"
    chmod 600 "$d/CLIProxyAPI-patched.provenance"
  fi
done
log "COMPLETED mode=$MODE sets=$fixed"
echo RESTORE_SET_BACKFILL_DONE
'@
$command = $command.Replace('__MODE__', $mode)
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

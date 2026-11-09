#requires -Version 7
# Quarantine rollback sets whose image is gone, instead of leaving them
# looking restorable. Marking is reversible (rename the marker file back).
#
# The 2026-11-09 audit found 7 backups referencing images no longer on disk.
# All are September v7.x sets whose images were pruned long ago; none is a
# current rollback target. Marking them means an operator listing backups is
# told which ones cannot be restored, rather than finding out at 03:00.
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
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

marked=0
for d in "$DIR"/backups/*/; do
  d=${d%/}
  id=$(head -c 200 "$d/image-id" 2>/dev/null || echo none)
  [ "$id" = image_gone ] || continue
  [ -f "$d/UNRESTORABLE" ] && continue
  ref=$(python3 -c "import sys,yaml;print(yaml.safe_load(open(sys.argv[1]))['services']['cli-proxy-api']['image'])" "$d/compose.yml" 2>/dev/null || echo unreadable)
  log "MARK_UNRESTORABLE $(basename "$d") ref=$ref"
  marked=$((marked+1))
  if [ "$MODE" = apply ]; then
    printf 'reason=image_no_longer_present\nimage_ref=%s\nmarked_at=%s\nrestore_hint=rebuild_from_upstream_or_replay_build_and_update_this_backup\n' \
      "$ref" "$(date -u +%FT%TZ)" > "$d/UNRESTORABLE"
    chmod 600 "$d/UNRESTORABLE"
  fi
done
log "QUARANTINED mode=$MODE sets=$marked"
echo UNRESTORABLE_MARKING_DONE
'@
$command = $command.Replace('__MODE__', $mode)
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

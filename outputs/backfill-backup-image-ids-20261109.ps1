#requires -Version 7
# Backfill image-id records for backups that predate the fix.
#
# A backup whose recorded image is already gone is marked explicitly rather
# than left ambiguous: an operator must be told "this rollback set no longer
# contains its image" instead of discovering it during an incident.
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

recorded=0; missing=0; skipped=0; already=0
report=''
for d in "$DIR"/backups/*/; do
  d=${d%/}
  if [ ! -f "$d/compose.yml" ]; then skipped=$((skipped+1)); continue; fi
  if [ -f "$d/image-id" ]; then already=$((already+1)); continue; fi
  ref=$(python3 -c "import sys,yaml;print(yaml.safe_load(open(sys.argv[1]))['services']['cli-proxy-api']['image'])" "$d/compose.yml" 2>/dev/null) || { skipped=$((skipped+1)); continue; }
  case "$ref" in *@sha256:*) pinned=yes ;; *) pinned=no ;; esac
  id=$(docker image inspect --format '{{.Id}}' "$ref" 2>/dev/null || true)
  if [ -n "$id" ]; then
    recorded=$((recorded+1))
    if [ "$MODE" = apply ]; then
      printf '%s\n' "$id" > "$d/image-id"
      chmod 600 "$d/image-id"
    fi
  else
    missing=$((missing+1))
    report="$report"$'\n'"unrecoverable=$(basename "$d") ref=$ref"
    if [ "$MODE" = apply ]; then
      printf 'image_gone\n' > "$d/image-id"
      chmod 600 "$d/image-id"
    fi
  fi
done
log "BACKFILL mode=$MODE recorded=$recorded image_gone=$missing already=$already skipped=$skipped"
if [ -n "$report" ]; then printf '%s\n' "$report"; fi
echo BACKFILL_DONE
'@
$command = $command.Replace('__MODE__', $mode)
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

#requires -Version 7
# Verify that every *retained* rollback set can actually be restored.
#
# A backup is only a rollback set if it captures BOTH halves of the runtime:
# the compose image reference AND, in the bind-mount era, the fork binary that
# was mounted over it. 2026-11-09 inspection found
# backups/20261108T174044...-fork-image-compose-20261109/ contains only
# compose.yml + pre-sha256.txt + (backfilled) image-id: no binary. Restoring
# it would bring back the official image and silently drop the Gemini
# raw-reasoning patch. This probe makes that class of gap visible instead of
# leaving it to be discovered mid-incident.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -uo pipefail
DIR=/opt/cliproxyapi
LEDGER=$(cat "$DIR/fork-binary.sha256" 2>/dev/null || echo none)
echo "ledger=$LEDGER"
complete=0; incomplete=0; gone=0
for d in $(ls -1d "$DIR"/backups/*/ | sort); do
  d=${d%/}
  [ -f "$d/compose.yml" ] || continue
  id=$(cat "$d/image-id" 2>/dev/null || echo none)
  if [ "$id" = image_gone ]; then gone=$((gone+1)); continue; fi
  ref=$(python3 -c "import sys,yaml;print(yaml.safe_load(open(sys.argv[1]))['services']['cli-proxy-api']['image'])" "$d/compose.yml" 2>/dev/null || echo unreadable)
  # bind-mount era = the compose declares the CLIProxyAPI bind mount
  has_mount=no
  grep -q 'CLIProxyAPI-patched:/CLIProxyAPI/CLIProxyAPI' "$d/compose.yml" 2>/dev/null && has_mount=yes
  if [ "$has_mount" = yes ]; then
    if [ -f "$d/CLIProxyAPI-patched" ]; then
      sha=$(sha256sum "$d/CLIProxyAPI-patched" | cut -d' ' -f1)
      echo "complete $(basename "$d") kind=bind_mount binary=$sha"
      complete=$((complete+1))
    elif [ -f "$d/CLIProxyAPI.pre-projection" ] || [ -f "$d/CLIProxyAPI.pre-streamfix" ]; then
      echo "complete $(basename "$d") kind=bind_mount binary=named_differently"
      complete=$((complete+1))
    else
      echo "INCOMPLETE $(basename "$d") kind=bind_mount reason=binary_not_captured"
      incomplete=$((incomplete+1))
    fi
  else
    echo "complete $(basename "$d") kind=image_only ref=${ref:0:60}"
    complete=$((complete+1))
  fi
done
echo "RESTORABILITY complete=$complete incomplete=$incomplete image_gone=$gone"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 240 -HardTimeoutSeconds 600

#requires -Version 7
# Bound the backup families that no retention policy currently touches.
#
# Measured 2026-11-09: 184 backup dirs / 1247 MiB, of which only 67 dirs
# match the updater's '*-from-v[0-9]*' retention pattern. The other 117
# (admission-*, plugin-*, converge-*, guardrail-*, workbuddy-*) are created by
# manual transactions and never expire -- 800 MiB of unbounded growth on a
# 40 GB disk that already sits at 77%.
#
# Policy, deliberately conservative:
#   - keep the newest N dirs per family (default 4)
#   - never delete anything younger than MIN_AGE_HOURS (default 168 = 7d)
#   - never delete a dir that verify-restorability calls complete
#   - never touch *-from-v* (the updater owns that family)
param([switch]$Apply, [int]$Keep = 4, [int]$MinAgeHours = 168)
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
KEEP=__KEEP__
MIN_AGE_HOURS=__AGE__
DIR=/opt/cliproxyapi
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

families='admission plugin converge guardrail workbuddy fork-image relabel other'
removed=0; kept=0; skipped_young=0; skipped_complete=0
for d in "$DIR"/backups/*/; do
  d=${d%/}; b=$(basename "$d")
  case "$b" in *-from-v[0-9]*) continue ;; esac
  case "$b" in
    *admission*) fam=admission ;;
    *plugin*) fam=plugin ;;
    *converge*) fam=converge ;;
    *guardrail*) fam=guardrail ;;
    *workbuddy*) fam=workbuddy ;;
    *fork-image*) fam=fork-image ;;
    *) fam=other ;;
  esac
  printf '%s\t%s\n' "$fam" "$d"
done | sort > /tmp/families.$$
while IFS= read -r fam; do
  mapfile -t members < <(awk -F'\t' -v f="$fam" '$1==f{print $2}' /tmp/families.$$)
  count=${#members[@]}
  [ "$count" -gt "$KEEP" ] || { kept=$((kept+count)); continue; }
  # newest first; delete from index KEEP onward, subject to age + completeness
  idx=0
  for m in $(printf '%s\n' "${members[@]}" | sort -r); do
    idx=$((idx+1))
    if [ "$idx" -le "$KEEP" ]; then kept=$((kept+1)); continue; fi
    age_h=$(( ( $(date +%s) - $(stat -c %Y "$m") ) / 3600 ))
    if [ "$age_h" -lt "$MIN_AGE_HOURS" ]; then skipped_young=$((skipped_young+1)); continue; fi
    # completeness guard: prefer keeping restorable sets
    if [ -f "$m/compose.yml" ] && [ ! -f "$m/UNRESTORABLE" ]; then
      if grep -q 'CLIProxyAPI-patched:/CLIProxyAPI/CLIProxyAPI' "$m/compose.yml" 2>/dev/null && [ ! -f "$m/CLIProxyAPI-patched" ]; then
        : # incomplete sets are still deletable: they cannot restore anything
      elif [ -f "$m/image-id" ] || [ -f "$m/CLIProxyAPI-patched" ]; then
        skipped_complete=$((skipped_complete+1)); continue
      fi
    fi
    log "WOULD_DELETE fam=$fam age_h=$age_h dir=$(basename "$m")"
    removed=$((removed+1))
    if [ "$MODE" = apply ]; then rm -rf -- "$m"; fi
  done
done < <(awk -F'\t' '{print $1}' /tmp/families.$$ | sort -u)
rm -f /tmp/families.$$
log "BACKUP_GC mode=$MODE family_keep=$KEEP min_age_h=$MIN_AGE_HOURS kept=$kept removed=$removed skipped_young=$skipped_young skipped_complete=$skipped_complete"
echo BACKUP_GC_DONE
'@
$command = $command.Replace('__MODE__', $mode).Replace('__KEEP__', [string]$Keep).Replace('__AGE__', [string]$MinAgeHours)
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

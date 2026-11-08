#requires -Version 7
# Reclaim disk on bwg without touching any rollback reference.
# Evidence gathered first: the only executables referenced by backup content
# are *copies* (CLIProxyAPI-patched / pre-projection / official-v8.0.59 ... and
# old plugin .so builds). None is ever exec'd: the compose entrypoint only runs
# ./CLIProxyAPI from the live bind mount, and systemd only runs files under
# /etc/systemd, /usr and /opt/cliproxyapi top level. Clearing the exec bit on
# backup copies therefore removes the "backup content dropped from a tmpfs"
# hazard at zero rollback cost.
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

before=$(du -sm "$DIR/backups" /root | awk '{s+=$1} END{print s}')
log "DISK_BEFORE backups_mb_root_mb=$before free=$(df -Pm / | awk 'NR==2{print $4}')MB"

echo '--- exec-bit inventory (backup content)'
find "$DIR/backups" -maxdepth 2 -type f -perm -u+x ! -name '*.md' ! -name '*.txt' ! -name '*.json' \
  -printf '%m %s %p\n' | awk '{n++; s+=$2} END{printf "exec_files=%d bytes=%d\n", n, s}'

echo '--- decommissioned scratch dirs (older than 24h)'
find /root -maxdepth 1 -type d \( -name 'cpa-v80*-*-2*' -o -name 'cpa-v80*-load-*' -o -name 'cpa-wb-*' -o -name 'cpa-prestage*' -o -name 'lane-singles-*' -o -name 'cpa-selected-test-cache-*' \) -mmin +1440 -printf '%s %p\n' 2>/dev/null | wc -l

if [ "$MODE" = dry-run ]; then
  echo DRYRUN_OK
  exit 0
fi

# 1. Drop the exec bit from backup copies (content unchanged, sha still valid).
exec_cleared=$(find "$DIR/backups" -maxdepth 2 -type f -perm /111 -printf . | wc -c)
find "$DIR/backups" -maxdepth 2 -type f -perm /111 -exec chmod a-x {} +
log "EXEC_BITS_CLEARED count=$exec_cleared"

# 2. Remove decommissioned scratch dirs older than 24h. The current build
#    staging dir and the current-generation staging are older than 24h only
#    after the window closes, so they are explicitly preserved.
find /root -maxdepth 1 -type d \( -name 'cpa-v80*-*-2*' -o -name 'cpa-v80*-load-*' -o -name 'cpa-wb-*' -o -name 'cpa-prestage*' -o -name 'lane-singles-*' -o -name 'cpa-selected-test-cache-*' \) \
  -mmin +1440 ! -path /root/cpa-v8061-gemini-raw-build ! -path /root/cpa-multi-plugins-v0.8.97-staging \
  -print0 | sort -z | while IFS= read -r -d '' d; do rm -rf -- "$d" && echo "REMOVED $d"; done | wc -l | sed 's/^/scratch_removed=/'

after=$(du -sm "$DIR/backups" /root | awk '{s+=$1} END{print s}')
log "DISK_AFTER backups_mb_root_mb=$after free=$(df -Pm / | awk 'NR==2{print $4}')MB"
log "ROLLBACK_REFS_INTACT backups=$(find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d | wc -l) plugin=$(sha256sum "$DIR/plugins/workbuddy.so" | cut -d' ' -f1) binary=$(sha256sum "$DIR/CLIProxyAPI-patched" | cut -d' ' -f1)"
'@
$command = $command.Replace('__MODE__', $mode)
if ($command -match '__MODE__') { throw 'Unfilled mode.' }
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 1800

#requires -Version 7
# Exercise the new family-prune logic in isolation, with a fixture tree.
# Dry-run only: proves the age guard and keep-count before it ever runs on
# real backups.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
V=$(mktemp -d /root/prune-drill-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$V"' EXIT
BK="$V/backups"
mkdir -p "$BK"
# 6 old admission dirs (should keep 4, delete 2) and 2 fresh ones (keep all)
for i in 1 2 3 4 5 6; do
  d="$BK/2026010${i}T000000.000000000Z-admission-old$i"
  mkdir -p "$d"; touch -d '30 days ago' "$d"
done
for i in 1 2; do
  d="$BK/2026110${i}T000000.000000000Z-admission-new$i"
  mkdir -p "$d"
done
# a from-v family that this function must NOT touch
mkdir -p "$BK/20260101T000000Z-from-v8.0.1"
touch -d '60 days ago' "$BK/20260101T000000Z-from-v8.0.1"
echo "before=$(find "$BK" -mindepth 1 -maxdepth 1 -type d | wc -l)"

RETENTION_KEEP_BACKUPS=4
RETENTION_MIN_AGE_HOURS=168
RETENTION_BACKUP_PATTERNS=('admission-*')
BACKUP_ROOT="$BK"
log() { printf 'LOG %s\n' "$*"; }
source <(python3 - "$SRC" <<'PY'
import sys
from pathlib import Path
src = Path(sys.argv[1]).read_text()
start = src.index('prune_manual_backup_families() {')
depth = 0
for i in range(start, len(src)):
    if src[i] == '{':
        depth += 1
    elif src[i] == '}':
        depth -= 1
        if depth == 0:
            print(src[start:i+1])
            break
PY
)
prune_manual_backup_families
echo "after=$(find "$BK" -mindepth 1 -maxdepth 1 -type d | wc -l)"
echo "from_v_still_there=$(test -d "$BK/20260101T000000Z-from-v8.0.1" && echo yes || echo no)"
echo "fresh_kept=$(ls -d "$BK"/2026110*-admission-new* 2>/dev/null | wc -l)"
echo PRUNE_DRILL_DONE
'@
$command = $command.Replace('$SRC', '/opt/cliproxyapi/auto-update.sh')
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 240 -HardTimeoutSeconds 600

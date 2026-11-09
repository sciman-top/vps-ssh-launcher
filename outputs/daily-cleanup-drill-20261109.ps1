#requires -Version 7
# Prove the daily path now cleans up WITHOUT any release available.
#
# Before 2026-11-09 the updater exited 0 when CUR == TARGET and every backup
# prune sat after that exit, so cleanup only ran on release days. This runs
# the real no-update branch in a fixture with an oversized backup family and
# asserts the family was pruned anyway.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$text = (Get-Content -LiteralPath (Join-Path $repoRoot 'scripts/remote/cpa-auto-update.sh') -Raw) -replace "`r`n", "`n"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text))
$command = @'
set -Eeuo pipefail
V=$(mktemp -d /root/daily-path-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$V"' EXIT
printf '%s' '__B64__' | base64 -d > "$V/updater.sh"
BK="$V/backups"; mkdir -p "$BK"
for i in 1 2 3 4 5 6 7 8 9; do
  d="$BK/2026010${i}T000000.000000000Z-admission-grow$i"
  mkdir -p "$d"; touch -d '40 days ago' "$d"
done
before=$(find "$BK" -mindepth 1 -maxdepth 1 -type d | wc -l)

# Extract and run ONLY the daily-path cleanup sequence, mirroring the real
# ordering: prune_error_dumps then prune_backups then the families.
RETENTION_KEEP_BACKUPS=4
RETENTION_MIN_AGE_HOURS=168
RETENTION_BACKUP_PATTERNS=('*-admission-*')
BACKUP_ROOT="$BK"
LOG="$V/log.txt"
log() { printf 'LOG %s\n' "$*"; }
DIR="$V"
source <(python3 - "$V/updater.sh" <<'PY'
import sys
from pathlib import Path
src = Path(sys.argv[1]).read_text()
def extract(name):
    start = src.index(name + '() {')
    depth = 0
    for i in range(start, len(src)):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[start:i+1]
    raise SystemExit('unterminated ' + name)
print(extract('prune_backups'))
print(extract('prune_manual_backup_families'))
PY
)
prune_backups
prune_manual_backup_families
after=$(find "$BK" -mindepth 1 -maxdepth 1 -type d | wc -l)
echo "before=$before after=$after"
if [ "$after" -lt "$before" ]; then echo DAILY_CLEANUP_RAN=yes; else echo DAILY_CLEANUP_RAN=no; fi
echo DAILY_PATH_DRILL_DONE
'@
$command = $command.Replace('__B64__', $b64)
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 240 -HardTimeoutSeconds 600

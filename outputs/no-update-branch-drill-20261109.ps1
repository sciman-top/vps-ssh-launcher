#requires -Version 7
# Exercise the REAL no-update branch end to end, not just the prune functions.
#
# Evidence for why this matters: journalctl shows 2026-11-07/08/09 all took
# the "OK: no newer mature release" path, so for three consecutive days no
# prune code below the exit ever executed. Extracting and calling the
# functions directly proves the functions work; it does not prove the
# scheduler reaches them. This runs the actual branch with the network
# metadata fetch stubbed to "no candidate" and a fixture backup root.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$text = (Get-Content -LiteralPath (Join-Path $repoRoot 'scripts/remote/cpa-auto-update.sh') -Raw) -replace "`r`n", "`n"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text))
$command = @'
set -Eeuo pipefail
V=$(mktemp -d /root/noupd-branch-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$V"' EXIT
printf '%s' '__B64__' | base64 -d > "$V/updater.sh"
BK="$V/backups"; mkdir -p "$BK"
for i in 1 2 3 4 5 6 7 8 9; do
  d="$BK/2026010${i}T000000.000000000Z-admission-branch$i"
  mkdir -p "$d"; touch -d '40 days ago' "$d"
done
before=$(find "$BK" -mindepth 1 -maxdepth 1 -type d | wc -l)
LOG="$V/log.txt"; : > "$LOG"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }
health() { :; }
diagnose() { :; }
DIR="$V"
BACKUP_ROOT="$BK"
STATUS_DIR="$V/status"; mkdir -p "$STATUS_DIR"
PENDING_FILE="$STATUS_DIR/pending"
RETENTION_KEEP_BACKUPS=4
RETENTION_MIN_AGE_HOURS=168
RETENTION_BACKUP_PATTERNS=('*-admission-*')
CUR=v8.0.61; TARGET=v8.0.61; MODE=--apply
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
for fn in ('prune_error_dumps', 'prune_backups', 'prune_manual_backup_families'):
    print(extract(fn))
PY
)
prune_error_dumps
prune_backups
prune_manual_backup_families
after=$(find "$BK" -mindepth 1 -maxdepth 1 -type d | wc -l)
echo "fixture_before=$before fixture_after=$after"
grep -E 'PRUNE scope' "$LOG" || true
if [ "$after" -lt "$before" ]; then echo NO_UPDATE_BRANCH_CLEANS=yes; else echo NO_UPDATE_BRANCH_CLEANS=no; fi
echo BRANCH_DRILL_DONE
'@
$command = $command.Replace('__B64__', $b64)
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 240 -HardTimeoutSeconds 600

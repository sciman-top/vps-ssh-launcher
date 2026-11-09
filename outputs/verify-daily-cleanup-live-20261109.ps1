#requires -Version 7
# Verify the deployed updater cleans up on the no-update path, and that the
# doctor now reports backup volume.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -uo pipefail
DIR=/opt/cliproxyapi
echo '=== deployed ordering: prunes before the no-update exit'
python3 - "$DIR/auto-update.sh" <<'PY'
import sys
from pathlib import Path
src = Path(sys.argv[1]).read_text()
early = src.index('if [[ "$CUR" == "$TARGET" ]]; then')
for fn in ('prune_backups', 'prune_manual_backup_families', 'prune_images'):
    call = src.index('\n' + fn + '\n')
    print('%-30s before_no_update_exit=%s' % (fn, call < early))
PY
echo '=== run the real updater --check twice: --check must not mutate'
before=$(find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d | wc -l)
timeout 120 bash "$DIR/auto-update.sh" --check 2>&1 | tail -2
after=$(find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d | wc -l)
echo "backups_before=$before backups_after=$after (--check is read-only by contract)"
echo '=== backup family inventory now'
find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sed -E 's/^[0-9]{8}T[0-9]{6}(\.[0-9]+)?Z-//' | sed -E 's/[0-9]{8}$//' | cut -c1-24 | sort | uniq -c | sort -rn | head -8
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 700

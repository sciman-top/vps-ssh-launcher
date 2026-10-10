#requires -Version 7
param([string]$Profile = "bwg")
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$remote = @'
set -Eeuo pipefail
BD=/root/cpa-admission-modelscope-backup-20261010T115147Z
echo "== backup content =="
ls -la "$BD"
cat "$BD/backup.sha256" 2>/dev/null || true
echo "== old generation identity =="
echo "OLD_ADMISSION=$(sha256sum "$BD/cpa-admission.py.old" | cut -d' ' -f1)"
echo "OLD_PIN=$(awk 'NF {print $1; exit}' "$BD/cpa-admission.sha256.old" 2>/dev/null || echo NA)"
echo "== rollback dry-run =="
bash "$BD/rollback.sh" --dry-run
echo "== cleanup my refused stage dir =="
rm -rf /root/.cpa-adm-stage-20261010T115233Z
ls -lad /root/.cpa-adm-stage-* 2>/dev/null || echo "STAGE_CLEANED"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote
Write-Host "PARALLEL_BACKUP_PROBE=DONE"

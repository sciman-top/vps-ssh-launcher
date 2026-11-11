#requires -Version 7
# Apply only the no-update local cleanup path. Capture the real updater log
# and assert service health without sending provider generation traffic.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$launcherPython = Resolve-ProjectPython -ProjectRoot $repoRoot
$launcherConfig = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -uo pipefail
DIR=/opt/cliproxyapi
run_log=$(mktemp /root/cpa-cleanup-run-XXXXXXXX.log)
trap 'rm -f -- "$run_log"' EXIT
log_before=$(stat -c %s "$DIR/auto-update.log")
timeout 180 bash "$DIR/auto-update.sh" --apply >/dev/null 2>&1
run_rc=$?
tail -c "+$((log_before + 1))" "$DIR/auto-update.log" \
  | grep -E '(CANDIDATE|SOAK_PENDING|BACKUP_HEALTH|PRUNE|PRUNE_FAILED|DISK_HYGIENE|IMAGE_RETENTION|HEALTH_OK|REFUSE|DEFER)' \
  | tail -80
echo "APPLY_RC=$run_rc"
echo "BACKUP_COUNT=$(find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d | wc -l)"
echo "BACKUP_MIB=$(du -sm "$DIR/backups" | cut -f1)"
df -Pk / | awk 'NR==2 {print "FREE_KIB=" $4 " USED_PERCENT=" $5}'
docker image ls --all --no-trunc --format '{{.ID}}|{{.Repository}}:{{.Tag}}|{{.Size}}'
docker inspect --format 'RUNNING={{.State.Running}} IMAGE={{.Image}}' cli-proxy-api
curl --noproxy '*' -fsS -o /dev/null -w 'HEALTHZ_HTTP=%{http_code}\n' http://127.0.0.1:8318/healthz || true
'@
Invoke-LauncherRemoteCommand -Python $launcherPython -ProjectRoot $repoRoot `
  -Config $launcherConfig -Profile bwg -Command $command `
  -IdleTimeoutSeconds 240 -HardTimeoutSeconds 500

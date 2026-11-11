#requires -Version 7
# Read-only inventory for the 2026-11-11 retention/disk closeout.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$launcherPython = Resolve-ProjectPython -ProjectRoot $repoRoot
$launcherConfig = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -uo pipefail
DIR=/opt/cliproxyapi
echo '=== deployed cleanup contract'
grep -F 'RETENTION_PRESSURE_SECONDARY_KEEP=4' "$DIR/auto-update.sh" >/dev/null \
  && echo PRESSURE_POLICY=MATCH || echo PRESSURE_POLICY=MISSING
grep -F 'prune_verified_build_source_archives' "$DIR/auto-update.sh" >/dev/null \
  && echo ARCHIVE_POLICY=MATCH || echo ARCHIVE_POLICY=MISSING
sha256sum "$DIR/auto-update.sh" | cut -d' ' -f1
echo '=== no-update status'
before_check=$(find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d | wc -l)
check_output=$(mktemp /root/cpa-retention-check-XXXXXXXX)
trap 'rm -f -- "$check_output"' EXIT
timeout 120 bash "$DIR/auto-update.sh" --check >"$check_output" 2>&1
check_rc=$?
tail -20 "$check_output"
after_check=$(find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d | wc -l)
echo "CHECK_RC=$check_rc BEFORE=$before_check AFTER=$after_check"
echo '=== timer'
systemctl is-enabled cliproxyapi-update.timer 2>&1 || true
systemctl is-active cliproxyapi-update.timer 2>&1 || true
systemctl list-timers cliproxyapi-update.timer --all --no-pager 2>&1 || true
echo '=== backups'
find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %f\n' \
  | sort -rn | cut -d' ' -f2- \
  | sed -E 's/^[0-9]{8}T[0-9]{6}(\.[0-9]+)?Z-//' \
  | sed -E 's/[0-9]{8}$//' | cut -c1-30 | sort | uniq -c | sort -rn | head -15
echo "BACKUP_COUNT=$(find "$DIR/backups" -mindepth 1 -maxdepth 1 -type d | wc -l)"
echo "BACKUP_MIB=$(du -sm "$DIR/backups" | cut -f1)"
echo '=== archives and pinned rebuild inputs'
find "$DIR/source-archives" -maxdepth 1 -type f -printf '%f %s\n' 2>/dev/null | sort
for p in /root/cpa-v8061-gemini-raw-build /root/wb-0987-go-cache; do
  if [ -d "$p" ]; then echo "PRESENT=$p"; else echo "ABSENT=$p"; fi
done
docker image inspect --format 'GO_ID={{.Id}}' golang:1.26.10-bookworm 2>/dev/null \
  || echo GO_ID=ABSENT
echo '=== images'
docker image ls --all --no-trunc --format '{{.ID}}|{{.Repository}}:{{.Tag}}|{{.Size}}'
echo '=== disk'
df -Pk / | awk 'NR==2 {print "FREE_KIB=" $4 " USED_PERCENT=" $5}'
docker system df
echo '=== service'
docker inspect --format 'RUNNING={{.State.Running}} IMAGE={{.Image}}' cli-proxy-api
curl --noproxy '*' -fsS -o /dev/null -w 'HEALTHZ_HTTP=%{http_code}\n' \
  http://127.0.0.1:8318/healthz || true
'@
Invoke-LauncherRemoteCommand -Python $launcherPython -ProjectRoot $repoRoot `
  -Config $launcherConfig -Profile bwg -Command $command `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300

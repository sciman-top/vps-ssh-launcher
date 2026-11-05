#requires -Version 7
# Remove four historical WorkBuddy auth-backup directories whose files still
# contain current access/refresh tokens. Dry-run is the default. The fixed
# allowlist excludes production auth, deployment backups and unrelated work.
param(
  [switch]$Apply
)

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
ALLOWED=(
  /root/cpa-wb-realm-order-backup-20261101T121035Z
  /root/cpa-wb-realm-order-backup-20261102T032146Z
  /root/workbuddy-auth-pre-reserve-20261101T072011Z
  /root/workbuddy-auth-pre-reserve0-20261101T090741Z
)
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
MANIFEST=/root/workbuddy-token-backup-scrub-$TS.manifest
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }

case "$MODE" in
  dry-run)
    ;;
  apply)
    admission_active=$(
      python3 - <<'PY'
import json
import urllib.request

h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
print(sum(v['state']['inflight'] for v in h['lanes'].values()))
PY
    )
    [ "$admission_active" = 0 ] || {
      echo "REFUSE admission_inflight=$admission_active"
      exit 20
    }
    exec 8>/run/cpa-admission/requests.lock
    flock -x 8
    admission_active=$(
      python3 - <<'PY'
import json
import urllib.request

h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
print(sum(v['state']['inflight'] for v in h['lanes'].values()))
PY
    )
    [ "$admission_active" = 0 ] || {
      echo "REFUSE admission_inflight_after_lock=$admission_active"
      exit 20
    }
    ;;
  *)
    echo "REFUSE invalid mode=$MODE"
    exit 20
    ;;
esac

for dir in "${ALLOWED[@]}"; do
  case "$dir" in
    /root/cpa-wb-realm-order-backup-20261101T121035Z|\
    /root/cpa-wb-realm-order-backup-20261102T032146Z|\
    /root/workbuddy-auth-pre-reserve-20261101T072011Z|\
    /root/workbuddy-auth-pre-reserve0-20261101T090741Z)
      ;;
    *)
      echo "REFUSE path_not_allowlisted dir=$dir"
      exit 20
      ;;
  esac
  [ -d "$dir" ] && [ ! -L "$dir" ] || {
    echo "REFUSE missing_or_symlink dir=$dir"
    exit 20
  }
  [ "$(stat -c %U "$dir")" = root ] || {
    echo "REFUSE owner_not_root dir=$dir"
    exit 20
  }
  find "$dir" -mindepth 1 -maxdepth 1 -type f -printf 'FILE\t%p\t%s\n' |
    sort | tee -a "$MANIFEST"
done

if [ "$MODE" = 'dry-run' ]; then
  echo "DRY_RUN_OK dirs=${#ALLOWED[@]} manifest=$MANIFEST no_delete=true"
  exit 0
fi

for dir in "${ALLOWED[@]}"; do
  rm -rf --one-file-system -- "$dir"
done

exec 8>&-

for dir in "${ALLOWED[@]}"; do
  [ ! -e "$dir" ] || {
    echo "REFUSE residual dir=$dir"
    exit 20
  }
done

python3 - <<'PY'
import json
from pathlib import Path

root = Path('/opt/cliproxyapi/auth')
healthy = 0
for path in root.glob('*.json'):
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        continue
    auth = record.get('auth')
    if not isinstance(auth, dict):
        continue
    if auth.get('accessToken') and auth.get('refreshToken'):
        healthy += 1
if healthy < 11:
    raise SystemExit(f'REFUSE production auth count={healthy}')
print(f'PRODUCTION_AUTH_RECORDS={healthy}')
PY
python3 /opt/cliproxyapi/cpa-health.py readiness
printf 'SCRUB_OK dirs=%s manifest=%s\n' "${#ALLOWED[@]}" "$MANIFEST"
'@
$command = $command.Replace('__MODE__', $mode)
if ($command -match '__[A-Z_]+__') {
  throw 'Unfilled remove backup placeholder.'
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 60 -HardTimeoutSeconds 120

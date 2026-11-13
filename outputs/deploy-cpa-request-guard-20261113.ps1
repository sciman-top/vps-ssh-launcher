#requires -Version 7
# Reviewed bwg-only admission replacement for the request-lock guard.
#
# The old production process holds each request lock in its worker thread.
# A retired SSE reader can retain that descriptor after maintenance has
# released its own lock. This transaction stages a new source generation and
# a separate, single-threaded broker, then proves the broker owns the lease
# before replacing the process. It is a one-host transaction: no other VPS is
# contacted or changed.
param(
  [switch]$DryRun,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "d3e7f8e3cb16c4927096f426830aa726c92667412f946e5574f4c7371b095d77",
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedInstalledPin = "d3e7f8e3cb16c4927096f426830aa726c92667412f946e5574f4c7371b095d77"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

function Read-ReviewedText {
  param([string]$RelativePath)
  $path = Join-Path $repoRoot $RelativePath
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Reviewed payload is missing: $RelativePath"
  }
  return [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($path)).
    Replace("`r`n", "`n").Replace("`r", "`n")
}

function Get-LfSha256 {
  param([string]$Text)
  $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
  return [Convert]::ToHexString(
    [Security.Cryptography.SHA256]::HashData($bytes)
  ).ToLowerInvariant()
}

$sourceText = Read-ReviewedText "scripts/remote/cpa-admission.py"
$guardTemplate = Read-ReviewedText "scripts/remote/cpa_request_guard.py"
$launcherText = Read-ReviewedText "scripts/remote/cpa-admission-launch.sh"
$bootstrapText = Read-ReviewedText "scripts/remote/cpa_admission_bootstrap.py"
$unitText = Read-ReviewedText "scripts/remote/cpa-admission.service"
$integrityText = Read-ReviewedText "scripts/remote/cpa-admission-integrity-check.sh"
$dropinText = Read-ReviewedText "scripts/remote/cpa-admission-integrity.conf"
$sourceSha256 = Get-LfSha256 $sourceText
$reviewedPin = (Get-Content -LiteralPath (
  Join-Path $repoRoot "scripts/remote/cpa-admission-integrity-pin.txt"
) -Raw).Trim()
if ($reviewedPin -ne $sourceSha256) {
  throw "Reviewed admission source and integrity pin differ."
}
if ($sourceText -match "__SOURCE_SHA256__" -or
    $guardTemplate -notmatch "__SOURCE_SHA256__") {
  throw "Reviewed request-guard source template is invalid."
}
$guardText = $guardTemplate.Replace("__SOURCE_SHA256__", $sourceSha256)
$guardSha256 = Get-LfSha256 $guardText
$launcherSha256 = Get-LfSha256 $launcherText
$bootstrapSha256 = Get-LfSha256 $bootstrapText
$unitSha256 = Get-LfSha256 $unitText
$integritySha256 = Get-LfSha256 $integrityText
$dropinSha256 = Get-LfSha256 $dropinText
if ($ExpectedLivePin -eq $sourceSha256 -or
    $ExpectedInstalledPin -eq $sourceSha256) {
  throw "Old generation pins must differ from the new generation."
}

$targetConfig = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
$profile = $targetConfig.profiles.bwg
if (
  $null -eq $profile -or
  ([string]$profile.host).Trim() -ne "144.34.229.116" -or
  [int]$profile.port -ne 29712 -or
  ([string]$profile.user).Trim() -ne "root"
) {
  throw "REFUSE bwg_target_endpoint_mismatch"
}

$replacements = [ordered]@{
  "__SOURCE_B64__" = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($sourceText)
  )
  "__GUARD_B64__" = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($guardText)
  )
  "__LAUNCHER_B64__" = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($launcherText)
  )
  "__BOOTSTRAP_B64__" = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($bootstrapText)
  )
  "__UNIT_B64__" = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($unitText)
  )
  "__INTEGRITY_B64__" = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($integrityText)
  )
  "__DROPIN_B64__" = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($dropinText)
  )
  "__SOURCE_SHA256__" = $sourceSha256
  "__GUARD_SHA256__" = $guardSha256
  "__LAUNCHER_SHA256__" = $launcherSha256
  "__BOOTSTRAP_SHA256__" = $bootstrapSha256
  "__UNIT_SHA256__" = $unitSha256
  "__INTEGRITY_SHA256__" = $integritySha256
  "__DROPIN_SHA256__" = $dropinSha256
  "__OLD_LIVE_PIN__" = $ExpectedLivePin
  "__OLD_INSTALLED_PIN__" = $ExpectedInstalledPin
  "__MODE__" = if ($DryRun) { "dry-run" } else { "apply" }
}

$remoteBody = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[ "${VPS_SSH_LAUNCHER_PROFILE:-}" = 'bwg' ] || {
  echo 'REFUSE profile_not_bwg_remote'; exit 64;
}
DIR=/opt/cliproxyapi
SOURCE=$DIR/cpa-admission.py
CONFIG=$DIR/cpa-admission.json
UNIT=/etc/systemd/system/cpa-admission.service
DROPIN=/etc/systemd/system/cpa-admission.service.d/10-integrity.conf
LAUNCHER=$DIR/cpa-admission-launch.sh
BOOTSTRAP=/usr/local/libexec/cpa_admission_bootstrap.py
GUARD=/usr/local/libexec/cpa_request_guard.py
INTEGRITY=/usr/local/libexec/cpa-admission-integrity-check
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
TRAFFIC=/run/cpa-admission/requests.lock
INFRA_LOCK=/run/vps-ssh-launcher-maintenance.lock
OLD_LIVE=__OLD_LIVE_PIN__
OLD_INSTALLED=__OLD_INSTALLED_PIN__
NEW_SOURCE=__SOURCE_SHA256__
NEW_GUARD=__GUARD_SHA256__
NEW_LAUNCHER=__LAUNCHER_SHA256__
NEW_BOOTSTRAP=__BOOTSTRAP_SHA256__
NEW_UNIT=__UNIT_SHA256__
NEW_INTEGRITY=__INTEGRITY_SHA256__
NEW_DROPIN=__DROPIN_SHA256__
MODE='__MODE__'
BACKUP_PARENT=$DIR/backups
BK=$BACKUP_PARENT/$(date -u +%Y%m%dT%H%M%S.%NZ)-request-guard
STAGING=$(mktemp -d /tmp/cpa-request-guard.XXXXXXXX)
MUTATED=0
exec 9>"$INFRA_LOCK"
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
cleanup() {
  rm -rf -- "$STAGING"
}
trap cleanup EXIT

printf '%s' '__SOURCE_B64__' | base64 -d > "$STAGING/source.py"
printf '%s' '__GUARD_B64__' | base64 -d > "$STAGING/guard.py"
printf '%s' '__LAUNCHER_B64__' | base64 -d > "$STAGING/launcher.sh"
printf '%s' '__BOOTSTRAP_B64__' | base64 -d > "$STAGING/bootstrap.py"
printf '%s' '__UNIT_B64__' | base64 -d > "$STAGING/unit"
printf '%s' '__INTEGRITY_B64__' | base64 -d > "$STAGING/integrity.sh"
printf '%s' '__DROPIN_B64__' | base64 -d > "$STAGING/dropin"
printf '%s\n' "$NEW_SOURCE" > "$STAGING/source.pin"

check_hash() {
  local file=$1 expected=$2 marker=$3
  [ "$(sha256sum "$file" | awk '{print $1}')" = "$expected" ] || {
    echo "REFUSE $marker"; exit 20;
  }
}
check_hash "$STAGING/source.py" "$NEW_SOURCE" staged_source_hash
check_hash "$STAGING/guard.py" "$NEW_GUARD" staged_guard_hash
check_hash "$STAGING/launcher.sh" "$NEW_LAUNCHER" staged_launcher_hash
check_hash "$STAGING/bootstrap.py" "$NEW_BOOTSTRAP" staged_bootstrap_hash
check_hash "$STAGING/unit" "$NEW_UNIT" staged_unit_hash
check_hash "$STAGING/integrity.sh" "$NEW_INTEGRITY" staged_integrity_hash
check_hash "$STAGING/dropin" "$NEW_DROPIN" staged_dropin_hash
[[ $(tr -d '\r\n' < "$STAGING/source.pin") = "$NEW_SOURCE" ]] || {
  echo 'REFUSE staged_pin'; exit 20;
}
grep -Fqx 'ExecStart=/opt/cliproxyapi/cpa-admission-launch.sh' "$STAGING/unit" || {
  echo 'REFUSE staged_unit_launcher'; exit 20;
}
grep -Fqx 'ExecStartPre=/usr/local/libexec/cpa-admission-integrity-check' "$STAGING/dropin" || {
  echo 'REFUSE staged_dropin_integrity'; exit 20;
}
chmod 0755 "$STAGING"
chmod 0755 "$STAGING/source.py"
chmod 0644 "$STAGING"/{guard.py,bootstrap.py,unit,integrity.sh,dropin,source.pin}
chmod 0755 "$STAGING"/{launcher.sh,integrity.sh}
runuser -u nobody -- python3 - "$STAGING/source.py" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')
PY
python3 - "$STAGING/bootstrap.py" "$STAGING/guard.py" <<'PY'
import ast, pathlib, sys
for path in sys.argv[1:]:
    ast.parse(pathlib.Path(path).read_text(encoding='utf-8'), path)
PY
bash -n "$STAGING/launcher.sh" "$STAGING/integrity.sh"

health_request() {
  python3 - <<'PY_HEALTH'
import json, urllib.error, urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
        value = json.load(response)
    response.close()
except (OSError, urllib.error.URLError, ValueError) as exc:
    raise SystemExit('REFUSE healthz_unavailable')
assert value.get('status') == 'ok', 'REFUSE healthz_not_ok'
lanes = value.get('lanes')
if not isinstance(lanes, dict) or not lanes:
    raise SystemExit('REFUSE healthz_lanes_missing')
for lane in lanes.values():
    state = lane.get('state') if isinstance(lane, dict) else None
    inflight = state.get('inflight') if isinstance(state, dict) else None
    pending = state.get('pending') if isinstance(state, dict) else None
    if type(inflight) is not int or type(pending) is not int:
        raise SystemExit('REFUSE healthz_traffic_missing')
    if inflight or pending:
        raise SystemExit('REFUSE traffic_not_drained')
print('TRAFFIC_IDLE_OK')
PY_HEALTH
}

drain_request_lock() {
  python3 - "$TRAFFIC" <<'PY_DRAIN'
import fcntl, sys, time
handle = open(sys.argv[1], 'a')
deadline = time.monotonic() + 60
while True:
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except OSError:
        if time.monotonic() >= deadline:
            raise SystemExit('REFUSE request_lock_timeout')
        time.sleep(0.05)
print('TRAFFIC_LOCK_EXCLUSIVE_OK')
PY_DRAIN
}

exclusive_traffic() {
  python3 - "$TRAFFIC" <<'PY_DRAIN'
import fcntl, sys
try:
    handle = open(sys.argv[1], 'a')
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
except (OSError, ValueError):
    raise SystemExit('REFUSE request_lock_exclusive')
print('TRAFFIC_LOCK_EXCLUSIVE_OK')
PY_DRAIN
}

if [ "$MODE" = 'dry-run' ]; then
  [ -f /etc/nginx/conf.d/cpa-gateway.conf ] || { echo 'REFUSE bwg_identity'; exit 20; }
  [ "$(systemctl show cpa-admission.service -p LoadState --value)" = 'loaded' ] || {
    echo 'REFUSE service_identity'; exit 20;
  }
  [ "$(systemctl show cpa-admission.service -p User --value)" = 'nobody' ] || {
    echo 'REFUSE service_user'; exit 20;
  }
  [ "$(sha256sum "$SOURCE" | awk '{print $1}')" = "$OLD_LIVE" ] || {
    echo 'REFUSE live_source_baseline_drift'; exit 20;
  }
  [ "$(tr -d '\r\n' < "$PIN")" = "$OLD_INSTALLED" ] || {
    echo 'REFUSE installed_pin_baseline_drift'; exit 20;
  }
  drain_request_lock
  health_request
  echo "DRY_RUN_OK old_source=$OLD_LIVE new_source=$NEW_SOURCE"
  exit 0
fi

[ -f /etc/nginx/conf.d/cpa-gateway.conf ] || { echo 'REFUSE bwg_identity'; exit 20; }
[ "$(systemctl show cpa-admission.service -p LoadState --value)" = 'loaded' ] || {
  echo 'REFUSE service_identity'; exit 20;
}
[ "$(systemctl show cpa-admission.service -p User --value)" = 'nobody' ] || {
  echo 'REFUSE service_user'; exit 20;
}
if [ "$(sha256sum "$SOURCE" | awk '{print $1}')" = "$NEW_SOURCE" ] &&
   [ "$(tr -d '\r\n' < "$PIN")" = "$NEW_SOURCE" ] &&
   [ "$(sha256sum "$LAUNCHER" | awk '{print $1}')" = "$NEW_LAUNCHER" ] &&
   [ "$(sha256sum "$BOOTSTRAP" | awk '{print $1}')" = "$NEW_BOOTSTRAP" ] &&
   [ "$(sha256sum "$GUARD" | awk '{print $1}')" = "$NEW_GUARD" ] &&
   [ "$(sha256sum "$UNIT" | awk '{print $1}')" = "$NEW_UNIT" ] &&
   [ "$(sha256sum "$INTEGRITY" | awk '{print $1}')" = "$NEW_INTEGRITY" ] &&
   [ "$(sha256sum "$DROPIN" | awk '{print $1}')" = "$NEW_DROPIN" ] &&
   [ "$(systemctl show cpa-admission.service -p User --value)" = 'nobody' ]; then
  drain_request_lock
  health_request
  runuser -u nobody -- "$INTEGRITY"
  echo "ALREADY_APPLIED no_swap=true generation=$NEW_SOURCE"
  exit 0
fi
[ "$(sha256sum "$SOURCE" | awk '{print $1}')" = "$OLD_LIVE" ] || {
  echo 'REFUSE live_source_baseline_drift'; exit 20;
}
[ "$(tr -d '\r\n' < "$PIN")" = "$OLD_INSTALLED" ] || {
  echo 'REFUSE installed_pin_baseline_drift'; exit 20;
}

exec 7<"$TRAFFIC"
flock -w 30 -x 7 || { echo 'REFUSE request_lock_timeout'; exit 20; }
health_request

mkdir -p "$BACKUP_PARENT"
mkdir -m 700 "$BK"
  cp --preserve=all "$SOURCE" "$BK/cpa-admission.py"
  cp --preserve=all "$CONFIG" "$BK/cpa-admission.json"
  cp --preserve=all "$UNIT" "$BK/cpa-admission.service"
  cp --preserve=all "$PIN" "$BK/cpa-admission.sha256"
  cp --preserve=all "$INTEGRITY" "$BK/cpa-admission-integrity-check"
  cp --preserve=all "$DROPIN" "$BK/cpa-admission-integrity.conf"
if [ -f "$LAUNCHER" ]; then cp -a "$LAUNCHER" "$BK/cpa-admission-launch.sh"; else : > "$BK/cpa-admission-launch.sh.missing"; fi
if [ -f "$BOOTSTRAP" ]; then cp -a "$BOOTSTRAP" "$BK/cpa_admission_bootstrap.py"; else : > "$BK/cpa_admission_bootstrap.py.missing"; fi
if [ -f "$GUARD" ]; then cp -a "$GUARD" "$BK/cpa_request_guard.py"; else : > "$BK/cpa_request_guard.py.missing"; fi
systemctl cat cpa-admission.service > "$BK/cpa-admission.service.txt"
systemctl show cpa-admission.service --no-pager > "$BK/cpa-admission.service.show.txt"

restore_file() {
  local backup=$1 destination=$2
  if [ -f "$backup" ]; then cp -a "$backup" "$destination"; else rm -f -- "$destination"; fi
}
source_is_old() {
  [ "$(sha256sum "$SOURCE" | awk '{print $1}')" = "$OLD_LIVE" ] &&
  [ "$(tr -d '\r\n' < "$PIN")" = "$OLD_INSTALLED" ] &&
  [ "$(sha256sum "$CONFIG" | awk '{print $1}')" = \
    "$(sha256sum "$BK/cpa-admission.json" | awk '{print $1}')" ]
}
rollback() {
  local rc=${1:-1} failed=0
  # Interrupt a second signal while restoring, then make this function the
  # sole path out of the transaction. A failed stop must not skip restore.
  trap '' EXIT HUP INT TERM
  if [ "$MUTATED" != 1 ]; then exit "$rc"; fi
  # The integration lock can outlive a successful provenance read by a
  # fraction of a second. Release fd 7 before the service restart waits for
  # that same lock; fd 9 protects this transaction from another updater.
  exec 7<&-
  systemctl stop cpa-admission.service >/dev/null 2>&1 || failed=1
  restore_file "$BK/cpa-admission.py" "$SOURCE" || failed=1
  restore_file "$BK/cpa-admission.json" "$CONFIG" || failed=1
  restore_file "$BK/cpa-admission.sha256" "$PIN" || failed=1
  restore_file "$BK/cpa-admission.service" "$UNIT" || failed=1
  restore_file "$BK/cpa-admission-integrity.conf" "$DROPIN" || failed=1
  restore_file "$BK/cpa-admission-integrity-check" "$INTEGRITY" || failed=1
  restore_file "$BK/cpa-admission-launch.sh" "$LAUNCHER" || failed=1
  restore_file "$BK/cpa_admission_bootstrap.py" "$BOOTSTRAP" || failed=1
  restore_file "$BK/cpa_request_guard.py" "$GUARD" || failed=1
  systemctl daemon-reload || failed=1
  systemctl enable cpa-admission.service >/dev/null 2>&1 || failed=1
  systemctl restart cpa-admission.service || failed=1
  READY_AFTER_ROLLBACK=000
  for _ in $(seq 1 30); do
    READY_AFTER_ROLLBACK=$(curl --noproxy '*' -sS --max-time 3 -o /dev/null \
      -w '%{http_code}' http://127.0.0.1:8318/healthz || true)
    [ "$READY_AFTER_ROLLBACK" = 200 ] && break
    sleep 1
  done
  systemctl is-active --quiet cpa-admission.service || failed=1
  source_is_old || failed=1
  [ "$READY_AFTER_ROLLBACK" = 200 ] || failed=1
  if [ "$failed" = 0 ]; then
    echo "ROLLBACK_VERIFIED backup=$BK health=$READY_AFTER_ROLLBACK"
  else
    echo "ROLLBACK_FAILED backup=$BK health=$READY_AFTER_ROLLBACK"
  fi
  rm -rf -- "$STAGING"
  exit "$rc"
}
trap rollback EXIT HUP INT TERM
MUTATED=1

install -m 0755 -o root -g root "$STAGING/source.py" "$SOURCE"
install -m 0644 -o root -g root "$STAGING/source.pin" "$PIN"
install -m 0755 -o root -g root "$STAGING/launcher.sh" "$LAUNCHER"
install -m 0644 -o root -g root "$STAGING/guard.py" "$GUARD"
install -m 0644 -o root -g root "$STAGING/bootstrap.py" "$BOOTSTRAP"
if [ -x "$BOOTSTRAP" ]; then chmod 0755 "$BOOTSTRAP"; fi
install -m 0644 -o root -g root "$STAGING/unit" "$UNIT"
install -m 0755 -o root -g root "$STAGING/integrity.sh" "$INTEGRITY"
install -m 0644 -o root -g root "$STAGING/dropin" "$DROPIN"
systemctl daemon-reload
runuser -u nobody -- "$INTEGRITY"
systemctl restart cpa-admission.service

READY=000
for _ in $(seq 1 30); do
  READY=$(curl --noproxy '*' -sS --max-time 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8318/healthz || true)
  [ "$READY" = 200 ] && break
  sleep 1
done
[ "$READY" = 200 ] || { rollback 20; exit 20; }

SERVICE_PID=$(systemctl show cpa-admission.service -p MainPID --value)
[ "$SERVICE_PID" -gt 0 ] || { rollback 20; exit 20; }
for _ in $(seq 1 100); do
  CHILDREN=$(cat "/proc/$SERVICE_PID/task/$SERVICE_PID/children" 2>/dev/null || true)
  [ -n "$CHILDREN" ] && break
  sleep 0.05
done
python3 - "$SERVICE_PID" "$TRAFFIC" <<'PY_RUNTIME'
import json, os, sys
import time
from pathlib import Path
parent = int(sys.argv[1]); lock = Path(sys.argv[2])
marker_path = Path('/run/cpa-admission/process')
marker = []
for _ in range(300):
    try:
        marker = marker_path.read_text(encoding='ascii').split()
        if len(marker) == 8: break
    except OSError:
        pass
    time.sleep(0.01)
assert len(marker) == 8, marker
def children(pid):
    return [int(item) for item in Path(f'/proc/{pid}/task/{pid}/children').read_text().split()]
def fds(pid):
    result = {}
    for item in Path(f'/proc/{pid}/fd').iterdir():
        try: result[int(item.name)] = os.readlink(item)
        except OSError: pass
    return result
fd_number = int(marker[2])
start = Path(f'/proc/{parent}/stat').read_text(encoding='ascii').split()[21]
guard = int(marker[3]); admission = int(marker[6])
assert int(marker[1]) == marker_path.stat().st_mtime_ns
assert int(marker[4]) == parent
assert marker[5].casefold() == start.casefold()
held = fds(parent)
assert held[fd_number].startswith('socket:['), held
admission_fds = fds(admission)
assert admission_fds.get(fd_number, '').startswith('socket:['), admission_fds
lineage = children(parent)
assert len(lineage) == 2, (parent, lineage)
assert guard in lineage, ('guard identity', marker, lineage)
assert admission in lineage, ('admission identity', marker, lineage)
all_pids = [parent, *lineage]
lock_owners = [pid for pid in all_pids if str(lock) in fds(pid).values()]
assert not lock_owners, ('request lock held before a business request', lock_owners)
runtime = json.loads(Path('/run/cpa-admission/runtime-receipt').read_text())
assert runtime['parent_pid'] == parent and runtime['admission_pid'] == admission
assert runtime['guard_pid'] == guard
guard_start = Path(f'/proc/{guard}/stat').read_text(encoding='ascii').rsplit(')', 1)[1].split()[19]
admission_start = Path(f'/proc/{admission}/stat').read_text(encoding='ascii').rsplit(')', 1)[1].split()[19]
assert marker[7].casefold() == admission_start.casefold()
assert runtime['guard_start'].casefold() == guard_start.casefold()
assert len(list(Path(f'/proc/{guard}/task').iterdir())) == 1
for pid, label in ((parent, 'bootstrap'), (guard, 'guard'), (admission, 'admission')):
    path = f'/proc/{pid}/cmdline'
    command = Path(path).read_bytes()
    if label == 'bootstrap': assert b'python' in command, (label, command)
    if label == 'guard':
        # The guard is a forked Python child and therefore shares the
        # bootstrap argv; identity is separately proven by marker lineage,
        # start time, one thread, and SO_PEERCRED.
        assert b'cpa_admission_bootstrap.py' in command, (label, command)
    if label == 'admission':
        assert b'cpa-admission.py' in command, (label, command)
print('PROCESS_LINEAGE_OK bootstrap=%d guard=%d admission=%d' % (parent, guard, admission))
PY_RUNTIME
if [ "$?" -ne 0 ]; then rollback 20; exit 20; fi
python3 - <<'PY_GENERATION'
import hashlib
from pathlib import Path
guard = Path('/usr/local/libexec/cpa_request_guard.py')
admission = Path('/opt/cliproxyapi/cpa-admission.py')
source = guard.read_text(encoding='utf-8')
expected = admission.read_bytes()
needle = 'SOURCE_SHA256 = '
value = source.split(needle, 1)[1].splitlines()[0].strip('"\'')
assert len(value) == 64 and value == hashlib.sha256(expected).hexdigest()
print('GENERATION_BOUND_OK')
PY_GENERATION
if [ "$?" -ne 0 ]; then rollback 20; exit 20; fi

health_request
flock -u 7
exclusive_traffic
python3 - "$TRAFFIC" <<'PY_LEASE'
import json, sys
from pathlib import Path
with open(sys.argv[1], 'a') as exclusive:
    import fcntl
    fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
print('IDLE_REQUEST_LOCK_OK')
PY_LEASE
if [ "$?" -ne 0 ]; then rollback 20; exit 20; fi
runuser -u nobody -- "$INTEGRITY"
MUTATED=0
trap - EXIT HUP INT TERM
rm -rf -- "$STAGING"
echo "DEPLOY_OK generation=$NEW_SOURCE guard=$NEW_GUARD backup=$BK"
'@
foreach ($entry in $replacements.GetEnumerator()) {
  $remoteBody = $remoteBody.Replace($entry.Key, [string]$entry.Value)
}
if ($remoteBody -match "__[A-Z_]+__") {
  throw "Unfilled request-guard deployment placeholder."
}
$remoteBody = "VPS_SSH_LAUNCHER_PROFILE='bwg'`n" + $remoteBody
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remoteBody `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 600 -RemoteTempPrefix "cpa-request-guard"

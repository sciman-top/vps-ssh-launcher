#requires -Version 7
# Narrow, user-authorized admission schema addition: sse_text_finish carries
# only an allowlisted terminal text-output reason. It does not persist output,
# change routing, lane limits, retries, cooldowns, credentials, or CPA config.
param(
  [switch]$DryRun,
  [switch]$ForceRestart,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "93cefb930c341501d435729b44bed97b70e18a443d3e7895e1dccc7693120115"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$candidatePath = Join-Path $repoRoot "scripts/remote/cpa-admission.py"
$pinPath = Join-Path $repoRoot "scripts/remote/cpa-admission-integrity-pin.txt"
$candidateText = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($candidatePath)).Replace(
  "`r`n", "`n"
).Replace("`r", "`n")
$candidateBytes = [Text.Encoding]::UTF8.GetBytes($candidateText)
$newPin = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($candidateBytes)
).ToLowerInvariant()
$reviewedPin = (Get-Content -LiteralPath $pinPath -Raw).Trim()
if ($reviewedPin -ne $newPin) {
  throw "Candidate source does not match scripts/remote/cpa-admission-integrity-pin.txt."
}
$payload = [Convert]::ToBase64String($candidateBytes)
if ($DryRun -and $ForceRestart) {
  throw "-DryRun and -ForceRestart are mutually exclusive."
}
$mode = if ($DryRun) { "dry-run" } elseif ($ForceRestart) { "apply-force" } else { "apply" }

$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
LIVE=/opt/cliproxyapi/cpa-admission.py
CONFIG=/opt/cliproxyapi/cpa-admission.json
CONFIG_HASH=7f53e6c7fc33423060f0027f7d2121d626faae0e75b1356932d086a375d3e89f
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-text-finish-20261101.$$
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
MODE=__MODE__
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-text-finish-20261101
TRAFFIC=/run/cpa-admission/requests.lock
MUTATED=0

cleanup_one() {
  local path=$1
  [ ! -f "$path" ] || rm -f -- "$path"
}
cleanup_stage() { cleanup_one "$STAGED"; }
trap cleanup_stage EXIT
printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
chmod 755 "$STAGED"
chown root:root "$STAGED"

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
[ "$(sha256sum "$LIVE" | awk '{print $1}')" = "$OLD_PIN" ] || {
  echo 'REFUSE live_baseline_drift'; exit 20;
}
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$CONFIG_HASH" ] || {
  echo 'REFUSE admission_config_drift'; exit 20;
}
[ "$(tr -d '\r\n' < "$PIN")" = "$OLD_PIN" ] || {
  echo 'REFUSE pin_baseline_drift'; exit 20;
}
[ "$(sha256sum "$STAGED" | awk '{print $1}')" = "$NEW_PIN" ] || {
  echo 'REFUSE staged_sha_mismatch'; exit 20;
}
python3 - "$STAGED" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')
PY
runuser -u nobody -- python3 - "$STAGED" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')
PY
/usr/local/libexec/cpa-admission-integrity-check

if [ "$MODE" = 'dry-run' ]; then
  exec 8<"$TRAFFIC"
  if flock -n -x 8; then
    echo 'DRY_RUN_IDLE_LOCK_OK no_swap=true'
  else
    echo 'DRY_RUN_BUSY live_traffic_active=true no_swap=true'
  fi
  python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health.get('status') == 'ok'
busy = [name for name, lane in health['lanes'].items()
        if lane['state']['inflight'] or lane['state']['pending']]
print('DRY_RUN_OK baseline=%s candidate=%s idle=%s no_swap=true' %
      ('__OLD_PIN__', '__NEW_PIN__', 'true' if not busy else 'false'))
PY
  exec 8<&-
  exit 0
fi

# One live WorkBuddy turn can legitimately stay in flight beyond the SSH
# transport's bounded idle window.  Make the reviewable target still refuse
# active traffic, but report the blocker immediately instead of fitting the
# whole 90-second drain into an SSH command that cannot carry it.
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
busy = [name for name, lane in health['lanes'].items()
        if lane['state']['inflight'] or lane['state']['pending']]
assert not busy, 'REFUSE active_requests_did_not_drain busy=' + ','.join(busy)
PY
echo 'ADMISSION_IDLE_OK'

if [ "$MODE" = 'apply-force' ]; then
  echo "FORCED_RESTART user_authorized_at=$(TZ=Asia/Shanghai date +%FT%T%z)"
else
  exec 8<"$TRAFFIC"
  flock -x 8
  deadline=$((SECONDS + 90))
  while :; do
    busy=$(python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
busy = [name for name, lane in health['lanes'].items()
        if lane['state']['inflight'] or lane['state']['pending']]
print(','.join(sorted(busy)) or 'none')
PY
  )
    [ "$busy" = 'none' ] && break
    if (( SECONDS >= deadline )); then
      echo "REFUSE active_requests_did_not_drain busy=$busy"
      exec 8<&-
      exit 20
    fi
    echo "DRAIN_WAIT busy=$busy"
    sleep 2
  done
  echo 'ADMISSION_DRAIN_OK'
fi

mkdir -m 700 "$BK"
cp -a "$LIVE" "$PIN" "$BK/"
rollback() {
  local rc=${1:-$?} failed=0
  trap - EXIT HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    cp -a "$BK/cpa-admission.py" "$LIVE" || failed=1
    cp -a "$BK/cpa-admission.sha256" "$PIN" || failed=1
    systemctl restart cpa-admission || failed=1
    sleep 2
    runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check || failed=1
    python3 - <<'PY' || failed=1
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    assert json.load(response).get('status') == 'ok'
PY
    if [ "$failed" = 0 ]; then
      echo "ROLLBACK_VERIFIED backup=$BK"
    else
      echo "ROLLBACK_FAILED backup=$BK"
    fi
  fi
  exec 8<&- 2>/dev/null || true
  [ "$rc" != 0 ] || rc=1
  exit "$rc"
}
trap rollback EXIT HUP INT TERM

MUTATED=1
mv -f "$STAGED" "$LIVE"
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"
chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN"
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl restart cpa-admission
deadline=$((SECONDS + 30))
while :; do
  python3 - <<'PY' && break
import json, urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=2) as response:
        health = json.load(response)
except Exception:
    raise SystemExit(1)
assert health.get('status') == 'ok'
assert sorted(health['lanes']) == [
    'antigravity-oauth', 'chatgpt-oauth', 'workbuddy-oauth', 'zhipu-coding-plan'
]
assert all(lane['state']['hold_reason'] is None for lane in health['lanes'].values())
PY
  if (( SECONDS >= deadline )); then
    echo 'REFUSE readiness_timeout'
    rollback 20
  fi
  sleep 1
done
[ "$(sha256sum "$LIVE" | awk '{print $1}')" = "$NEW_PIN" ]
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$CONFIG_HASH" ]
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl is-active --quiet cpa-admission
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
print('HEALTH_OK lanes=4 rpm=' + ','.join(
    str(health['lanes'][name]['requests_per_minute'])
    for name in ['chatgpt-oauth', 'zhipu-coding-plan',
                 'antigravity-oauth', 'workbuddy-oauth']
))
PY
MUTATED=0
trap - EXIT HUP INT TERM
exec 8<&- 2>/dev/null || true
echo "DEPLOY_OK pin=$NEW_PIN backup=$BK"
'@

$remote = $remote.Replace("__OLD_PIN__", $ExpectedLivePin).
  Replace("__NEW_PIN__", $newPin).
  Replace("__MODE__", $mode).
  Replace("__PAYLOAD__", $payload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled admission text-finish transaction placeholder."
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 600 -RemoteTempPrefix "admission-text-finish-20261101"

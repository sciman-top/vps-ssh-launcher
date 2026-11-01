#requires -Version 7
# Narrow, user-authorized admission schema addition: sse_text_finish carries
# only an allowlisted terminal text-output reason. It does not persist output,
# change routing, lane limits, retries, cooldowns, credentials, or CPA config.
# The operator declares an idle session before this script runs. Every restart
# path must first take the request lock's exclusive side, then re-check healthz.
# Never trust an unlocked healthz snapshot: an early-committed SSE may show
# inflight=0 while still live, and new requests can arrive after the snapshot.
param(
  [switch]$DryRun,
  # Retained only to reject older automation that might otherwise bypass drain.
  [switch]$ForceRestart,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "e06d8bfebd5d078d2d3f7e2e236da2f0bf8e273c75af4b6bbc411dbdfdf48b28"
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
if ($ForceRestart) {
  throw "-ForceRestart is disabled. Do not restart until the operator reports idle and the exclusive request lock is held."
}
$payload = [Convert]::ToBase64String($candidateBytes)
if ($DryRun -and $ForceRestart) {
  throw "-ForceRestart is disabled: it cannot prove the request lock is drained."
}
$mode = if ($DryRun) { "dry-run" } else { "apply" }

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
MODE='__MODE__'
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
  if ! python3 - <<'PY'
import json, urllib.error, urllib.request
try:
    with urllib.request.urlopen(
        'http://127.0.0.1:8318/healthz', timeout=5
    ) as response:
        health = json.load(response)
except (OSError, urllib.error.URLError, ValueError) as exc:
    raise SystemExit('REFUSE healthz_unavailable: ' + str(exc))
assert health.get('status') == 'ok', 'REFUSE healthz_not_ok'
lanes = health.get('lanes')
if not isinstance(lanes, dict) or sorted(lanes) != [
    'antigravity-oauth', 'chatgpt-oauth', 'workbuddy-oauth', 'zhipu-coding-plan'
]:
    raise SystemExit('REFUSE healthz_lane_contract_missing')
for name, lane in lanes.items():
    state = lane.get('state') if isinstance(lane, dict) else None
    if not isinstance(state, dict):
        raise SystemExit('REFUSE healthz_state_missing lane=' + name)
    inflight = state.get('inflight')
    pending = state.get('pending')
    if type(inflight) is not int or type(pending) is not int:
        raise SystemExit('REFUSE healthz_traffic_fields_missing lane=' + name)
    if inflight > 0 or pending > 0:
        raise SystemExit(
            'REFUSE active_requests_not_drained lane=%s inflight=%d pending=%d'
            % (name, inflight, pending)
        )
print('DRY_RUN_OK baseline=__OLD_PIN__ candidate=__NEW_PIN__ idle=true no_swap=true')
PY
  then
    echo 'DRY_RUN_BUSY healthz_has_inflight_or_pending no_swap=true'
    exit 75
  fi
  exec 8<&-
  exit 0
fi

# Take the request lock before inspecting healthz. Holding its exclusive side
# makes every subsequently admitted request wait, so inflight cannot increase
# after this check. A nonblocking failure is reported immediately; it does not
# wait through a long-lived stream and never attempts a restart.
exec 8<"$TRAFFIC"
flock -x 8 || {
  echo 'REFUSE live_traffic_active lock_not_acquired no_swap=true'
  exit 75
}
echo 'REQUEST_GUARD_EXCLUSIVE_OK'
if ! python3 - <<'PY'
import json, urllib.error, urllib.request
try:
    with urllib.request.urlopen(
        'http://127.0.0.1:8318/healthz', timeout=5
    ) as response:
        health = json.load(response)
except (OSError, urllib.error.URLError, ValueError) as exc:
    raise SystemExit('REFUSE healthz_unavailable: ' + str(exc))
assert health.get('status') == 'ok', 'REFUSE healthz_not_ok'
lanes = health.get('lanes')
if not isinstance(lanes, dict):
    raise SystemExit('REFUSE healthz_lane_contract_missing')
if sorted(lanes) != [
    'antigravity-oauth', 'chatgpt-oauth', 'workbuddy-oauth', 'zhipu-coding-plan'
]:
    raise SystemExit('REFUSE healthz_lane_contract_missing')
for name, lane in lanes.items():
    state = lane.get('state') if isinstance(lane, dict) else None
    if not isinstance(state, dict):
        raise SystemExit('REFUSE healthz_state_missing lane=' + name)
    inflight = state.get('inflight')
    pending = state.get('pending')
    if type(inflight) is not int or type(pending) is not int:
        raise SystemExit('REFUSE healthz_traffic_fields_missing lane=' + name)
    if inflight != 0 or pending != 0:
        raise SystemExit(
            'REFUSE active_requests_not_drained lane=%s inflight=%d pending=%d'
            % (name, inflight, pending)
        )
print('ADMISSION_IDLE_OK')
PY
then
  echo 'REFUSE active_requests_or_missing_health_fields no_swap=true'
  exit 75
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

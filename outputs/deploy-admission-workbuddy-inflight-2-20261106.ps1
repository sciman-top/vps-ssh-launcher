#requires -Version 7
# User-authorized WorkBuddy lane concurrency deployment (2026-11-06: lane
# max_inflight 1 -> 2). The WorkBuddy lane is not one account: auth/ holds eight
# consumer OAuth credentials and CPA now round-robins across them, so
# single-flight serialised eight accounts behind one slot. The transaction
# drains the admission request lock, rotates the source, config and integrity
# pin together, and restarts only admission.
param(
  [switch]$DryRun,
  [switch]$ForceRestart,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "45c5fab6d5fab9d05f134e368be66db998bd6fb2719c4402a322ed3c9f9923ea"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$candidatePath = Join-Path $repoRoot "scripts/remote/cpa-admission.py"
$pinPath = Join-Path $repoRoot "scripts/remote/cpa-admission-integrity-pin.txt"
$candidateText = [Text.Encoding]::UTF8.GetString(
  [IO.File]::ReadAllBytes($candidatePath)
).Replace("`r`n", "`n").Replace("`r", "`n")
$candidateBytes = [Text.Encoding]::UTF8.GetBytes($candidateText)
$newPin = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($candidateBytes)
).ToLowerInvariant()
$reviewedPin = (Get-Content -LiteralPath $pinPath -Raw).Trim()
if ($reviewedPin -ne $newPin) {
  throw "Candidate source does not match scripts/remote/cpa-admission-integrity-pin.txt."
}
if ($ForceRestart) {
  throw "-ForceRestart is disabled; the transaction proves drain and health itself."
}

$payload = [Convert]::ToBase64String($candidateBytes)
# The matching lane config travels with the code for the same reason the pin
# does: the runtime refuses to start when the two disagree, and -Apply refuses
# any pin rotation, so no other entry can land them together.
$configCandidatePath = Join-Path $repoRoot "scripts/remote/cpa-admission.json"
$configText = [Text.Encoding]::UTF8.GetString(
  [IO.File]::ReadAllBytes($configCandidatePath)
).Replace("`r`n", "`n").Replace("`r", "`n")
$configBytes = [Text.Encoding]::UTF8.GetBytes($configText)
$newConfigHash = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($configBytes)
).ToLowerInvariant()
if ($newConfigHash -ne "96f4793d285ce4f109e91a64095101628afc1ae98a508a6869899a62c3268210") {
  throw "Candidate config does not match the reviewed CONFIG_HASH."
}
$configPayload = [Convert]::ToBase64String($configBytes)
$mode = if ($DryRun) { "dry-run" } else { "apply" }
$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
LIVE=/opt/cliproxyapi/cpa-admission.py
CONFIG=/opt/cliproxyapi/cpa-admission.json
OLD_CONFIG_HASH=5be8627576453b05dbf06859e9e712bc30f81f9a2f35512c1682400f02732174
CONFIG_HASH=96f4793d285ce4f109e91a64095101628afc1ae98a508a6869899a62c3268210
CONFIG_STAGED=/opt/cliproxyapi/.cpa-admission.json.new-workbuddy-inflight-2-20261106.$$
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-workbuddy-inflight-2-20261106.$$
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
MODE='__MODE__'
BACKUP_PARENT=/opt/cliproxyapi/backups
BK=$BACKUP_PARENT/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-workbuddy-inflight-2-20261106
TRAFFIC=/run/cpa-admission/requests.lock
MUTATED=0

cleanup_stage() {
  [ ! -f "$STAGED" ] || rm -f -- "$STAGED"
  [ ! -f "$CONFIG_STAGED" ] || rm -f -- "$CONFIG_STAGED"
}
trap cleanup_stage EXIT
printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
chmod 755 "$STAGED"
chown root:root "$STAGED"
printf '%s' '__CONFIG_PAYLOAD__' | base64 -d > "$CONFIG_STAGED"
chmod 644 "$CONFIG_STAGED"
chown root:root "$CONFIG_STAGED"

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
current_live_pin="$(sha256sum "$LIVE" | awk '{print $1}')"
current_installed_pin="$(tr -d '\r\n' < "$PIN")"
if [ "$current_live_pin" = "$NEW_PIN" ] && [ "$current_installed_pin" = "$NEW_PIN" ]; then
  runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
  python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health.get('status') == 'ok'
assert sorted(health.get('lanes', {})) == [
    'antigravity-oauth', 'chatgpt-oauth', 'workbuddy-oauth', 'zhipu-coding-plan'
]
print('ALREADY_APPLIED_HEALTH_OK')
PY
  echo "ALREADY_APPLIED mode=$MODE pin=$NEW_PIN no_swap=true"
  exit 0
fi
[ "$current_live_pin" = "$OLD_PIN" ] || {
  echo 'REFUSE live_baseline_drift'; exit 20;
}
# The config travels with this transaction, because -Apply refuses a pin
# rotation and therefore cannot be the one to land the matching config. The
# baseline assertion is that the live config is still the one this host ran
# before: the reviewed OLD_CONFIG_HASH. Anything else is a concurrent writer
# and must be inspected, not overwritten.
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$OLD_CONFIG_HASH" ] || {
  echo 'REFUSE admission_config_drift'; exit 20;
}
[ "$current_installed_pin" = "$OLD_PIN" ] || {
  echo 'REFUSE pin_baseline_drift'; exit 20;
}
[ "$(sha256sum "$STAGED" | awk '{print $1}')" = "$NEW_PIN" ] || {
  echo 'REFUSE staged_sha_mismatch'; exit 20;
}
[ "$(sha256sum "$CONFIG_STAGED" | awk '{print $1}')" = "$CONFIG_HASH" ] || {
  echo 'REFUSE staged_config_sha_mismatch'; exit 20;
}
python3 - "$STAGED" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')
PY
runuser -u nobody -- python3 - "$STAGED" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')
PY

check_idle() {
  python3 - <<'PY'
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
expected = [
    'antigravity-oauth', 'chatgpt-oauth', 'workbuddy-oauth', 'zhipu-coding-plan'
]
if not isinstance(lanes, dict) or sorted(lanes) != expected:
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
}

if [ "$MODE" = 'dry-run' ]; then
  exec 8<"$TRAFFIC"
  flock -n -x 8 || {
    echo 'DRY_RUN_BUSY live_traffic_active=true no_swap=true'
    exec 8<&-
    exit 0
  }
  check_idle || {
    echo 'DRY_RUN_BUSY healthz_has_inflight_or_pending no_swap=true'
    exec 8<&-
    exit 75
  }
  runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
  exec 8<&-
  echo "DRY_RUN_OK baseline=$OLD_PIN candidate=$NEW_PIN idle=true no_swap=true"
  exit 0
fi

# Block new admissions before the authoritative idle snapshot. Existing streams
# finish under the shared side; this transaction never cuts them. flock does not
# queue waiters, so a steady shared-lock stream can starve a blocking request;
# poll with a deadline and emit a heartbeat so the channel never looks idle.
echo 'PREFLIGHT_OK'
exec 8<"$TRAFFIC"
acquired=0
for attempt in $(seq 1 240); do
  if flock -x -w 1 8; then
    acquired=1
    break
  fi
  if [ $((attempt % 15)) -eq 0 ]; then
    echo "DRAIN_WAIT attempt=$attempt live_streams_still_open=true"
  fi
done
if [ "$acquired" != 1 ]; then
  echo 'REFUSE live_traffic_active lock_not_acquired no_swap=true'
  exit 75
fi
echo 'REQUEST_GUARD_EXCLUSIVE_OK'
check_idle

mkdir -p "$BACKUP_PARENT"
mkdir -m 700 "$BK"
# Record the unit and integrity drop-in state before the first mutation.
systemctl cat cpa-admission.service > "$BK/cpa-admission.service.txt"
systemctl show cpa-admission.service --no-pager > "$BK/cpa-admission.service.show.txt"
systemctl is-enabled cpa-admission.service > "$BK/cpa-admission.service.enabled.txt"
systemctl is-active cpa-admission.service > "$BK/cpa-admission.service.active.txt"
cp -a "$LIVE" "$PIN" "$BK/"
echo "BACKUP_READY path=$BK"

rollback() {
  local rc=${1:-$?} failed=0
  trap - EXIT HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    cp -a "$BK/cpa-admission.py" "$LIVE" || failed=1
    cp -a "$BK/cpa-admission.json" "$CONFIG" || failed=1
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
# Config lands with the code: the runtime refuses to start when its reviewed
# per-lane bound disagrees with the config, so a half swap is a hard outage.
mv -f "$CONFIG_STAGED" "$CONFIG"
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"
chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN"
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl restart cpa-admission

deadline=$((SECONDS + 30))
while :; do
  check_idle >/dev/null 2>&1 && break
  if (( SECONDS >= deadline )); then
    echo 'REFUSE readiness_timeout'
    rollback 20
  fi
  sleep 1
done
[ "$(sha256sum "$LIVE" | awk '{print $1}')" = "$NEW_PIN" ]
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$CONFIG_HASH" ]
[ "$(tr -d '\r\n' < "$PIN")" = "$NEW_PIN" ]
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl is-active --quiet cpa-admission

exec 8<&- 2>/dev/null || true
if ! python3 - "$LIVE" <<'PY_6004'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("cpa_admission_candidate", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit("candidate_import_failed")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
config = module.load_config(Path("/opt/cliproxyapi/cpa-admission.json"))
lanes = {item["name"]: item for item in config["lanes"]}
# The deployed generation must accept the new bound, and the source must agree
# with the config it just loaded (the guard exists so a stale candidate cannot
# project a lane the runtime would then refuse to start with).
assert lanes["workbuddy-oauth"]["max_inflight"] == 2
assert module.ADMISSION_MAX_INFLIGHT_BY_LANE["workbuddy-oauth"] == 2
# The sibling shared-account lanes keep their reviewed bounds.
assert module.ADMISSION_MAX_INFLIGHT_BY_LANE["chatgpt-oauth"] == 2
assert module.ADMISSION_MAX_INFLIGHT_BY_LANE["antigravity-oauth"] == 2
assert module.ADMISSION_MAX_INFLIGHT_BY_LANE["claude-oauth"] == 2
# The 6004 bounded park from the previous generation must still be in force:
# raising concurrency must not have been bought by dropping risk control.
assert module.WORKBUDDY_6004_MAX_COOLDOWN_SECONDS == 300
print("WORKBUDDY_INFLIGHT_CONTRACT_OK")
PY_6004
then
  # The offline guard is outside the request lock. Re-drain before restoring a
  # source generation so rollback itself cannot cut a newly admitted stream.
  exec 8<"$TRAFFIC"
  flock -x 8 || {
    echo 'ROLLBACK_REFUSED live_traffic_active lock_not_acquired'
    exit 75
  }
  rollback 20
fi
if ! python3 - <<'PY'
import json, urllib.error, urllib.request
body = b'{"model":"gpt-6-luna","input":"encoding guard"}'
request = urllib.request.Request(
    'http://127.0.0.1:8318/v1/responses',
    data=body,
    method='POST',
    headers={
        'Content-Type': 'application/json',
        'Content-Encoding': 'zstd',
    },
)
try:
    urllib.request.urlopen(request, timeout=5)
except urllib.error.HTTPError as exc:
    assert exc.code == 415, exc.code
    payload = json.load(exc)
    assert payload['error']['code'] == 'unsupported_content_encoding'
    assert payload['upstream_contacted'] is False
    assert exc.headers.get('X-CPA-Admission-Reason') == 'unsupported_content_encoding'
else:
    raise SystemExit('REFUSE encoding_guard_not_enforced')
print('ADMISSION_REGRESSION_GUARD_OK')
PY
then
  # The probe is outside the request lock. Re-drain before restoring a source
  # generation so rollback itself cannot cut a newly admitted stream.
  exec 8<"$TRAFFIC"
  flock -x 8 || {
    echo 'ROLLBACK_REFUSED live_traffic_active lock_not_acquired'
    exit 75
  }
  rollback 20
fi

MUTATED=0
trap - EXIT HUP INT TERM
(
  find "$BACKUP_PARENT" -mindepth 1 -maxdepth 1 -type d \
    -name '*-admission-workbuddy-inflight-2-20261106' -printf '%T@ %p\n' \
    | sort -rn | tail -n +9 | cut -d' ' -f2-
) | while IFS= read -r old_backup; do
  rm -rf -- "$old_backup"
done
echo "DEPLOY_OK pin=$NEW_PIN backup=$BK"
'@

$remote = $remote.Replace("__OLD_PIN__", $ExpectedLivePin).
  Replace("__NEW_PIN__", $newPin).
  Replace("__MODE__", $mode).
  Replace("__PAYLOAD__", $payload).
  Replace("__CONFIG_PAYLOAD__", $configPayload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled WorkBuddy inflight admission transaction placeholder."
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900 -RemoteTempPrefix "admission-workbuddy-inflight-2-20261106"

#requires -Version 7
# User-authorized workbuddy bare-429 transient cooldown fix + stale hold
# release (2026-11-07).
#
# Incident: one upstream 429 blip (no business code, no Retry-After) between
# healthy 200s latched retry_window_review_required and gated every model on
# workbuddy-oauth behind operator_review_required, while a direct upstream
# single-shot probe succeeded minutes later. The generic WorkBuddy rate-limit
# family (bare 429/6003 envelopes, rate_limit_error wrappers without quoted
# account codes) now rides the capacity ladder instead of latching; durable
# account states keep their dedicated signals (6008 credits, 11140/auth).
#
# The transaction drains the admission request lock, releases the latched
# hold with an exact-reason assertion, rotates the source and its integrity
# pin together, restarts admission once, and proves the new classification on
# the deployed generation.
param(
  [switch]$DryRun,
  [switch]$ForceRestart,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "fcfc5e9dec0dd476ce197678685b69beae0178b7d9dd90af71bbcb4ba618a66e"
)

if ($ForceRestart) {
  throw "-ForceRestart is disabled; the transaction proves drain and health itself."
}

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
if (-not $candidateText.Contains('no longer latches the operator-review hold')) {
  throw "Candidate source is missing the bare-429 transient classification."
}
if (-not $candidateText.Contains('account_credits_exhausted')) {
  throw "Candidate source is missing the credits-exhausted classification."
}

$payload = [Convert]::ToBase64String($candidateBytes)
# The lane config travels with the code (runtime refuses to start when a
# lane's reviewed config disagrees with the source). This window changes no
# config bytes, but the contract keeps moving them together.
$configCandidatePath = Join-Path $repoRoot "scripts/remote/cpa-admission.json"
$configText = [Text.Encoding]::UTF8.GetString(
  [IO.File]::ReadAllBytes($configCandidatePath)
).Replace("`r`n", "`n").Replace("`r", "`n")
$configBytes = [Text.Encoding]::UTF8.GetBytes($configText)
$configPayload = [Convert]::ToBase64String($configBytes)
if (-not $configText.Contains('zero_cost_models')) {
  throw "Candidate config is missing zero_cost_models."
}
$mode = if ($DryRun) { "dry-run" } else { "apply" }
$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
LIVE=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-bare429-20261107.$$
CONFIG=/opt/cliproxyapi/cpa-admission.json
CONFIG_STAGED=/opt/cliproxyapi/.cpa-admission.json.new-bare429-20261107.$$
STATE=/var/lib/cpa-admission/workbuddy-risk.json
EXPECTED_HOLD_REASON='retry_window_review_required'
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
MODE='__MODE__'
BACKUP_PARENT=/opt/cliproxyapi/backups
BK=$BACKUP_PARENT/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-bare429-transient-20261107
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
hold = health['lanes']['workbuddy-oauth']['state'].get('hold_reason')
assert hold is None, 'hold still present after deploy: %r' % hold
print('ALREADY_APPLIED_HEALTH_OK')
PY
  echo "ALREADY_APPLIED mode=$MODE pin=$NEW_PIN no_swap=true"
  exit 0
fi
[ "$current_live_pin" = "$OLD_PIN" ] || {
  echo 'REFUSE live_baseline_drift'; exit 20;
}
[ "$current_installed_pin" = "$OLD_PIN" ] || {
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

# Exact-reason assertion for the hold release: only the incident reason is
# releasable here; any drift stops the transaction before anything mutates.
python3 - "$STATE" "$EXPECTED_HOLD_REASON" <<'PY'
import json, sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert set(state) == {"version", "not_before", "hold_reason"}, sorted(state)
reason = state["hold_reason"]
assert reason == sys.argv[2], "REFUSE reason_drift got=%s" % reason
assert isinstance(state["not_before"], (int, float))
print("HOLD_PREFLIGHT_OK reason=%s" % reason)
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
        raise SystemExit(
            'REFUSE healthz_traffic_fields_missing lane=' + name
        )
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
systemctl cat cpa-admission.service > "$BK/cpa-admission.service.txt"
systemctl show cpa-admission.service --no-pager > "$BK/cpa-admission.service.show.txt"
systemctl is-enabled cpa-admission.service > "$BK/cpa-admission.service.enabled.txt"
systemctl is-active cpa-admission.service > "$BK/cpa-admission.service.active.txt"
cp -a "$LIVE" "$PIN" "$CONFIG" "$STATE" "$BK/"
sha256sum "$BK"/*
echo "BACKUP_READY path=$BK"

rollback() {
  local rc=${1:-$?} failed=0
  trap - EXIT HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    cp -a "$BK/cpa-admission.py" "$LIVE" || failed=1
    cp -a "$BK/cpa-admission.sha256" "$PIN" || failed=1
    cp -a "$BK/cpa-admission.json" "$CONFIG" || failed=1
    # Conservative re-latch: restoring the durable hold backup returns the
    # lane to its pre-transaction state, so a failed swap can never leave a
    # half-released hold behind.
    cp -a "$BK/workbuddy-risk.json" "$STATE" || failed=1
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
mv -f "$CONFIG_STAGED" "$CONFIG"
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"
chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN"
# Release the stale hold: atomic write under the exclusive request guard,
# before the single restart that activates the new generation.
python3 - "$STATE" <<'PY_RELEASE'
import json, os, sys
from pathlib import Path

path = Path(sys.argv[1])
state = json.loads(path.read_text(encoding="utf-8"))
assert set(state) == {"version", "not_before", "hold_reason"}, sorted(state)
assert state["hold_reason"] == "retry_window_review_required", state["hold_reason"]
state["hold_reason"] = None
temporary = path.with_name(path.name + ".releasing")
temporary.write_text(json.dumps(state) + "\n", encoding="utf-8")
os.chmod(temporary, path.stat().st_mode & 0o777)
os.chown(temporary, path.stat().st_uid, path.stat().st_gid)
os.replace(temporary, path)
print("HOLD_RELEASED")
PY_RELEASE
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
[ "$(tr -d '\r\n' < "$PIN")" = "$NEW_PIN" ]
python3 - "$STATE" <<'PY_STATE'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["hold_reason"] is None, state["hold_reason"]
print("HOLD_STATE_CLEARED")
PY_STATE
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl is-active --quiet cpa-admission

exec 8<&- 2>/dev/null || true
# Prove the deployed generation: the frequency family cools transiently, and
# every durable account signal keeps its dedicated classification.
if ! python3 - "$LIVE" <<'PY_CONTRACT'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("cpa_admission_candidate", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit("candidate_import_failed")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

bare_429 = b'{"code":429,"msg":"rate limited"}'
code_6003 = b'{"code":6003,"msg":"frequency limited"}'
wrapper = b'{"error":{"type":"rate_limit_error"}}'
for body in (bare_429, code_6003, wrapper):
    hold, delay = module.oauth_risk_signal("workbuddy-oauth", 429, body)
    assert (hold, delay) == (None, None), (body, hold, delay)

credits = b'upstream 429: {"code":6008,"msg":"Credits exhausted"}'
hold, delay = module.oauth_risk_signal("workbuddy-oauth", 429, credits)
assert (hold, delay) == ("account_credits_exhausted", None), (hold, delay)

restricted = b'{"code":11140,"msg":"request illegal"}'
hold, _ = module.oauth_risk_signal("workbuddy-oauth", 200, restricted)
assert hold == "account_restricted", hold

busy = b'{"code":14003,"msg":"Model busy"}'
hold, _ = module.oauth_risk_signal("workbuddy-oauth", 429, busy)
assert hold is None, hold

_CFG = module.load_config(Path("/opt/cliproxyapi/cpa-admission.json"))
_LANE = next(item for item in _CFG["lanes"] if item["name"] == "workbuddy-oauth")
assert "hy4free" in _LANE["zero_cost_models"], _LANE.get("zero_cost_models")
assert module.WORKBUDDY_6004_MAX_COOLDOWN_SECONDS == 300
print("BARE429_TRANSIENT_CONTRACT_OK")
PY_CONTRACT
then
  echo 'REFUSE post_swap_contract_failed'
  rollback 20
fi

# Lane-hold delta: the released lane must stay clear and no other lane may
# have latched a hold across the restart.
python3 - <<'PY_DELTA'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
for name, lane in sorted(health["lanes"].items()):
    hold = lane["state"].get("hold_reason")
    assert hold is None, "hold on %s: %s" % (name, hold)
print("LANE_HOLD_DELTA=none")
PY_DELTA

MUTATED=0
trap - EXIT HUP INT TERM
(
  find "$BACKUP_PARENT" -mindepth 1 -maxdepth 1 -type d \
    -name '*-admission-bare429-transient-20261107' -printf '%T@ %p\n' \
    | sort -rn | tail -n +9 | cut -d' ' -f2-
) | while IFS= read -r old_backup; do
  rm -rf -- "$old_backup"
done
echo "RELEASE_OK lane=workbuddy-oauth cleared=$EXPECTED_HOLD_REASON backup=$BK"
echo "DEPLOY_OK pin=$NEW_PIN backup=$BK"
'@

$remote = $remote.Replace("__OLD_PIN__", $ExpectedLivePin).
  Replace("__NEW_PIN__", $newPin).
  Replace("__MODE__", $mode).
  Replace("__PAYLOAD__", $payload).
  Replace("__CONFIG_PAYLOAD__", $configPayload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled admission bare429 transaction placeholder."
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900 -RemoteTempPrefix "admission-bare429-20261107"

#requires -Version 7
# User-authorized admission de-latch deployment (2026-11-14).
#
# Root-cause hardening for the recurring false operator-review latches and
# the abnormally long lane windows:
#   1. 6008 ("Credits exhausted") rides the capacity ladder beside the 6004
#      family instead of latching `account_credits_exhausted`: it is the
#      burnout signature of the UTC+8 daily free-credit allowance and
#      self-heals at the day boundary. The old hold manufactured the
#      2026-11-13 "all 14 credentials dead, purchase add-on" verdict,
#      falsified 2026-11-14 by a CN-credential single-shot 200 minutes after
#      the boundary with zero account actions.
#   2. A bare 401/403 status (empty body, untyped JSON, permission_error,
#      in-band codes without typed account evidence) no longer latches a
#      durable hold on chatgpt/workbuddy; the 2026-11-11 overnight latch was
#      a fallback leg's JSON 403.
#   3. capacity_scope's model_not_found exemption also matches the
#      quote-escaped shape inside the CPA `upstream <status>: {json}`
#      wrapper (the 2026-11-13 half-day parks rode exactly this gap).
#   4. Zero-cost models (hy4free) pass the lane cooldown, not just the old
#      credits hold.
#
# The transaction drains the admission request lock, rotates the source and
# its integrity pin together, restarts only admission, and proves the new
# classification contract on the deployed generation.
param(
  [switch]$DryRun,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "de1a548ddaf3f23f6aa31f1b2fca141e74c766eda4b5d52d4d8cdb1d23a1d3d9"
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
if (-not $candidateText.Contains('free_under_billing_state')) {
  throw "Candidate source is missing the zero-cost cooldown bypass."
}
if (-not $candidateText.Contains('not supported when using codex')) {
  throw "Candidate source is missing the wrapped model_not_found exemption."
}

# The request guard embeds the admission generation it serves: the bootstrap
# refuses to start when sha256(live admission) != guard.SOURCE_SHA256. The
# guard therefore travels with the code in this transaction, exactly like the
# -Apply projection pairs them (scripts/cpa_bwg_guardrails.ps1 replaces
# __SOURCE_SHA256__ with the LF-normalized admission hash).
$guardCandidatePath = Join-Path $repoRoot "scripts/remote/cpa_request_guard.py"
$guardTemplateText = [Text.Encoding]::UTF8.GetString(
  [IO.File]::ReadAllBytes($guardCandidatePath)
).Replace("`r`n", "`n").Replace("`r", "`n")
if (-not $guardTemplateText.Contains('__SOURCE_SHA256__')) {
  throw "Request guard template is missing the __SOURCE_SHA256__ placeholder."
}
$guardDeployedText = $guardTemplateText.Replace('__SOURCE_SHA256__', $newPin)
$guardBytes = [Text.Encoding]::UTF8.GetBytes($guardDeployedText)
$guardPayload = [Convert]::ToBase64String($guardBytes)

$payload = [Convert]::ToBase64String($candidateBytes)
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
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-delatch-20261114.$$
CONFIG=/opt/cliproxyapi/cpa-admission.json
CONFIG_STAGED=/opt/cliproxyapi/.cpa-admission.json.new-delatch-20261114.$$
GUARD=/usr/local/libexec/cpa_request_guard.py
GUARD_STAGED=/usr/local/libexec/.cpa_request_guard.py.new-delatch-20261114.$$
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
MODE='__MODE__'
BACKUP_PARENT=/opt/cliproxyapi/backups
BK=$BACKUP_PARENT/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-delatch-20261114
TRAFFIC=/run/cpa-admission/requests.lock
MUTATED=0

cleanup_stage() {
  [ ! -f "$STAGED" ] || rm -f -- "$STAGED"
  [ ! -f "$CONFIG_STAGED" ] || rm -f -- "$CONFIG_STAGED"
  [ ! -f "$GUARD_STAGED" ] || rm -f -- "$GUARD_STAGED"
}
trap cleanup_stage EXIT
printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
chmod 755 "$STAGED"
chown root:root "$STAGED"
printf '%s' '__CONFIG_PAYLOAD__' | base64 -d > "$CONFIG_STAGED"
chmod 644 "$CONFIG_STAGED"
chown root:root "$CONFIG_STAGED"
printf '%s' '__GUARD_PAYLOAD__' | base64 -d > "$GUARD_STAGED"
chmod 644 "$GUARD_STAGED"
chown root:root "$GUARD_STAGED"
# The live guard must still name the baseline admission generation.
grep -q "SOURCE_SHA256 = \"$OLD_PIN\"" "$GUARD" || {
  echo 'REFUSE live_guard_generation_drift'; exit 20;
}
grep -q "SOURCE_SHA256 = \"$NEW_PIN\"" "$GUARD_STAGED" || {
  echo 'REFUSE staged_guard_generation_mismatch'; exit 20;
}

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
cp -a "$LIVE" "$PIN" "$CONFIG" "$GUARD" "$BK/"
echo "BACKUP_READY path=$BK"

rollback() {
  local rc=${1:-$?} failed=0
  trap - EXIT HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    cp -a "$BK/cpa-admission.py" "$LIVE" || failed=1
    cp -a "$BK/cpa-admission.sha256" "$PIN" || failed=1
    cp -a "$BK/cpa-admission.json" "$CONFIG" || failed=1
    cp -a "$BK/cpa_request_guard.py" "$GUARD" || failed=1
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
mv -f "$GUARD_STAGED" "$GUARD"
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
[ "$(tr -d '\r\n' < "$PIN")" = "$NEW_PIN" ]
grep -q "SOURCE_SHA256 = \"$NEW_PIN\"" "$GUARD"
python3 - "$CONFIG" <<'PY_CFG'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
lane = next(item for item in cfg["lanes"] if item["name"] == "workbuddy-oauth")
assert "hy4free" in lane["zero_cost_models"], lane.get("zero_cost_models")
print("CONFIG_ZERO_COST_OK")
PY_CFG
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl is-active --quiet cpa-admission

exec 8<&- 2>/dev/null || true
# Prove the deployed generation: 6008 is window-class (no hold), bare auth
# statuses no longer latch, structured account evidence still does, the
# wrapped model_not_found shape stays exempt from lane scoping, and the
# zero-cost scope survives.
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

wrapped = b'upstream 429: {"code":6008,"msg":"Credits exhausted. Please visit the link"}'
hold, delay = module.oauth_risk_signal("workbuddy-oauth", 429, wrapped)
assert (hold, delay) == (None, None), (hold, delay)

bare403 = b'{"error":{"type":"permission_error","code":403}}'
assert module.oauth_risk_signal("workbuddy-oauth", 403, bare403) == (None, None)
typed_auth = b'{"error":{"type":"authentication_error","code":401}}'
hold_auth, _ = module.oauth_risk_signal("chatgpt-oauth", 401, typed_auth)
assert hold_auth == "authentication_required", hold_auth
suspended = b'{"error":{"message":"account has been suspended"}}'
hold_susp, _ = module.oauth_risk_signal("workbuddy-oauth", 403, suspended)
assert hold_susp == "account_restricted", hold_susp

busy = b'upstream 429: {"code":14003,"msg":"Model busy"}'
hold_busy, _ = module.oauth_risk_signal("workbuddy-oauth", 429, busy)
assert hold_busy is None, hold_busy

chatgpt = module.load_config(Path("/opt/cliproxyapi/cpa-admission.json"))
chatgpt = next(item for item in chatgpt["lanes"] if item["name"] == "chatgpt-oauth")
wrapped_nf = (
    b'{"error":{"message":"upstream 400: {\\"code\\":\\"model_not_found\\",'
    b'\\"message\\":\\"not supported when using Codex with a ChatGPT account\\"}"}}'
)
assert module.capacity_scope(503, wrapped_nf, "25902", chatgpt) is None
prose_nf = (
    b'upstream 400: {"code":"model_not_found",'
    b'"message":"not supported when using Codex with a ChatGPT account"}'
)
assert module.capacity_scope(503, prose_nf, "25902", chatgpt) is None
direct_nf = b'{"error":{"code":"model_not_found"}}'
assert module.capacity_scope(400, direct_nf, None, chatgpt) is None

_CFG = module.load_config(Path("/opt/cliproxyapi/cpa-admission.json"))
_LANE = next(item for item in _CFG["lanes"] if item["name"] == "workbuddy-oauth")
assert "hy4free" in _LANE["zero_cost_models"], _LANE.get("zero_cost_models")
assert module.WORKBUDDY_6004_MAX_COOLDOWN_SECONDS == 300
print("DELATCH_20261114_CONTRACT_OK")
PY_CONTRACT
then
  echo 'REFUSE post_swap_contract_failed'
  rollback 20
fi

MUTATED=0
trap - EXIT HUP INT TERM
(
  find "$BACKUP_PARENT" -mindepth 1 -maxdepth 1 -type d \
    -name '*-admission-delatch-20261114' -printf '%T@ %p\n' \
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
  Replace("__CONFIG_PAYLOAD__", $configPayload).
  Replace("__GUARD_PAYLOAD__", $guardPayload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled admission delatch transaction placeholder."
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900 -RemoteTempPrefix "admission-delatch-20261114"

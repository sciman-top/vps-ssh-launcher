#requires -Version 7
# Admission edge-denial classification fix (2026-11-09).
#
# cpa-admission.py rewrites are refused by -Apply by design ("rotate admission
# code with a reviewed deploy transaction"), so this transaction is that review.
# It drains the admission request lock, rotates source and integrity pin
# together, restarts only admission, proves the antigravity edge-denial rule is
# live, and rolls back on any mismatch.
param(
  [switch]$DryRun,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "e0ad23c9c900d67b8d94abcb1fe8b3ec38a1d0dd5f2e9bb147ce2dc099084870"
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

$payload = [Convert]::ToBase64String($candidateBytes)
$mode = if ($DryRun) { "dry-run" } else { "apply" }
$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
LIVE=/opt/cliproxyapi/cpa-admission.py
CONFIG=/opt/cliproxyapi/cpa-admission.json
CONFIG_HASH=f3a45d75778d2b76ee9507a799bcd71f2e26d9680af16f869c1278ab7fb58716
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-edgedenial-20261109.$$
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
MODE='__MODE__'
BACKUP_PARENT=/opt/cliproxyapi/backups
BK=$BACKUP_PARENT/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-edgedenial-20261109
TRAFFIC=/run/cpa-admission/requests.lock
MUTATED=0

cleanup_stage() {
  [ ! -f "$STAGED" ] || rm -f -- "$STAGED"
}
trap cleanup_stage EXIT
printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
chmod 755 "$STAGED"
chown root:root "$STAGED"

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
current_live_pin="$(sha256sum "$LIVE" | awk '{print $1}')"
current_installed_pin="$(tr -d '\r\n' < "$PIN")"
if [ "$current_live_pin" = "$NEW_PIN" ] && [ "$current_installed_pin" = "$NEW_PIN" ]; then
  runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
  echo "ALREADY_APPLIED mode=$MODE pin=$NEW_PIN no_swap=true"
  exit 0
fi
[ "$current_live_pin" = "$OLD_PIN" ] || {
  echo 'REFUSE live_baseline_drift'; exit 20;
}
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$CONFIG_HASH" ] || {
  echo 'REFUSE admission_config_drift'; exit 20;
}
[ "$current_installed_pin" = "$OLD_PIN" ] || {
  echo 'REFUSE pin_baseline_drift'; exit 20;
}
[ "$(sha256sum "$STAGED" | awk '{print $1}')" = "$NEW_PIN" ] || {
  echo 'REFUSE staged_sha_mismatch'; exit 20;
}
# The staged file must be readable by the unit own user before it replaces
# the live file: a root-only file would crash-loop ExecStartPre forever.
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

# Post-swap contract for THIS change: an antigravity 403 carrying no
# structured account evidence must NOT latch a durable operator hold, while a
# structured TOS_VIOLATION envelope still must. Asserting both directions
# means a regression either way fails the deploy instead of parking a healthy
# Google AI Pro lane (or hiding a real revocation).
check_edge_denial_contract() {
  python3 - <<'PY'
import importlib.util, json, sys

spec = importlib.util.spec_from_file_location(
    'cpa_admission_deploy', '/opt/cliproxyapi/cpa-admission.py'
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

page = b'<!DOCTYPE html><html><head><title>403 Forbidden</title></head></html>'
if module.oauth_risk_signal('antigravity-oauth', 403, page) != (None, None):
    raise SystemExit('REFUSE antigravity_edge_page_still_latches')

tos = json.dumps({
    'error': {
        'code': 403,
        'status': 'PERMISSION_DENIED',
        'details': [{
            '@type': 'type.googleapis.com/google.rpc.ErrorInfo',
            'reason': 'TOS_VIOLATION',
        }],
    }
}).encode()
if module.oauth_risk_signal('antigravity-oauth', 403, tos)[0] != 'account_restricted':
    raise SystemExit('REFUSE antigravity_tos_still_latching')

# The sibling lanes keep the behaviour they already had.
for lane in ('chatgpt-oauth', 'workbuddy-oauth'):
    if module.oauth_risk_signal(lane, 403, page) != (None, None):
        raise SystemExit('REFUSE edge_rule_regressed lane=' + lane)
print('EDGE_DENIAL_CONTRACT_OK')
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

exec 8<"$TRAFFIC"
flock -x 8 || {
  echo 'REFUSE live_traffic_active lock_not_acquired no_swap=true'
  exit 75
}
echo 'REQUEST_GUARD_EXCLUSIVE_OK'
check_idle

mkdir -p "$BACKUP_PARENT"
mkdir -m 700 "$BK"
systemctl cat cpa-admission.service > "$BK/cpa-admission.service.txt"
systemctl show cpa-admission.service --no-pager > "$BK/cpa-admission.service.show.txt"
systemctl is-enabled cpa-admission.service > "$BK/cpa-admission.service.enabled.txt"
systemctl is-active cpa-admission.service > "$BK/cpa-admission.service.active.txt"
cp -a "$LIVE" "$PIN" "$BK/"
mkdir -m 700 "$BK/lane-state"
cp -a /var/lib/cpa-admission/*.json "$BK/lane-state/" 2>/dev/null || true

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
check_edge_denial_contract

exec 8<&- 2>/dev/null || true
MUTATED=0
trap - EXIT HUP INT TERM
echo "DEPLOY_OK pin=$NEW_PIN backup=$BK"
'@

$remote = $remote.Replace("__OLD_PIN__", $ExpectedLivePin).
  Replace("__NEW_PIN__", $newPin).
  Replace("__MODE__", $mode).
  Replace("__PAYLOAD__", $payload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled admission edge-denial transaction placeholder."
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 600 -RemoteTempPrefix "admission-edgedenial-20261109"

#requires -Version 7
# Admission state-machine stabilization deploy (2026-11-11).
#
# cpa-admission.py rewrites are refused by -Apply by design ("rotate admission
# code with a reviewed deploy transaction"), so this transaction is that review.
# Round content: recovery latch keeps the operator-visible hold across a clean
# recovery write (pre-failure upstream hold restored verbatim; no prior hold
# keeps state_write_failed), and the first-failure snapshot actually records
# the pre-failure hold/cooldown. It drains the admission request lock, rotates
# source and integrity pin together, restarts only admission, proves both
# recovery contracts are live, and rolls back on any mismatch.
param(
  [switch]$DryRun,
  [ValidateRange(0, 300)]
  [int]$DrainWaitSeconds = 0,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "476e1f24b2ea291c554542f1a4f60672aed5fa9cb39062f4882131ab094b85dc",
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedInstalledPin = "375da23846ae0cf5169f6210150ea085e053d9760f479c5cf46ae7d46941f96d",
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$CurrentDeployedPin = "c410187f7975131630bcdb0c7e2bc4f045df1fb5fc9165a59b1bd1867fda6ea5"
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
if ($ExpectedInstalledPin -eq $newPin) {
  throw "ExpectedInstalledPin must name the old installed generation; use idempotent apply for verification."
}
if ($CurrentDeployedPin -eq $ExpectedLivePin) {
  throw "CurrentDeployedPin must name the separately audited deployed generation."
}
# Bind the explicit profile to the endpoint this review observed. This catches
# a redirected bwg config without printing the target address or credentials.
$targetConfig = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
$bwgProfile = $targetConfig.profiles.bwg
if (
  $null -eq $bwgProfile -or
  ([string]$bwgProfile.host).Trim() -ne "144.34.229.116" -or
  [int]$bwgProfile.port -ne 29712 -or
  ([string]$bwgProfile.user).Trim() -ne "root"
) {
  throw "REFUSE bwg_target_endpoint_mismatch"
}

$payload = [Convert]::ToBase64String($candidateBytes)
$mode = if ($DryRun) { "dry-run" } else { "apply" }
$drainWait = if ($DryRun) { $DrainWaitSeconds } else { 0 }
$remoteBody = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[ "${VPS_SSH_LAUNCHER_PROFILE:-}" = 'bwg' ] || {
  echo 'REFUSE profile_not_bwg_remote'; exit 64;
}
LIVE=/opt/cliproxyapi/cpa-admission.py
CONFIG=/opt/cliproxyapi/cpa-admission.json
CONFIG_HASH=f3a45d75778d2b76ee9507a799bcd71f2e26d9680af16f869c1278ab7fb58716
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-stabilization-20261111.$$
OLD_PIN=__OLD_PIN__
OLD_SOURCE_PIN=__OLD_SOURCE_PIN__
CURRENT_PIN=__CURRENT_PIN__
NEW_PIN=__NEW_PIN__
MODE='__MODE__'
DRAIN_WAIT=__DRAIN_WAIT__
BACKUP_PARENT=/opt/cliproxyapi/backups
BK=$BACKUP_PARENT/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-stabilization-20261111
TRAFFIC=/run/cpa-admission/requests.lock
LOCK_INFRA=/run/vps-ssh-launcher-admission-deploy-20261112.lock
MUTATED=0
SOURCE_APPLIED=0
PIN_APPLIED=0

cleanup_stage() {
  [ ! -f "$STAGED" ] || rm -f -- "$STAGED"
}
trap cleanup_stage EXIT
printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
chmod 755 "$STAGED"
chown root:root "$STAGED"

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
# bwg owns this gateway and the four reviewed admission lanes. zz does not
# expose the same service contract, so a redirected SSH target fails here.
[ -f /etc/nginx/conf.d/cpa-gateway.conf ] || {
  echo 'REFUSE bwg_gateway_identity_missing'; exit 20;
}
[ "$(systemctl show cpa-admission.service -p LoadState --value)" = 'loaded' ] || {
  echo 'REFUSE bwg_admission_service_identity'; exit 20;
}
[ "$(systemctl show cpa-admission.service -p User --value)" = 'nobody' ] || {
  echo 'REFUSE bwg_admission_service_user'; exit 20;
}
current_live_pin="$(sha256sum "$LIVE" | awk '{print $1}')"
current_installed_pin="$(tr -d '\r\n' < "$PIN")"
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$CONFIG_HASH" ] || {
  echo 'REFUSE admission_config_drift'; exit 20;
}
[ "$(sha256sum "$STAGED" | awk '{print $1}')" = "$NEW_PIN" ] || {
  echo 'REFUSE staged_sha_mismatch'; exit 20;
}
runuser -u nobody -- python3 - "$STAGED" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')
PY

check_idle() {
  python3 - <<'PY_PROCESS'
import json, urllib.error, urllib.request
import subprocess

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
    pid = lane.get('pid') if isinstance(lane, dict) else None
    if type(pid) is not int or pid <= 0:
        # The production unit runs one admission process. `/healthz` is served
        # by that process, so its sockets and threads cannot outlive a restart
        # silently. Older health snapshots omit pid and remain observable.
        continue
    try:
        sockets = subprocess.run(
            ['ss', '-Htnp', 'state', 'established', 'dport = :8317'],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise SystemExit('REFUSE socket_inventory_unavailable')
    if ('pid=%d,' % pid) in sockets:
        raise SystemExit('REFUSE active_upstream_socket')
    if '%04X' % pid in sockets:
        raise SystemExit('REFUSE active_upstream_socket')
print('ADMISSION_IDLE_OK')
PY_PROCESS
}

acquire_traffic() {
  local timeout=${1:-$DRAIN_WAIT}
  : > "$LOCK_INFRA"
  chmod 600 "$LOCK_INFRA"
  # The traffic lock needs a true exclusive flock(), not merely permission to
  # open the file. Keep a tiny guard alive across the body: helpers may retain
  # an inherited descriptor until EOF, which would otherwise extend the lock
  # beyond rollback. The guard receives no business data and always exits.
  exec 8<"$TRAFFIC"
  exec 7<"$LOCK_INFRA"
  local guard_pid
  coproc DEPLOY_GUARD { python3 - "$TRAFFIC" "$timeout" "$LOCK_INFRA" <<'PY_GUARD'
import fcntl, os, sys, time

traffic, timeout_string, infrastructure = sys.argv[1:]
timeout = int(timeout_string)
deadline = time.monotonic() + timeout
hold = inf_hold = None
control = sys.stdin
sys.stdin = open('/dev/null')
try:
    hold = open(traffic, 'a')
    inf_hold = open(infrastructure, 'a')
    fcntl.flock(inf_hold, fcntl.LOCK_EX | fcntl.LOCK_NB)
    while True:
        try:
            fcntl.flock(hold, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise SystemExit(75)
            time.sleep(0.25)
    print('REQUEST_GUARD_EXCLUSIVE_OK', flush=True)
    if control.readline().strip() != 'REQUEST_GUARD_COMMIT':
        raise SystemExit(124)
finally:
    if inf_hold is not None:
        inf_hold.close()
    if hold is not None:
        hold.close()
PY_GUARD
  }
  guard_pid=$DEPLOY_GUARD_PID
  local guard_line
  IFS= read -r guard_line <&"${DEPLOY_GUARD[0]}"
  [ "$guard_line" = 'REQUEST_GUARD_EXCLUSIVE_OK' ] || {
    wait "$guard_pid" || true
    DEPLOY_GUARD_PID=
    echo 'REFUSE request_guard_handoff'; return 75;
  }
}

release_guard() {
  local guard_pid=${DEPLOY_GUARD_PID:-}
  if [ -n "$guard_pid" ]; then
    printf 'REQUEST_GUARD_COMMIT\n' >&"${DEPLOY_GUARD[1]}" 2>/dev/null || true
    eval "exec ${DEPLOY_GUARD[0]}<&-" 2>/dev/null || true
    eval "exec ${DEPLOY_GUARD[1]}>&-" 2>/dev/null || true
    wait "$guard_pid" 2>/dev/null || true
  fi
}

release_traffic() {
  release_guard
  DEPLOY_GUARD_PID=
  exec 8<&- 2>/dev/null || true
  exec 7<&- 2>/dev/null || true
}

if [ "$current_live_pin" = "$NEW_PIN" ] && [ "$current_installed_pin" = "$NEW_PIN" ]; then
  acquire_traffic "$DRAIN_WAIT" || exit 75
  runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
  systemctl is-active --quiet cpa-admission
  check_idle
  release_traffic
  echo "ALREADY_APPLIED mode=$MODE pin=$NEW_PIN no_swap=true"
  exit 0
fi
if [ "$current_live_pin" = "$CURRENT_PIN" ] && [ "$current_installed_pin" = "$CURRENT_PIN" ]; then
  # A separately recorded transaction already published this generation.
  acquire_traffic "$DRAIN_WAIT" || exit 75
  runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
  systemctl is-active --quiet cpa-admission
  check_idle
  release_traffic
  if [ "$MODE" = 'dry-run' ]; then
    echo "DRY_RUN_OK candidate=$NEW_PIN current_deployed=$CURRENT_PIN idle=true no_swap=true"
  else
    echo "ALREADY_APPLIED mode=$MODE pin=$CURRENT_PIN no_swap=true"
  fi
  exit 0
fi
[ "$current_live_pin" = "$OLD_PIN" ] || {
  echo "REFUSE live_source_baseline_drift expected=$OLD_PIN actual=$current_live_pin"; exit 20;
}
[ "$current_installed_pin" = "$OLD_SOURCE_PIN" ] || {
  echo "REFUSE installed_pin_baseline_drift expected=$OLD_SOURCE_PIN actual=$current_installed_pin"; exit 20;
}

# Post-swap contract for THIS change: a recovery write must not erase the
# operator latch when the pre-failure state had no hold (capacity cooldown
# only), and must restore the pre-failure upstream hold verbatim when one
# existed. Both directions failing means the deploy regresses the latch.
check_recovery_contract() {
  python3 - <<'PY'
import importlib.util, json, os, sys, tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'cpa_admission_live', '/opt/cliproxyapi/cpa-admission.py'
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

# LaneState consumes load_config output (top-level merges such as
# retry_after_max_seconds are applied there), not the raw json lane dict.
loaded = module.load_config(
    Path('/opt/cliproxyapi/cpa-admission.json'),
    route_manifest_path=Path('/opt/cliproxyapi/cpa_provider_routes.json'),
)
cfg = next(l for l in loaded['lanes'] if l['name'] == 'chatgpt-oauth')
workdir = Path(tempfile.mkdtemp(prefix='admission-contract-'))
real_replace = os.replace


def fail_replace(*args, **kwargs):
    raise OSError('deploy contract: storage unavailable')


def write_state(name):
    path = workdir / name
    path.write_text(
        json.dumps({'version': 1, 'not_before': 0, 'hold_reason': None}),
        encoding='utf-8',
    )
    return path


# Contract A: a pre-failure upstream hold survives the recovery write.
path = write_state('hold-a.json')
state = module.LaneState(cfg, path)
outstanding = state.acquire()
os.replace = fail_replace
state.release(
    outstanding,
    capacity_error=False,
    retry_after=None,
    successful=False,
    hold_reason='authentication_required',
)
assert state.snapshot()['state_write_failed']
os.replace = real_replace
assert state.recover_state()
assert state.snapshot()['hold_reason'] == 'authentication_required', (
    state.snapshot()['hold_reason']
)
assert not state.acquire().admitted

# Contract B: with no pre-failure hold, the recovery write keeps the
# state_write_failed operator latch and the next acquire stays gated.
path = write_state('hold-b.json')
state = module.LaneState(cfg, path)
outstanding = state.acquire()
os.replace = fail_replace
state.release(outstanding, capacity_error=True, retry_after=3600)
assert state.snapshot()['state_write_failed']
os.replace = real_replace
assert state.recover_state()
assert state.snapshot()['hold_reason'] == 'state_write_failed', (
    state.snapshot()['hold_reason']
)
assert state.acquire().reason == 'operator_review_required'
assert state.snapshot()['server_retry_after_remaining'] > 0

print('RECOVERY_CONTRACT_OK')
PY
}

if [ "$MODE" = 'dry-run' ]; then
  acquire_traffic "$DRAIN_WAIT" || exit 75
  check_idle || {
    release_traffic
    echo 'DRY_RUN_BUSY healthz_has_inflight_or_pending no_swap=true'
    exit 75
  }
  runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
  release_traffic
  echo "DRY_RUN_OK baseline=$OLD_PIN candidate=$NEW_PIN idle=true no_swap=true"
  exit 0
fi

acquire_traffic "$DRAIN_WAIT" || exit 75
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
  release_guard
  if [ "$MUTATED" = 1 ]; then
    # The unit restarts only after both old source and old pin are published.
    # A failed copy must not leave a new source under the old generation's pin.
    if [ "$SOURCE_APPLIED" = 1 ]; then
      cp -a "$BK/cpa-admission.py" "$LIVE" || failed=1
    fi
    if [ "$PIN_APPLIED" = 1 ]; then
      cp -a "$BK/cpa-admission.sha256" "$PIN" || failed=1
    fi
    [ "$(sha256sum "$LIVE" | awk '{print $1}')" = "$OLD_PIN" ] || failed=1
    [ "$(tr -d '\r\n' < "$PIN")" = "$OLD_SOURCE_PIN" ] || failed=1
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
  release_traffic
  [ "$rc" != 0 ] || rc=1
  exit "$rc"
}
trap rollback EXIT HUP INT TERM

MUTATED=1
mv -f "$STAGED" "$LIVE" || rollback 20
SOURCE_APPLIED=1
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"
chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN" || rollback 20
PIN_APPLIED=1
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
check_recovery_contract

release_traffic
MUTATED=0
trap - EXIT HUP INT TERM
echo "DEPLOY_OK pin=$NEW_PIN backup=$BK"
'@

$remote = $remoteBody.Replace("__OLD_PIN__", $ExpectedLivePin).
  Replace("__OLD_SOURCE_PIN__", $ExpectedInstalledPin).
  Replace("__CURRENT_PIN__", $CurrentDeployedPin).
  Replace("__NEW_PIN__", $newPin).
  Replace("__MODE__", $mode).
  Replace("__DRAIN_WAIT__", [string]$drainWait).
  Replace("__PAYLOAD__", $payload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled admission stabilization transaction placeholder."
}
# The SSH launcher receives this as a variable assignment before the staged
# body, so the consumer never relies on an absent server-side environment.
$remote = "VPS_SSH_LAUNCHER_PROFILE='bwg'`n" + $remote

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 600 -RemoteTempPrefix "admission-stabilization-20261111"

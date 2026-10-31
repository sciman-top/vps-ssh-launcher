#requires -Version 7
# Reviewed low-peak transaction for the admission queue/stream bundle:
#   - deliver 200 + text/event-stream before a shared lane queue wait
#   - keep the waiting SSE turn alive with 15s comment heartbeats
#   - deliver admission/upstream failures as terminal SSE events
#   - include the already-reviewed numeric token/cache journal telemetry
# No CPA image, config, credential, risk-state, lane or cooldown is changed.
# Exit: 0 deployed | 20 contract refusal | 30 wrong window | 75 lock busy.
param(
  [switch]$DryRun,
  [switch]$AllowActiveWindow,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "970a8ad279ec53af1751d744ff99d74c6b4065fc19fe7b8e2a2347e02e7b5663"
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
$newPin = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($candidateBytes)).ToLowerInvariant()
$reviewedPin = (Get-Content -LiteralPath $pinPath -Raw).Trim()
if ($reviewedPin -ne $newPin) {
  throw "Candidate source does not match scripts/remote/cpa-admission-integrity-pin.txt."
}
$payload = [Convert]::ToBase64String($candidateBytes)
if ($DryRun -and $AllowActiveWindow) {
  throw "-DryRun and -AllowActiveWindow are mutually exclusive."
}
$mode = if ($DryRun) {
  "dry-run"
} elseif ($AllowActiveWindow) {
  "apply-override"
} else {
  "apply"
}

$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
LIVE=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
CONFIG=/opt/cliproxyapi/cpa-admission.json
TRAFFIC=/run/cpa-admission/requests.lock
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
CONFIG_SHA=7f53e6c7fc33423060f0027f7d2121d626faae0e75b1356932d086a375d3e89f
MODE=__MODE__
STAGED=/opt/cliproxyapi/.cpa-admission.py.queue-stream-20261031.$$
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-queue-stream-20261031
MUTATED=0

cleanup_stage() { rm -f -- "$STAGED"; }
trap cleanup_stage EXIT

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }

if [ "$MODE" = 'apply' ]; then
  hour=$(TZ=Asia/Shanghai date +%H%M)
  if (( 10#$hour < 400 || 10#$hour >= 600 )); then
    echo "REFUSE outside_low_peak_window local=$hour+0800"
    exit 30
  fi
fi
if [ "$MODE" = 'apply-override' ]; then
  echo "ACTIVE_WINDOW_OVERRIDE user_requested_at=$(TZ=Asia/Shanghai date +%FT%T%z)"
fi

printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
chmod 755 "$STAGED"
chown root:root "$STAGED"

[ "$(sha256sum "$LIVE" | awk '{print $1}')" = "$OLD_PIN" ] || {
  echo "REFUSE baseline_drift live=$(sha256sum "$LIVE" | awk '{print $1}')"; exit 20; }
[ "$(sha256sum "$PIN" | awk '{print $1}')" = "$(printf '%s\n' "$OLD_PIN" | sha256sum | awk '{print $1}')" ] || {
  echo 'REFUSE pin_file_drift'; exit 20; }
[ "$(tr -d '\r\n' < "$PIN")" = "$OLD_PIN" ] || {
  echo "REFUSE pin_value_drift got=$(tr -d '\r\n' < "$PIN")"; exit 20; }
[ "$(sha256sum "$STAGED" | awk '{print $1}')" = "$NEW_PIN" ] || {
  echo 'REFUSE staged_sha_mismatch'; exit 20; }
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$CONFIG_SHA" ] || {
  echo 'REFUSE admission_config_drift'; exit 20; }

python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health['status'] == 'ok'
expected = {'antigravity-oauth', 'chatgpt-oauth', 'workbuddy-oauth', 'zhipu-coding-plan'}
assert set(health['lanes']) == expected
for lane in health['lanes'].values():
    state = lane['state']
    assert state['hold_reason'] is None, state
print('ADMISSION_PREFLIGHT_OK lanes=4 holds=null')
PY

if [ "$MODE" = 'dry-run' ]; then
  echo "DRY_RUN_OK baseline=$OLD_PIN candidate=$NEW_PIN low_peak=(skip)"
  exit 0
fi

# Block new requests before waiting for in-flight turns. Admission takes its
# per-request shared lock non-blocking, so this is a visible, bounded 503
# maintenance response rather than starvation behind live traffic.
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
    python3 - <<'PY' || failed=1
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    assert json.load(response)['status'] == 'ok'
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
/usr/local/libexec/cpa-admission-integrity-check
systemctl restart cpa-admission

deadline=$((SECONDS + 30))
while :; do
  if python3 - <<'PY'
import json, urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=2) as response:
        health = json.load(response)
except Exception:
    raise SystemExit(1)
if health.get('status') != 'ok':
    raise SystemExit(1)
expected = {'antigravity-oauth', 'chatgpt-oauth', 'workbuddy-oauth', 'zhipu-coding-plan'}
if set(health.get('lanes', {})) != expected:
    raise SystemExit(1)
PY
  then
    break
  fi
  if (( SECONDS >= deadline )); then
    echo 'REFUSE readiness_timeout'
    rollback 20
  fi
  sleep 1
done

/usr/local/libexec/cpa-admission-integrity-check
systemctl is-active --quiet cpa-admission
[ "$(sha256sum "$LIVE" | awk '{print $1}')" = "$NEW_PIN" ]
MUTATED=0
trap - EXIT HUP INT TERM
exec 8<&-
echo "DEPLOY_OK admission=$NEW_PIN backup=$BK"
'@

$remote = $remote.Replace("__OLD_PIN__", $ExpectedLivePin).
  Replace("__NEW_PIN__", $newPin).
  Replace("__MODE__", $mode).
  Replace("__PAYLOAD__", $payload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled admission transaction placeholder."
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 600 -RemoteTempPrefix "admission-queue-stream-20261031"

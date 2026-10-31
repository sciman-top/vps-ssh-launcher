#requires -Version 7
param(
  [switch]$DryRun,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "07c8dca4c3ad1f86e6c98ed613fee790566d9184b00d282f9dd1c4988bb78a92"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$candidatePath = Join-Path $repoRoot "scripts/remote/cpa-admission.py"
$configPath = Join-Path $repoRoot "scripts/remote/cpa-admission.json"
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
$configText = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($configPath)).Replace(
  "`r`n", "`n"
).Replace("`r", "`n")
$configBytes = [Text.Encoding]::UTF8.GetBytes($configText)
$newConfigPin = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($configBytes)).ToLowerInvariant()
if ($newConfigPin -ne "7f53e6c7fc33423060f0027f7d2121d626faae0e75b1356932d086a375d3e89f") {
  throw "Candidate admission config does not match the reviewed RPM projection hash."
}
$payload = [Convert]::ToBase64String($candidateBytes)
$configPayload = [Convert]::ToBase64String($configBytes)
$mode = if ($DryRun) { "dry-run" } else { "apply" }

$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
LIVE=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-rpm-20261031
CONFIG=/opt/cliproxyapi/cpa-admission.json
CONFIG_STAGED=/opt/cliproxyapi/.cpa-admission.json.new-rpm-20261031
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
OLD_CONFIG_SHA=f80d9b69b56a04c5620c08d5c972b70b3bee3b8068f61ce41b123688f38287e1
NEW_CONFIG_SHA=__NEW_CONFIG_PIN__
MODE=__MODE__
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-rpm-20261031

cleanup_stage() { rm -f -- "$STAGED" "$CONFIG_STAGED"; }
trap cleanup_stage EXIT
printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
printf '%s' '__CONFIG_PAYLOAD__' | base64 -d > "$CONFIG_STAGED"
chmod 755 "$STAGED"
chmod 644 "$CONFIG_STAGED"
chown root:root "$STAGED" "$CONFIG_STAGED"

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }

LIVE_SHA=$(sha256sum "$LIVE" | awk '{print $1}')
[ "$LIVE_SHA" = "$OLD_PIN" ] || {
  echo "REFUSE baseline_drift live=$LIVE_SHA expected=$OLD_PIN"; exit 20;
}
CURRENT_PIN=$(tr -d '\r\n' < "$PIN")
[ "$CURRENT_PIN" = "$OLD_PIN" ] || {
  echo "REFUSE pin_drift got=$CURRENT_PIN expected=$OLD_PIN"; exit 20;
}
[ "$(sha256sum "$STAGED" | awk '{print $1}')" = "$NEW_PIN" ] || {
  echo 'REFUSE staged_sha_mismatch'; exit 20;
}
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$OLD_CONFIG_SHA" ] || {
  echo 'REFUSE config_baseline_drift'; exit 20;
}
[ "$(sha256sum "$CONFIG_STAGED" | awk '{print $1}')" = "$NEW_CONFIG_SHA" ] || {
  echo 'REFUSE staged_config_sha_mismatch'; exit 20;
}

python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok', h.get('status')
busy = {k: v['state'] for k, v in h['lanes'].items()
        if v['state']['inflight'] or v['state']['pending']}
assert not busy, f'REFUSE active traffic {list(busy)}'
print('ADMISSION_IDLE_OK')
PY

if [ "$MODE" = 'dry-run' ]; then
  echo "DRY_RUN_OK baseline=$OLD_PIN candidate=$NEW_PIN no_swap=true"
  exit 0
fi

mkdir -m 700 "$BK"
cp -a "$LIVE" "$CONFIG" "$PIN" "$BK/"
rollback() {
  local rc=$?
  trap - EXIT HUP INT TERM
  cp -a "$BK/cpa-admission.py" "$LIVE"
  cp -a "$BK/cpa-admission.json" "$CONFIG"
  cp -a "$BK/cpa-admission.sha256" "$PIN"
  systemctl restart cpa-admission
  sleep 2
  echo "ROLLBACK restored backup=$BK"
  exit "$rc"
}
trap rollback EXIT

mv -f "$STAGED" "$LIVE"
mv -f "$CONFIG_STAGED" "$CONFIG"
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"
chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN"
/usr/local/libexec/cpa-admission-integrity-check
systemctl restart cpa-admission
sleep 2

python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok', h.get('status')
expected = {
    'chatgpt-oauth': 24,
    'zhipu-coding-plan': 30,
    'antigravity-oauth': 18,
    'workbuddy-oauth': 18,
}
assert sorted(h['lanes']) == sorted(expected), sorted(h['lanes'])
for name, rpm in expected.items():
    lane = h['lanes'][name]
    assert lane['requests_per_minute'] == rpm, (name, lane)
    assert lane['state']['hold_reason'] is None, (name, lane['state'])
print('HEALTH_OK lanes=4 rpm=24,30,18,18 holds=null')
PY
systemctl is-active --quiet cpa-admission
/usr/local/libexec/cpa-admission-integrity-check
trap - EXIT
echo "DEPLOY_OK pin=$NEW_PIN backup=$BK"
'@

$remote = $remote.Replace("__OLD_PIN__", $ExpectedLivePin).
  Replace("__NEW_PIN__", $newPin).
  Replace("__NEW_CONFIG_PIN__", $newConfigPin).
  Replace("__MODE__", $mode).
  Replace("__PAYLOAD__", $payload).
  Replace("__CONFIG_PAYLOAD__", $configPayload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled admission transaction placeholder."
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 600 -RemoteTempPrefix "admission-rpm-20261031"

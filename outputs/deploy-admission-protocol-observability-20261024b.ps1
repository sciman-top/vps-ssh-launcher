param([string]$Config = "", [string]$Profile = "bwg")
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
Initialize-WindowsProcessEnvironment
$py = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$cfg = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
$source = Join-Path $repoRoot "scripts\remote\cpa-admission.py"
$pinPath = Join-Path $repoRoot "scripts\remote\cpa-admission-integrity-pin.txt"
$oldPin = ((git -C $repoRoot show 833aa74^:scripts/remote/cpa-admission-integrity-pin.txt) | Select-Object -First 1).Trim() # pre-rotation generation pin (remote live baseline); HEAD already carries the new pin via 833aa74
$newPin = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
if ((Get-Content -LiteralPath $pinPath -Raw).Trim() -ne $newPin) { throw "local admission pin mismatch" }
$payload = [Convert]::ToBase64String([IO.File]::ReadAllBytes($source))
$remote = @'
set -Eeuo pipefail
umask 077
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo MAINTENANCE_BUSY; exit 75; }
LIVE=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-protocol-observability
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-protocol-observability
LIVE_SHA=$(sha256sum "$LIVE" | awk '{print $1}')
[ "$LIVE_SHA" = "$OLD_PIN" ] || { echo "REFUSE baseline_drift live=$LIVE_SHA"; exit 20; }
lane_holds() {
  python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health['status'] == 'ok', health
for name, lane in sorted(health['lanes'].items()):
    print(f"{name}={lane['state'].get('hold_reason')}")
PY
}
PRE_HOLDS=$(lane_holds)
exec 8< /run/cpa-admission/requests.lock || {
  echo "REFUSE admission_not_running (requests.lock absent)"
  exit 20
}
if ! flock -w 60 -x 8; then
  echo "DRAIN_TIMEOUT requests.lock held shared for 60s"
  exit 76
fi
sleep 1
python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok', h
assert all(
    not lane['state']['inflight'] and not lane['state']['pending']
    for lane in health['lanes'].values()
), 'REFUSE active traffic survived the exclusive drain lock'
print('ADMISSION_IDLE_OK')
PY
rm -f "$STAGED"
printf %s '__PAYLOAD__' | base64 -d > "$STAGED"
chmod 755 "$STAGED"; chown root:root "$STAGED"
[ "$(sha256sum "$STAGED" | awk '{print $1}')" = "$NEW_PIN" ] || { echo REFUSE staged_sha_mismatch; exit 20; }
mkdir -m 700 "$BK"
cp -a "$LIVE" "$PIN" "$BK/"
rollback() {
  rc=$?
  if [ $rc -ne 0 ]; then
    cp -a "$BK/cpa-admission.py" "$LIVE"
    cp -a "$BK/cpa-admission.sha256" "$PIN"
    rm -f "$STAGED"
    systemctl restart cpa-admission
    echo "ROLLBACK restored backup=$BK"
  fi
  exit $rc
}
trap rollback EXIT
mv -f "$STAGED" "$LIVE"
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"; chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN"
/usr/local/libexec/cpa-admission-integrity-check
systemctl restart cpa-admission
sleep 2
python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok', h
assert sorted(h['lanes']) == ['antigravity-oauth', 'chatgpt-oauth', 'workbuddy-oauth', 'zhipu-coding-plan']
print('HEALTH_OK lanes=%d retired_readers=%s' % (len(h['lanes']), h.get('retired_readers', 'unknown')))
PY
trap - EXIT
exec 8>&-
POST_HOLDS=$(lane_holds)
NEW_HOLDS=$(comm -13 <(sort <<<"$PRE_HOLDS") <(sort <<<"$POST_HOLDS") || true)
if [ -n "$NEW_HOLDS" ]; then
  echo "UNVERIFIED: lane hold appeared across the restart: ${NEW_HOLDS//$'\n'/, }"
  exit 10
fi
echo "OK deployed pin=$NEW_PIN backup=$BK"
'@
$remote = $remote.Replace('__OLD_PIN__', $oldPin).Replace('__NEW_PIN__', $newPin).Replace('__PAYLOAD__', $payload)
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $repoRoot -Config $cfg -Profile $Profile -Command $remote -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300 -RemoteTempPrefix "cpa-admission-observability"

param([string]$Config = "", [string]$Profile = "bwg")
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
Initialize-WindowsProcessEnvironment
$py = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$cfg = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
$source = Join-Path $repoRoot "scripts\remote\cpa-admission.py"
$pinPath = Join-Path $repoRoot "scripts\remote\cpa-admission-integrity-pin.txt"
$oldPin = ((git -C $repoRoot show HEAD:scripts/remote/cpa-admission-integrity-pin.txt) | Select-Object -First 1).Trim()
$newPin = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
if ((Get-Content -LiteralPath $pinPath -Raw).Trim() -ne $newPin) { throw "local admission pin mismatch" }
$payload = [Convert]::ToBase64String([IO.File]::ReadAllBytes($source))
$remote = @'
set -Eeuo pipefail
umask 077
LIVE=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-protocol-observability
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-protocol-observability
LIVE_SHA=$(sha256sum "$LIVE" | awk '{print $1}')
[ "$LIVE_SHA" = "$OLD_PIN" ] || { echo "REFUSE baseline_drift live=$LIVE_SHA"; exit 20; }
python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok', h
busy = {k: v['state'] for k, v in h['lanes'].items() if v['state']['inflight'] or v['state']['pending']}
assert not busy, f'REFUSE active traffic {list(busy)}'
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
echo "OK deployed pin=$NEW_PIN backup=$BK"
'@
$remote = $remote.Replace('__OLD_PIN__', $oldPin).Replace('__NEW_PIN__', $newPin).Replace('__PAYLOAD__', $payload)
Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $repoRoot -Config $cfg -Profile $Profile -Command $remote -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300 -RemoteTempPrefix "cpa-admission-observability"

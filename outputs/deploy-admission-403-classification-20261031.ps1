#requires -Version 7
# Authorized admission-only deploy: 403 classification split.
#   * edge/WAF 403 page (non-JSON body) on chatgpt/workbuddy -> no durable
#     operator hold; the lane cools through its normal ladder.
#   * structured 11140 / permission_error / auth 403 keeps its hold.
#   * an implausible (>2^31 s) Retry-After clamps to the reviewed ladder cap on
#     every lane except claude-oauth, which keeps its review hold.
#   * every durable hold now logs lane_hold_latched with lane/reason/source.
# Entry returns 503 during the window so no in-flight turn is killed.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$candidatePath = Join-Path $repoRoot 'scripts/remote/cpa-admission.py'
$pinPath = Join-Path $repoRoot 'scripts/remote/cpa-admission-integrity-pin.txt'
$candidateText = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($candidatePath)).Replace(
  "`r`n", "`n"
).Replace("`r", "`n")
$candidateBytes = [Text.Encoding]::UTF8.GetBytes($candidateText)
$newPin = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($candidateBytes)).ToLowerInvariant()
$reviewedPin = (Get-Content -LiteralPath $pinPath -Raw).Trim()
if ($reviewedPin -ne $newPin) {
  throw 'Candidate source does not match scripts/remote/cpa-admission-integrity-pin.txt.'
}
$payload = [Convert]::ToBase64String($candidateBytes)

$remote = @'
set -Eeuo pipefail
umask 077
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
LIVE=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-403-20261031
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
NGX=/etc/nginx/conf.d/cpa-gateway.conf
NGX_BK=/root/cpa-gateway.conf.pre-403-$TS
NGX_SHA_BEFORE=$(sha256sum "$NGX" | cut -d ' ' -f 1)
LOG=/opt/cliproxyapi/canary-plugin-0982.log
BK=/opt/cliproxyapi/backups/$TS-admission-403-classification

cp -a "$NGX" "$NGX_BK"
[ -s "$NGX_BK" ] || { echo 'ENTRY_BACKUP_FAILED'; exit 20; }
printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
chmod 755 "$STAGED"
chown root:root "$STAGED"
cleanup_stage() { rm -f -- "$STAGED"; }
trap cleanup_stage EXIT

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

all_idle() {
  python3 - <<'PY'
import json
import urllib.request

h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
print(sum(v['state']['inflight'] for v in h['lanes'].values()))
PY
}

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
log "PREFLIGHT live=$OLD_PIN candidate=$NEW_PIN"

NGX_ON=0
MUTATED=0
restore_entry() {
  if [ "$NGX_ON" = 1 ] && [ -s "$NGX_BK" ]; then
    cp -a "$NGX_BK" "$NGX"
    nginx -t && nginx -s reload
    NGX_ON=0
  fi
}
rollback() {
  local code=${1:-$?}
  trap - ERR HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    cp -a "$BK/cpa-admission.py" "$LIVE" || log "ROLLBACK_COPY_FAILED backup=$BK"
    cp -a "$BK/cpa-admission.sha256" "$PIN" || log "ROLLBACK_PIN_FAILED backup=$BK"
    systemctl restart cpa-admission || true
    sleep 3
    if systemctl is-active --quiet cpa-admission &&
       /usr/local/libexec/cpa-admission-integrity-check; then
      log "ROLLBACK restored=$OLD_PIN backup=$BK"
    else
      log "ROLLBACK_UNVERIFIED backup=$BK"
    fi
  fi
  restore_entry
  log "ROLLBACK_DONE code=$code"
  exit "$code"
}
trap rollback ERR HUP INT TERM

python3 - "$NGX" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding='utf-8').split('\n')
for index, line in enumerate(lines):
    if line.startswith('    location ~') and '/v1/' in line:
        lines.insert(index + 1, '        return 503; # cpa maintenance window')
        path.write_text('\n'.join(lines), encoding='utf-8')
        raise SystemExit(0)
raise SystemExit('entry location anchor not found')
PY
nginx -t
nginx -s reload
NGX_ON=1
log 'MAINTENANCE_GATE_ON entry=503'

DRAIN_DEADLINE=$((SECONDS + 240))
while :; do
  if [ "$(all_idle)" = 0 ]; then
    log 'INFLIGHT_DRAINED'
    break
  fi
  if [ "$SECONDS" -ge "$DRAIN_DEADLINE" ]; then
    log 'DEFER in-flight did not drain within 240s'
    exit 75
  fi
  sleep 1
done

mkdir -m 700 "$BK"
cp -a "$LIVE" "$PIN" "$BK/"
MUTATED=1
systemctl stop cpa-admission
mv -f "$STAGED" "$LIVE"
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"
chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN"
/usr/local/libexec/cpa-admission-integrity-check
systemctl start cpa-admission
for _ in 1 2 3 4 5 6; do
  if curl -sf --max-time 3 http://127.0.0.1:8318/healthz >/dev/null; then
    break
  fi
  sleep 3
done
curl -sf --max-time 3 http://127.0.0.1:8318/healthz >/dev/null || {
  log 'REFUSE admission_not_ready_after_start'
  exit 24
}

python3 - <<'PY'
import json
import urllib.request

h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok', h.get('status')
expected = {'chatgpt-oauth', 'zhipu-coding-plan', 'antigravity-oauth', 'workbuddy-oauth'}
assert set(h['lanes']) == expected, sorted(h['lanes'])
holds = {name: lane['state']['hold_reason'] for name, lane in h['lanes'].items()}
assert all(reason is None for reason in holds.values()), holds
print('HEALTH_OK lanes=4 holds=null')
PY
systemctl is-active --quiet cpa-admission
/usr/local/libexec/cpa-admission-integrity-check
log "ADMISSION_DEPLOYED pin=$NEW_PIN"

restore_entry
if ! sha256sum "$NGX" | grep -qF "$NGX_SHA_BEFORE"; then
  log "REFUSE entry_config_drift expected=$NGX_SHA_BEFORE"
  exit 23
fi
log 'MAINTENANCE_GATE_OFF entry=restored'
trap - ERR HUP INT TERM
log "DEPLOY_OK pin=$NEW_PIN backup=$BK"
'@

$remote = $remote.Replace('__OLD_PIN__', '3af47e1332750f55fb15caa2979665101bc58397530b13c66f4b3c191023599d').
  Replace('__NEW_PIN__', $newPin).
  Replace('__PAYLOAD__', $payload)
if ($remote -match '__[A-Z_]+__') {
  throw 'Unfilled admission transaction placeholder.'
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile 'bwg' -Command $remote `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 600 -RemoteTempPrefix 'admission-403-20261031'

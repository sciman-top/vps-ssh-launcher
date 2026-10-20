#!/usr/bin/env bash
# Reviewed deploy transaction: workbuddy 429-no-Retry-After no longer pre-arms
# the operator hold; model-side "14003 Model busy" waves ride the cooldown
# ladder instead (user-approved structural change, 2026-10-20). Atomically
# rotates cpa-admission.py + its integrity pin as one pair, then restarts
# cpa-admission. Account-shaped bodies keep holding via the stream-signal
# path; the claude-oauth branch is unchanged (#6700 conservative).
# Exit: 0 deployed | 20 contract refusal | 75 lock busy.
set -Eeuo pipefail
umask 077
LIVE=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-14003-ladder
OLD_PIN=0207d7b4b9f06a444be9988c6e764d2e7dcb6bf66d0c72bef162b6b9ef27ad4d
NEW_PIN=b0e7ec31f09ac53505297a73cdb1aad8261a6e881897b86e264360a413401803
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-14003-ladder

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }

LIVE_SHA=$(sha256sum "$LIVE" | awk '{print $1}')
[ "$LIVE_SHA" = "$OLD_PIN" ] || { echo "REFUSE baseline_drift live=$LIVE_SHA"; exit 20; }
[ "$(sha256sum "$STAGED" | awk '{print $1}')" = "$NEW_PIN" ] || {
  echo 'REFUSE staged_sha_mismatch'; exit 20; }
python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok'
busy = {k: v['state'] for k, v in h['lanes'].items()
        if v['state']['inflight'] or v['state']['pending']}
assert not busy, f'REFUSE active traffic {list(busy)}'
print('ADMISSION_IDLE_OK')
PY

mkdir -m 700 "$BK"
cp -a "$LIVE" "$PIN" "$BK/"
trap '
  cp -a "$BK/cpa-admission.py" "$LIVE"
  cp -a "$BK/cpa-admission.sha256" "$PIN"
  systemctl restart cpa-admission
  sleep 2
  echo "ROLLBACK restored backup=$BK"
' EXIT

chmod 755 "$STAGED"; chown root:root "$STAGED"
mv -f "$STAGED" "$LIVE"
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"; chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN"
/usr/local/libexec/cpa-admission-integrity-check || {
  echo 'REFUSE integrity_check_failed'; exit 20; }
systemctl restart cpa-admission
sleep 2
python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok', h['status']
lanes = sorted(h['lanes'])
assert lanes == ['antigravity-oauth', 'chatgpt-oauth', 'workbuddy-oauth',
                 'zhipu-coding-plan'], lanes
wb = h['lanes']['workbuddy-oauth']['state']
print('HEALTH_OK lanes=%s workbuddy_hold=%s' % (len(lanes), wb['hold_reason']))
PY
journalctl -u cpa-admission --since "30 seconds ago" --no-pager | grep -E "ready" | tail -1
trap - EXIT
echo "OK deployed pin=$NEW_PIN backup=$BK"

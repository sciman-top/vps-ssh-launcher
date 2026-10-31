#!/usr/bin/env bash
# Reviewed deploy transaction: admission startup stale write-ahead marker
# auto-recovery (LaneState load clears hold_reason=unverified_request into one
# 90s bounded verification window instead of an indefinite operator-review
# latch; 2026-10-20/21/31 restart latches, zero real upstream signals).
# User-authorized 2026-10-31 round. Atomically rotates cpa-admission.py and
# its integrity pin as one pair, then restarts cpa-admission. No config,
# credential, risk-state, lane or cooldown change of any kind.
# Exit: 0 deployed | 20 contract refusal | 75 lock busy.
set -Eeuo pipefail
umask 077
LIVE=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-stale-marker-20261031
OLD_PIN=12d0a356dab11cb187800e2bafb6991f12b5ee64fe09e278d80fba6f36b38e7d
NEW_PIN=07c8dca4c3ad1f86e6c98ed613fee790566d9184b00d282f9dd1c4988bb78a92
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-stale-marker-20261031

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
holds = {k: h['lanes'][k]['state'].get('hold_reason') for k in lanes}
print('HEALTH_OK lanes=4 holds=%s' % holds)
PY
journalctl -u cpa-admission --since "30 seconds ago" --no-pager | grep -E "ready" | tail -1
journalctl -u cpa-admission --since "30 seconds ago" --no-pager | grep -E "stale_write_ahead_cleared" | tail -2 || true
trap - EXIT
echo "OK deployed pin=$NEW_PIN backup=$BK"

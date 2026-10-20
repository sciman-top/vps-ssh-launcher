#!/usr/bin/env bash
# Reviewed deploy transaction: workbuddy stream-signal classifier recognizes
# CPA-wrapped "14003 Model busy" (wrapper type rate_limit_error quoting
# upstream code 14003) and leaves it to the header-site ladder instead of
# re-arming the durable hold. 2026-10-21: the first post-43c7334 wave pinned
# the lane again because the wrapper label beat the header change. Account-
# shaped bodies (quoted 6003/6004, bare rate_limit_error, 11140, auth) keep
# holding; the claude branch is unchanged (1dcbb68 conservative).
# Exit: 0 deployed | 20 contract refusal | 75 lock busy.
set -Eeuo pipefail
umask 077
LIVE=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED=/opt/cliproxyapi/.cpa-admission.py.new-14003-wrapper
OLD_PIN=b0e7ec31f09ac53505297a73cdb1aad8261a6e881897b86e264360a413401803
NEW_PIN=19d64361dff6593343f48593d28de825983a80aa84639b99e951740a14c1ba3c
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-14003-wrapper

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

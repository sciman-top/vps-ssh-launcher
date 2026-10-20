#!/usr/bin/env bash
# Release workbuddy-oauth hold retry_window_review_required (2026-10-20), per
# docs/runbooks/cpa-gateway.md "OAuth 持久化风险状态的人工恢复": idle window,
# stop admission, back up state file, hold_reason -> null (not_before untouched),
# preserve owner=nobody/nogroup mode=0600, restart, verify /healthz.
# Arming cause: upstream 14003 capacity 429 carried an implausible retry window
# while the durable lane was in strict_backoff; capacity-type, not account risk.
# Exit: 0 released | 20 refusal | 75 lock busy.
set -Eeuo pipefail
umask 077
STATE=/var/lib/cpa-admission/workbuddy-risk.json
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-workbuddy-hold-release

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }

python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok'
busy = {k: v['state'] for k, v in h['lanes'].items()
        if v['state']['inflight'] or v['state']['pending']}
assert not busy, f'REFUSE active traffic {list(busy)}'
holds = {k: v['state'].get('hold_reason') for k, v in h['lanes'].items()}
print('HOLDS_BEFORE', holds)
assert holds.get('workbuddy-oauth'), 'REFUSE no workbuddy hold present'
print('ADMISSION_IDLE_OK')
PY

systemctl stop cpa-admission
trap 'systemctl start cpa-admission' EXIT
mkdir -m 700 "$BK"
cp -a "$STATE" "$BK/"
python3 - "$STATE" <<'PY'
import json, os, pwd, grp, sys
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text(encoding='utf-8'))
print('STATE_BEFORE', d)
assert set(d) <= {'version', 'not_before', 'hold_reason'}, 'REFUSE unexpected state shape'
d['hold_reason'] = None
tmp = p.with_name(p.name + '.tmp')
tmp.write_text(json.dumps(d, separators=(',', ':')) + '\n', encoding='utf-8')
os.chmod(tmp, 0o600)
os.chown(tmp, pwd.getpwnam('nobody').pw_uid, grp.getgrnam('nogroup').gr_gid)
os.replace(tmp, p)
print('STATE_AFTER', json.loads(p.read_text(encoding='utf-8')))
PY
systemctl start cpa-admission
trap - EXIT
sleep 2
python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok'
wb = h['lanes']['workbuddy-oauth']['state']
print('WORKBUDDY_AFTER', {k: wb[k] for k in
      ('hold_reason', 'strict_backoff', 'inflight', 'pending', 'state_write_failed')})
assert wb['hold_reason'] is None, 'hold still present'
print('HOLD_RELEASED_OK')
PY
ls -la /var/lib/cpa-admission/workbuddy-risk.json
journalctl -u cpa-admission --since "30 seconds ago" --no-pager | grep -E "ready|ERROR" | tail -3

#!/usr/bin/env bash
# Release the 2026-10-24 chatgpt-oauth unverified_request latch after the
# v8.0.41 canary restart. Follows docs/runbooks/cpa-gateway.md
# "OAuth 持久化风险状态的人工恢复": idle window, stop admission, back up state,
# clear only hold_reason (not_before untouched), preserve owner/mode, restart,
# then verify /healthz. Rollback is the timestamped backup in BACKUP.
# Exit: 0 released | 20 refusal | 75 maintenance lock busy.
set -Eeuo pipefail
umask 077
STATE=/var/lib/cpa-admission/chatgpt-risk.json
BACKUP_ROOT=/opt/cliproxyapi/backups
BACKUP="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%S.%NZ)-chatgpt-hold-release"

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo MAINTENANCE_BUSY; exit 75; }

python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health['status'] == 'ok'
lanes = health['lanes']
assert all(
    not lane['state']['inflight']
    and not lane['state']['pending']
    and not lane['state']['half_open_probe']
    for lane in lanes.values()
), 'REFUSE active traffic'
holds = {name: lane['state'].get('hold_reason') for name, lane in lanes.items()}
assert holds == {
    'chatgpt-oauth': 'unverified_request',
    'zhipu-coding-plan': None,
    'antigravity-oauth': None,
    'workbuddy-oauth': None,
}, f'REFUSE unexpected holds: {holds}'
print('HOLDS_BEFORE', json.dumps(holds, sort_keys=True))
print('ADMISSION_IDLE_OK')
PY

python3 - "$STATE" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
assert state == {
    'version': 1,
    'not_before': 0,
    'hold_reason': 'unverified_request',
}, state
print('STATE_PRECHECK', json.dumps(state, sort_keys=True))
PY

systemctl stop cpa-admission
trap 'systemctl start cpa-admission >/dev/null 2>&1 || true' EXIT
mkdir -m 700 "$BACKUP"
cp -a "$STATE" "$BACKUP/"

python3 - "$STATE" <<'PY'
import grp
import json
import os
import pwd
import sys
from pathlib import Path

path = Path(sys.argv[1])
state = json.loads(path.read_text(encoding='utf-8'))
assert state == {
    'version': 1,
    'not_before': 0,
    'hold_reason': 'unverified_request',
}, state
state['hold_reason'] = None
candidate = path.with_name(path.name + '.tmp')
with candidate.open('w', encoding='utf-8') as handle:
    json.dump(state, handle, separators=(',', ':'))
    handle.write('\n')
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(candidate, 0o600)
os.chown(
    candidate,
    pwd.getpwnam('nobody').pw_uid,
    grp.getgrnam('nogroup').gr_gid,
)
os.replace(candidate, path)
print('STATE_AFTER', json.dumps(state, sort_keys=True))
PY

systemctl start cpa-admission
sleep 2
python3 - <<'PY'
import grp
import json
import pwd
import urllib.request
from pathlib import Path

with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health['status'] == 'ok'
assert all(
    lane['state'].get('hold_reason') is None for lane in health['lanes'].values()
), health['lanes']
assert all(
    not lane['state']['inflight'] and not lane['state']['pending']
    for lane in health['lanes'].values()
), health['lanes']
st = Path('/var/lib/cpa-admission/chatgpt-risk.json').stat()
assert st.st_uid == pwd.getpwnam('nobody').pw_uid
assert st.st_gid == grp.getgrnam('nogroup').gr_gid
assert st.st_mode & 0o777 == 0o600
print('HEALTH_OK')
print('HOLDS_AFTER', {
    name: lane['state']['hold_reason']
    for name, lane in health['lanes'].items()
})
PY

echo "BACKUP=$BACKUP"
trap - EXIT

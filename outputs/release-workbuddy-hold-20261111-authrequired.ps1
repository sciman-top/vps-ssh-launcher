#requires -Version 7
# Authorized release of the workbuddy lane hold latched 2026-11-11T04:45:56Z
# as authentication_required (source=response_signal). Root cause chain
# (read-only forensics, see docs/change-evidence/20261111-bwg-workbuddy-hold-release.md):
# every workbuddy candidate went credit/rate dead overnight (INTL 6008
# "Credits exhausted" bare-429s + CN 6004 model-day windows with upstream
# reset timestamps), so the host handed the request to the OpenCode fallback,
# which returned a JSON 403 permission_error ("An active OpenCode Go
# subscription is required"). Admission classified that auth-class 403 per
# the 2026-10-31 403 layering and latched the whole lane. The hold reason
# (authentication_required) misattributes the oc fallback failure to wb
# credentials; the wb pool state is quota windows that auto-reset (first CN
# window resets 09:20:27 UTC). This transaction clears the hold after that
# reset so the lane resumes without another manual step; releasing earlier
# would deterministically re-latch via the same oc 403 path.
#
# Restart discipline per cpa-gateway.md (2026-10-24 drain contract): the
# requests.lock exclusive flock plus the all-lane idle assertion replace the
# older entry-503 shield, so no lane sees a fleet-wide 503 window. Backup,
# exact-reason assertion, atomic hold_reason=null, admission-only restart,
# health and lane-hold verification; rollback restores the backup only when
# the service fails to come back. Contract cloned verbatim from
# release-workbuddy-hold-20261107.ps1; only EXPECTED_REASON and identity
# strings change.
param(
  [switch]$ForceRestart
)

if ($ForceRestart) {
  throw "-ForceRestart is disabled; the transaction proves drain and health itself."
}

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
STATE=/var/lib/cpa-admission/workbuddy-risk.json
EXPECTED_REASON='authentication_required'
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK=/root/workbuddy-risk-backup-$TS.json
TRAFFIC=/run/cpa-admission/requests.lock
MUTATED=0

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
systemctl is-active --quiet cpa-admission || { echo 'ADMISSION_NOT_ACTIVE'; exit 1; }

python3 - "$STATE" "$EXPECTED_REASON" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert set(state) == {"version", "not_before", "hold_reason"}, sorted(state)
reason = state["hold_reason"]
assert reason == sys.argv[2], "REFUSE reason_drift got=%s" % reason
assert isinstance(state["not_before"], (int, float))
print("HOLD_PREFLIGHT_OK reason=%s not_before=%r" % (reason, state["not_before"]))
PY

cp -a "$STATE" "$BK"
chmod 600 "$BK"
sha256sum "$BK"
log "RELEASE_BACKUP state=$BK"

check_idle() {
  python3 - <<'PY'
import json, urllib.error, urllib.request
try:
    with urllib.request.urlopen(
        'http://127.0.0.1:8318/healthz', timeout=5
    ) as response:
        health = json.load(response)
except (OSError, urllib.error.URLError, ValueError) as exc:
    raise SystemExit('REFUSE healthz_unavailable: ' + str(exc))
assert health.get('status') == 'ok', 'REFUSE healthz_not_ok'
lanes = health.get('lanes')
expected = [
    'antigravity-oauth', 'chatgpt-oauth', 'workbuddy-oauth', 'zhipu-coding-plan'
]
if not isinstance(lanes, dict) or sorted(lanes) != expected:
    raise SystemExit('REFUSE healthz_lane_contract_missing')
for name, lane in lanes.items():
    state = lane.get('state') if isinstance(lane, dict) else None
    if not isinstance(state, dict):
        raise SystemExit('REFUSE healthz_state_missing lane=' + name)
    inflight = state.get('inflight')
    pending = state.get('pending')
    if type(inflight) is not int or type(pending) is not int:
        raise SystemExit('REFUSE healthz_traffic_fields_missing lane=' + name)
    if inflight != 0 or pending != 0:
        raise SystemExit(
            'REFUSE active_requests_not_drained lane=%s inflight=%d pending=%d'
            % (name, inflight, pending)
        )
print('ADMISSION_IDLE_OK')
PY
}

rollback() {
  local rc=${1:-$?} failed=0
  trap - EXIT HUP INT TERM
  # Restoring the durable backup re-latches the hold, so only do it when the
  # service did not come back: a failure after a healthy restart must not
  # undo the release.
  if [ "$MUTATED" = 1 ] && ! systemctl is-active --quiet cpa-admission; then
    cp -a "$BK" "$STATE" || failed=1
    systemctl start cpa-admission || failed=1
    sleep 2
    if [ "$failed" = 0 ]; then
      log "RELEASE_ROLLBACK restored_backup=$BK"
    else
      log "RELEASE_ROLLBACK_FAILED backup=$BK"
    fi
  fi
  exec 8<&- 2>/dev/null || true
  exit "$rc"
}
trap rollback EXIT HUP INT TERM

exec 8<"$TRAFFIC"
acquired=0
for attempt in $(seq 1 240); do
  if flock -x -w 1 8; then
    acquired=1
    break
  fi
  if [ $((attempt % 15)) -eq 0 ]; then
    log "DRAIN_WAIT attempt=$attempt live_streams_still_open=true"
  fi
done
if [ "$acquired" != 1 ]; then
  echo 'REFUSE live_traffic_active lock_not_acquired'
  exit 75
fi
log 'REQUEST_GUARD_EXCLUSIVE_OK'
check_idle

MUTATED=1
python3 - "$STATE" "$EXPECTED_REASON" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
state = json.loads(path.read_text(encoding="utf-8"))
assert set(state) == {"version", "not_before", "hold_reason"}, sorted(state)
assert state["hold_reason"] == sys.argv[2], "REFUSE reason_drift"
state["hold_reason"] = None
temporary = path.with_name(path.name + ".releasing")
temporary.write_text(json.dumps(state) + "\n", encoding="utf-8")
os.chmod(temporary, path.stat().st_mode & 0o777)
os.chown(temporary, path.stat().st_uid, path.stat().st_gid)
os.replace(temporary, path)
PY

systemctl restart cpa-admission

deadline=$((SECONDS + 30))
while :; do
  check_idle >/dev/null 2>&1 && break
  if (( SECONDS >= deadline )); then
    echo 'REFUSE readiness_timeout'
    rollback 20
  fi
  sleep 1
done

python3 - "$STATE" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert set(state) == {"version", "not_before", "hold_reason"}, sorted(state)
assert state["hold_reason"] is None, state["hold_reason"]

import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
lanes = health['lanes']
assert lanes['workbuddy-oauth']['state']['hold_reason'] is None, 'workbuddy still held'
for name in ('antigravity-oauth', 'chatgpt-oauth', 'zhipu-coding-plan'):
    reason = lanes[name]['state']['hold_reason']
    assert reason is None, 'NEW hold on %s: %s' % (name, reason)
print('LANE_HOLD_DELTA=none')
PY
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check

MUTATED=0
trap - EXIT HUP INT TERM
exec 8<&- 2>/dev/null || true
log "RELEASE_OK lane=workbuddy-oauth cleared=$EXPECTED_REASON backup=$BK"
'@

if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled release-workbuddy-hold transaction placeholder."
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900 -RemoteTempPrefix "release-workbuddy-hold-20261111"

#!/usr/bin/env bash
# One-shot, reviewed bwg admission code/pin transaction. Fill placeholders
# from the verified repository bytes before sending through the shared launcher.
set -Eeuo pipefail
umask 077
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'REFUSE maintenance_busy'; exit 75; }
LIVE=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
MODE=__MODE__
STAGED=$(mktemp /opt/cliproxyapi/.admission-disconnect.XXXXXXXX)
BK=
MUTATED=0
cleanup() {
  local code=$?
  trap - EXIT HUP INT TERM
  rm -f -- "$STAGED"
  if [[ "$code" != 0 && "$MUTATED" == 1 ]]; then
    if cp -a "$BK/cpa-admission.py" "$LIVE" &&
       cp -a "$BK/cpa-admission.sha256" "$PIN" &&
       /usr/local/libexec/cpa-admission-integrity-check &&
       systemctl restart cpa-admission &&
       wait_for_health && [[ "$POST_HOLDS" == "$PRE_HOLDS" ]]; then
      echo "ROLLBACK code_and_pin backup=$BK"
    else
      echo "ROLLBACK_FAILED backup=$BK"
      code=21
    fi
  fi
  exit "$code"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
[[ "$(sha256sum "$LIVE" | awk '{print $1}')" == "$OLD_PIN" ]] || {
  echo 'REFUSE live_code_drift'; exit 20;
}
[[ "$(awk 'NF {print $1; exit}' "$PIN")" == "$OLD_PIN" ]] || {
  echo 'REFUSE live_pin_drift'; exit 20;
}
printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
[[ "$(sha256sum "$STAGED" | awk '{print $1}')" == "$NEW_PIN" ]] || {
  echo 'REFUSE staged_hash'; exit 20;
}
python3 - "$STAGED" <<'PY'
import ast, sys
from pathlib import Path
ast.parse(Path(sys.argv[1]).read_text())
print('STAGED_SYNTAX_OK')
PY
lane_holds() {
  python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok'
assert h['traffic_guard'] == '/run/cpa-admission/requests.lock'
print(json.dumps({k: v['state'].get('hold_reason') for k, v in h['lanes'].items()}, sort_keys=True))
PY
}
wait_for_health() {
  local attempt
  for attempt in $(seq 1 15); do
    if POST_HOLDS=$(lane_holds 2>/dev/null); then return 0; fi
    sleep 1
  done
  return 1
}
PRE_HOLDS=$(lane_holds)
echo "PREFLIGHT code=$OLD_PIN candidate=$NEW_PIN holds=$PRE_HOLDS"
if [[ "$MODE" == dry-run ]]; then
  echo 'DRY_RUN_OK rollback=restore_code_and_pin_then_restart_admission; no_CPA_or_nginx_restart'
  exit 0
fi
[[ "$MODE" == apply-paused ]] || {
  echo 'REFUSE invalid_mode'; exit 20;
}
# Existing requests retain their shared lock. No traffic is killed to obtain
# an idle snapshot; a busy drain times out without changing live files.
exec 8</run/cpa-admission/requests.lock
if ! flock -w 180 -x 8; then
  echo 'DEFER active_requests_did_not_drain; live_unchanged'
  exit 75
fi
python3 - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5))
assert h['status'] == 'ok'
assert all(not v['state']['inflight'] and not v['state']['pending'] for v in h['lanes'].values())
print('ADMISSION_IDLE_OK')
PY
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-disconnect
mkdir -m 700 "$BK"
cp -a "$LIVE" "$PIN" "$BK/"
chmod 755 "$STAGED"
chown root:root "$STAGED"
MUTATED=1
mv -f -- "$STAGED" "$LIVE"
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"
chown root:root "$PIN.new"
mv -f -- "$PIN.new" "$PIN"
/usr/local/libexec/cpa-admission-integrity-check
WB_STATE=/var/lib/cpa-admission/workbuddy-risk.json
WB_STATE_BACKUP="$BK/workbuddy-risk.json.pre-6004-model-scope"
if [[ "$OLD_PIN" == 31df5b5e186388e933d01629e6475162138587b4fe29bad9573f3fa38d250dfa &&
      "$NEW_PIN" == 49ee2e0ca6e7266ab19dbe3ad52ada3f09d5877f4040e4d5020aa8ea218408f8 ]]; then
  cp -a "$WB_STATE" "$WB_STATE_BACKUP"
  python3 - "$WB_STATE" "$WB_STATE_BACKUP" <<'PY'
import json, os, pathlib, sys

state = pathlib.Path(sys.argv[1])
backup = pathlib.Path(sys.argv[2])
old = json.loads(backup.read_text(encoding="utf-8"))
desired = {"version": 1, "not_before": 0, "hold_reason": None}
if old == desired:
    print("WORKBUDDY_FALSE_HOLD_ALREADY_CLEARED")
else:
    assert old == {
        "version": 1,
        "not_before": 1792926341.9925077,
        "hold_reason": "retry_window_review_required",
    }, "REFUSE unexpected WorkBuddy review state"
    temporary = state.with_name(f".{state.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(desired, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, state.stat().st_uid, state.stat().st_gid)
        os.replace(temporary, state)
    finally:
        temporary.unlink(missing_ok=True)
PY
  echo 'WORKBUDDY_FALSE_HOLD_CLEARED backup='"$WB_STATE_BACKUP"
fi
systemctl restart cpa-admission
wait_for_health || { echo 'FAIL admission_readiness'; exit 20; }
[[ "$POST_HOLDS" == "$PRE_HOLDS" ]] || { echo 'FAIL hold_state_changed'; exit 20; }
exec 8>&-
echo "DEPLOY_OK code=$NEW_PIN backup=$BK"

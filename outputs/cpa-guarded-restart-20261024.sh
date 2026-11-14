#!/usr/bin/env bash
# Guarded restart of the CPA container and/or cpa-admission for manual
# operator restarts (config edits, pin restores, emergency bounce). Mirrors
# the updater/canary drain contract: maintenance lock, exclusive
# requests.lock, healthz idle assertion, pre/post lane-hold delta. The
# 2026-10-24 04:03Z manual restart skipped the drain, cut one in-flight
# chatgpt-oauth stream, and write-ahead latched unverified_request for a
# 14-minute 403 window.
# Usage: cpa-guarded-restart-20261024.sh [container|admission|both]
# Exit: 0 restarted | 10 hold appeared across the restart (recover per
# cpa-gateway.md manual release) | 20 refusal | 75 maintenance lock busy |
# 76 drain timeout. No file mutation, no auto-release of holds.
set -Eeuo pipefail
umask 077

MODE=${1:-both}
case "$MODE" in
  container|admission|both) ;;
  *) echo "USAGE: $0 [container|admission|both]" >&2; exit 20 ;;
esac

DIR=/opt/cliproxyapi

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo MAINTENANCE_BUSY; exit 75; }

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

assert_idle() {
  python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
assert health['status'] == 'ok', health
assert all(
    not lane['state']['inflight'] and not lane['state']['pending']
    for lane in health['lanes'].values()
), 'REFUSE active traffic survived the exclusive drain lock'
print('ADMISSION_IDLE_OK')
PY
}

PRE_HOLDS=$(lane_holds)
exec 8< /run/cpa-admission/requests.lock || {
  echo "REFUSE admission_not_running (requests.lock absent); nothing restarted"
  exit 20
}
if ! flock -w 60 -x 8; then
  echo "DRAIN_TIMEOUT requests.lock held shared for 60s; nothing restarted"
  exit 76
fi
sleep 1
assert_idle

case "$MODE" in
  container|both)
    docker restart cli-proxy-api >/dev/null
    ;;
esac
if [ "$MODE" = both ]; then
  READY=1
  for _ in $(seq 1 30); do
    if python3 "$DIR/cpa-health.py" readiness >/dev/null 2>&1; then
      READY=0
      break
    fi
    sleep 2
  done
  if [ "$READY" != 0 ]; then
    echo "CPA_READINESS_FAILED after container restart; admission not restarted"
    exit 1
  fi
fi
case "$MODE" in
  admission|both)
    systemctl restart cpa-admission.service
    ;;
esac

ADMISSION_READY=000
for _ in $(seq 1 15); do
  ADMISSION_READY=$(curl --noproxy '*' -sS --max-time 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8318/healthz || true)
  if [ "$ADMISSION_READY" = "200" ]; then
    break
  fi
  sleep 1
done
if [ "$ADMISSION_READY" != "200" ]; then
  echo "ADMISSION_HEALTHZ status=$ADMISSION_READY; see journalctl -u cpa-admission"
  exit 1
fi
# Release the drain lock before reading post-restart holds so client traffic
# resumes; hold state files were already rewritten by the restart.
exec 8>&-

POST_HOLDS=$(lane_holds)
NEW_HOLDS=$(comm -13 <(sort <<<"$PRE_HOLDS") <(sort <<<"$POST_HOLDS") || true)
if [ -n "$NEW_HOLDS" ]; then
  echo "UNVERIFIED: lane hold appeared across the restart: ${NEW_HOLDS//$'\n'/, }"
  echo "recover per docs/runbooks/cpa-gateway.md OAuth 持久化风险状态的人工恢复"
  exit 10
fi
echo "GUARDED_RESTART_OK mode=$MODE"

#!/bin/sh
# Read-only WorkBuddy plugin-version gate. The dynamically loaded .so files
# report their versions only through the registration lines, so container
# recreation resets the bounded container-log evidence like the adjacent
# runtime checks. 2026-11-26: workbuddy-ai-provider (INTL) is formally
# uninstalled/retired alongside the legacy workbuddy.so; only
# workbuddy-provider (CN) stays deployed and registered.
set -eu

DIR="${CPA_DIR:-/opt/cliproxyapi}"
EXPECTED_CN=0.21.31

if [ -f "$DIR/plugins/workbuddy.so" ]; then
  echo "workbuddy-version=LEGACY_SO_PRESENT expected=removed"
  exit 1
fi
if [ -f "$DIR/plugins/workbuddy-ai-provider.so" ]; then
  echo "workbuddy-version=INTL_SO_PRESENT expected=uninstalled"
  exit 1
fi
if [ ! -f "$DIR/plugins/workbuddy-provider.so" ]; then
  echo "workbuddy-version=LIVE_MISSING plugin=workbuddy-provider expected=$EXPECTED_CN"
  exit 1
fi

started=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api 2>/dev/null || true)
if [ -z "$started" ]; then
  echo "workbuddy-version=UNVERIFIED expected=$EXPECTED_CN current_process_log=none"
  exit 1
fi
since=$(date -u -d "$started" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)
if [ -z "$since" ]; then
  echo "workbuddy-version=UNVERIFIED expected=$EXPECTED_CN current_process_start_unreadable=true"
  exit 1
fi
registered_cn=$(
  docker logs --since "$since" cli-proxy-api 2>&1 |
    sed -n 's/.*plugin registered plugin_id=workbuddy-provider .*version=\([^ ]*\).*/\1/p' |
    tail -n 1
)
registered_intl=$(
  docker logs --since "$since" cli-proxy-api 2>&1 |
    sed -n 's/.*plugin registered plugin_id=workbuddy-ai-provider .*version=\([^ ]*\).*/\1/p' |
    tail -n 1
)

if [ -z "$registered_cn" ]; then
  echo "workbuddy-version=UNVERIFIED plugin=workbuddy-provider expected=$EXPECTED_CN current_process_log=none"
  exit 1
fi
if [ "$registered_cn" != "$EXPECTED_CN" ]; then
  echo "workbuddy-version=MISMATCH plugin=workbuddy-provider expected=$EXPECTED_CN got=$registered_cn"
  exit 1
fi
if [ -n "$registered_intl" ]; then
  echo "workbuddy-version=UNEXPECTED_REGISTRATION plugin=workbuddy-ai-provider got=$registered_intl note=uninstalled_20261126"
  exit 1
fi
echo "workbuddy-version=cn:$registered_cn intl:uninstalled"
echo "workbuddy-version-coverage=current_container_process_only; recreation resets the window; loading does not prove request behavior"
exit 0

#!/bin/sh
# Read-only WorkBuddy plugin-version gate. The dynamically loaded .so reports
# its version only through the registration line, so container recreation
# resets the bounded container-log evidence like the adjacent runtime checks.
set -eu

DIR="${CPA_DIR:-/opt/cliproxyapi}"
EXPECTED=0.9.86

if [ ! -f "$DIR/plugins/workbuddy.so" ]; then
  echo "workbuddy-version=LIVE_MISSING expected=$EXPECTED"
  exit 1
fi

registered=$(
  docker logs --since 168h cli-proxy-api 2>&1 |
    sed -n 's/.*plugin registered plugin_id=workbuddy .*version=\([^ ]*\).*/\1/p' |
    tail -n 1
)

if [ -z "$registered" ]; then
  echo "workbuddy-version=UNVERIFIED expected=$EXPECTED retained_container_logs=none"
  exit 1
fi

if [ "$registered" != "$EXPECTED" ]; then
  echo "workbuddy-version=MISMATCH expected=$EXPECTED got=$registered"
  exit 1
fi

echo "workbuddy-version=$registered"
echo "workbuddy-version-coverage=retained_container_logs_only; recreation resets the window; loading does not prove request behavior"

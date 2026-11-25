#!/bin/sh
# Read-only WorkBuddy plugin-version gate. The dynamically loaded .so files
# report their versions only through the registration lines, so container
# recreation resets the bounded container-log evidence like the adjacent
# runtime checks. 2026-11-17 luode0320 cutover: two plugins —
# workbuddy-provider (CN) and workbuddy-ai-provider (INTL) — and the legacy
# single-plugin workbuddy.so must be gone, or a stale mmqz artifact would
# register alongside the pair and serve an unpinned surface.
# 2026-11-17 (user directive, same day): the INTL lane is closed dark —
# workbuddy-ai-provider stays deployed but must NOT register; a registration
# line is an accidental re-open and fails this gate.
set -eu

DIR="${CPA_DIR:-/opt/cliproxyapi}"
EXPECTED_CN=0.21.31
EXPECTED_INTL=0.1.40

if [ -f "$DIR/plugins/workbuddy.so" ]; then
  echo "workbuddy-version=LEGACY_SO_PRESENT expected=removed"
  exit 1
fi
if [ ! -f "$DIR/plugins/workbuddy-provider.so" ]; then
  echo "workbuddy-version=LIVE_MISSING plugin=workbuddy-provider expected=$EXPECTED_CN"
  exit 1
fi
if [ ! -f "$DIR/plugins/workbuddy-ai-provider.so" ]; then
  echo "workbuddy-version=LIVE_MISSING plugin=workbuddy-ai-provider expected=$EXPECTED_INTL"
  exit 1
fi

started=$(docker inspect --format '{{.State.StartedAt}}' cli-proxy-api 2>/dev/null || true)
if [ -z "$started" ]; then
  echo "workbuddy-version=UNVERIFIED expected=$EXPECTED_CN+$EXPECTED_INTL current_process_log=none"
  exit 1
fi
since=$(date -u -d "$started" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)
if [ -z "$since" ]; then
  echo "workbuddy-version=UNVERIFIED expected=$EXPECTED_CN+$EXPECTED_INTL current_process_start_unreadable=true"
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
# Report an observed wrong version before a missing sibling: the wrong pin is
# the actionable signal when only one plugin registered.
if [ "$registered_cn" != "$EXPECTED_CN" ]; then
  echo "workbuddy-version=MISMATCH plugin=workbuddy-provider expected=$EXPECTED_CN got=$registered_cn"
  exit 1
fi
if [ -z "$registered_intl" ]; then
  # Closed-dark healthy state: the INTL plugin must not register while the
  # lane is closed. Its .so stays deployed for the one-flag re-enable.
  echo "workbuddy-version=cn:$registered_cn intl:disabled_closed_dark_20261117"
  echo "workbuddy-version-coverage=current_container_process_only; recreation resets the window; loading does not prove request behavior"
  exit 0
fi
echo "workbuddy-version=UNEXPECTED_REGISTRATION plugin=workbuddy-ai-provider got=$registered_intl note=closed_dark_20261117"
exit 1

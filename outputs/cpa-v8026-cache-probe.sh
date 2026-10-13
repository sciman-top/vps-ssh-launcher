#!/usr/bin/env bash
# BWG CPA cache-canary only (2026-10-12 UTC): the full quality-canary hit a
# channel-side muse timeout twice (exit 10, contract-UPSTREAM_UNAVAILABLE);
# the cache baseline is an independent mode and is banked separately without
# re-spending the quality budget.
set -Eeuo pipefail
if [[ "${VPS_SSH_LAUNCHER_PROFILE:-}" != "bwg" ]]; then
  echo BWG_PROFILE_REQUIRED >&2
  exit 64
fi
echo '== cache-canary glm-5.3-flash (CPA_HEALTH_NO_OAUTH=1) =='
CPA_HEALTH_NO_OAUTH=1 python3 /opt/cliproxyapi/cpa-health.py cache-canary glm-5.3-flash
echo "CACHE_CANARY_EXIT=$?"

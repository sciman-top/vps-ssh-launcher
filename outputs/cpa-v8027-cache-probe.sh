#!/usr/bin/env bash
# BWG CPA cache-canary only (2026-10-13): the full quality-canary aborted at
# the sol-input lane (ai.input.im channel-side pricing 400, natural traffic
# equally affected); the cache baseline is an independent mode and is banked
# separately without re-spending the quality budget.
set -Eeuo pipefail
if [[ "${VPS_SSH_LAUNCHER_PROFILE:-}" != "bwg" ]]; then
  echo BWG_PROFILE_REQUIRED >&2
  exit 64
fi
echo '== cache-canary glm-5.3-flash (CPA_HEALTH_NO_OAUTH=1) =='
CPA_HEALTH_NO_OAUTH=1 python3 /opt/cliproxyapi/cpa-health.py cache-canary glm-5.3-flash
echo "CACHE_CANARY_EXIT=$?"

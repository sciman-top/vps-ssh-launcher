#!/usr/bin/env bash
# BWG CPA post-upgrade read-back for v8.0.27 (2026-10-13 UTC): container
# state, then quality/cache canaries with the OAuth lane suppressed.
# Read-only probes: generation requests only, no OAuth traffic, no writes.
set -Eeuo pipefail
if [[ "${VPS_SSH_LAUNCHER_PROFILE:-}" != "bwg" ]]; then
  echo BWG_PROFILE_REQUIRED >&2
  exit 64
fi
DIR=/opt/cliproxyapi

echo '== container since upgrade =='
CID=$(docker compose -f "$DIR/compose.yml" ps -q cli-proxy-api)
docker inspect --format 'status={{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}}' "$CID"
docker inspect --format 'image={{.Config.Image}}' "$CID"
docker image inspect "$(docker inspect --format '{{.Config.Image}}' "$CID")" --format 'repo_digests={{json .RepoDigests}}'

echo '== quality-canary (CPA_HEALTH_NO_OAUTH=1) =='
CPA_HEALTH_NO_OAUTH=1 python3 "$DIR/cpa-health.py" quality-canary
echo "QUALITY_CANARY_EXIT=$?"

echo '== cache-canary glm-5.3-flash (CPA_HEALTH_NO_OAUTH=1) =='
CPA_HEALTH_NO_OAUTH=1 python3 "$DIR/cpa-health.py" cache-canary glm-5.3-flash
echo "CACHE_CANARY_EXIT=$?"

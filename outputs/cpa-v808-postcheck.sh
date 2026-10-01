#!/usr/bin/env bash
# Post-canary verification for CPA v8.0.8 (bwg): invariants + public live probes.
set -u
echo "== container invariants"
docker inspect cli-proxy-api --format 'image={{.Config.Image}} restarts={{.RestartCount}} started={{.State.StartedAt}}'
echo "== banner (first lines)"
docker logs cli-proxy-api 2>&1 | head -4
echo "== error/fatal/panic count in startup log"
docker logs cli-proxy-api 2>&1 | grep -ciE '\[error\]|\[fatal\]|panic' || true
echo "== config sha (prefix)"
sha256sum /opt/cliproxyapi/config.yaml | cut -c1-16
echo "== auth entries count"
ls -1 /opt/cliproxyapi/auth | wc -l
echo "== public live probes (1 streaming turn per lane)"
python3 /tmp/cpa-live-accept-v808.py gpt-6-luna glm-5.3-flash
echo "PROBES_EXIT=$?"

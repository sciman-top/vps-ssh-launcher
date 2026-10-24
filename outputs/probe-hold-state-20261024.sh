#!/usr/bin/env bash
# Read-only post-release state probe for the 2026-10-24 chatgpt-oauth hold.
set -Eeuo pipefail
date -u +'NOW=%Y-%m-%dT%H:%M:%SZ'
curl -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json
import sys

health = json.load(sys.stdin)
print("HEALTH", health["status"])
for name, lane in sorted(health["lanes"].items()):
    state = lane["state"]
    print(
        "LANE",
        name,
        "hold=%r" % (state.get("hold_reason"),),
        "inflight=%r" % (state.get("inflight"),),
        "pending=%r" % (state.get("pending"),),
        "cooldown_active=%r" % (state.get("cooldown_active"),),
    )
'
ls -l --time-style=full-iso /var/lib/cpa-admission/chatgpt-risk.json
cat /var/lib/cpa-admission/chatgpt-risk.json
ls -1 /opt/cliproxyapi/backups | grep chatgpt-hold-release || true

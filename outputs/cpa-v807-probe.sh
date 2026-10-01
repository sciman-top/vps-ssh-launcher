#!/usr/bin/env bash
# Read-only preflight probe for CPA v8.0.7 canary (bwg). No writes anywhere.
set -u
echo "== time"
date -u '+%FT%TZ'; date '+%F %T %z'
echo "== container"
docker ps --filter name=cli-proxy-api --format '{{.Names}} | {{.Image}} | {{.Status}}'
docker inspect cli-proxy-api --format 'restarts={{.RestartCount}} started={{.State.StartedAt}}' 2>/dev/null
echo "== config sha (prefix)"
sha256sum /opt/cliproxyapi/config.yaml | cut -c1-16
echo "== auth entries count"
ls -1 /opt/cliproxyapi/auth 2>/dev/null | wc -l
echo "== disk"
df -Pk /opt/cliproxyapi | tail -1
echo "== compose structural lines"
grep -nE 'network_mode|ports:|image:|container_name' /opt/cliproxyapi/compose.yml
echo "== netns tooling"
unshare --mount --net --fork true && echo UNSHARE_OK
echo "== updater --check (read-only)"
bash /opt/cliproxyapi/auto-update.sh --check 2>&1 | tail -6
echo "== update timer"
systemctl list-timers cliproxyapi-update.timer --no-pager 2>/dev/null | head -3
echo "== recent update log tail"
tail -3 /opt/cliproxyapi/auto-update.log 2>/dev/null

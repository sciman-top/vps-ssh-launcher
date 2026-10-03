#!/usr/bin/env bash
# Read-only audit probe for BWG CPA admission / risk controls. No writes.
set -u
echo "===A1 host==="; hostname; date -u +%FT%TZ; uptime | sed 's/  */ /g'
echo "===A2 container==="; docker ps --filter name=cli-proxy-api --format '{{.Names}} {{.Image}} {{.Status}}' 2>/dev/null || echo NO_DOCKER
echo "===A3 admission unit==="; systemctl is-active cpa-admission 2>/dev/null; systemctl show cpa-admission -p MainPID -p NRestarts --value 2>/dev/null | paste -sd, -
echo "===A4 healthz==="; curl -s --max-time 8 http://127.0.0.1:8318/healthz || echo HEALTHZ_FAIL
echo; echo "===A5 admission config in use==="; python3 -c "import json;d=json.load(open('/opt/cliproxyapi/cpa-admission.json'));print(json.dumps(d,separators=(',',':')))" 2>/dev/null || echo CONFIG_READ_FAIL
echo "===A6 deployed source hashes==="; for f in cpa-admission.py cpa-admission.json cpa_provider_routes.json cpa_policy.py cpa-health.py auto-update.sh; do p=/opt/cliproxyapi/$f; [ -f "$p" ] && echo "$f $(sha256sum "$p" | cut -c1-16) $(stat -c '%y' "$p" | cut -c1-19)"; done
echo "===A7 admission journal reasons (24h)==="
journalctl -u cpa-admission --since '24 hours ago' --no-pager 2>/dev/null | grep -oE 'lane_reject lane=[a-z0-9-]+ model=[^ ]+ reason=[a-z_]+' | awk '{print $3, $4, $5}' | sort | uniq -c | sort -rn | head -30
echo "===A8 admission upstream_result capacity (24h)==="
journalctl -u cpa-admission --since '24 hours ago' --no-pager 2>/dev/null | grep -oE 'upstream_result lane=[a-z0-9-]+ model=[^ ]+ status=[0-9]+ capacity=[a-z]+' | sort | uniq -c | sort -rn | head -30
echo "===A9 admission probe/reject totals (24h)==="
journalctl -u cpa-admission --since '24 hours ago' --no-pager 2>/dev/null | grep -cE 'lane_reject' | sed 's/^/lane_reject=/'
journalctl -u cpa-admission --since '24 hours ago' --no-pager 2>/dev/null | grep -cE 'lane_probe' | sed 's/^/lane_probe=/'
journalctl -u cpa-admission --since '24 hours ago' --no-pager 2>/dev/null | grep -cE 'upstream_error' | sed 's/^/upstream_error=/'
echo "===A10 nginx cpa access log upstream_status (24h)==="
LOG=/var/log/nginx/cpa_gateway.access.log
if [ -f "$LOG" ]; then
  awk -v cutoff="$(date -d '24 hours ago' +%d/%b/%Y:%H:%M:%S)" '$4 >= "["cutoff' "$LOG" 2>/dev/null | grep -oE 'upstream_status=[^ ]+' | sort | uniq -c | sort -rn | head -20
  echo "-- limit_conn/limit_req rejects --"
  awk -v cutoff="$(date -d '24 hours ago' +%d/%b/%Y:%H:%M:%S)" '$4 >= "["cutoff' "$LOG" 2>/dev/null | grep -cE 'limit_conn=[A-Z]+|limit_req=[A-Z]+' | sed 's/^/reject_lines=/'
  echo "-- terra upstream status --"
  awk -v cutoff="$(date -d '24 hours ago' +%d/%b/%Y:%H:%M:%S)" '$4 >= "["cutoff' "$LOG" 2>/dev/null | grep -c 'gpt-5.6-terra' | sed 's/^/terra_lines=/'
else echo NO_ACCESS_LOG; fi
echo "===A11 oauth quarantine / cooldown markers==="
[ -f /opt/cliproxyapi/oauth-quarantine.json ] && cat /opt/cliproxyapi/oauth-quarantine.json || echo "no quarantine marker"
echo "===A12 container log capacity markers (24h)==="
docker logs cli-proxy-api --since 24h 2>&1 | grep -ciE 'server_is_overloaded|usage_limit_reached|at capacity' | sed 's/^/capacity_markers=/'
docker logs cli-proxy-api --since 24h 2>&1 | grep -ciE 'credential_concurrency_exceeded|credential_model_concurrency_exceeded' | sed 's/^/cred_concurrency=/'
docker logs cli-proxy-api --since 24h 2>&1 | grep -ciE 'invalid_grant|refresh_token_reused' | sed 's/^/oauth_refresh_signal=/'
echo "===A13 routes projection==="
python3 -c "import json;d=json.load(open('/opt/cliproxyapi/cpa_provider_routes.json'));print('providers',[(p['slot'],p['name'],[m['alias'] for m in p['models']]) for p in d['providers']]);print('oauth',[(r['name'],[m['alias'] for m in r['models']]) for r in d['oauth_routes']])" 2>/dev/null || echo ROUTES_READ_FAIL
echo "===A14 admission probe budget / upstream reachability==="
curl -s -o /dev/null -w 'local_8317=%{http_code}\n' --max-time 5 http://127.0.0.1:8317/v1/models || echo 8317_FAIL
echo "===A15 disk/mem pressure==="
df -h / | tail -1; free -m | sed -n '2p'
echo "===PROBE_DONE==="

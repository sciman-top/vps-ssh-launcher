#!/usr/bin/env bash
# Read-only probe 2: attribute the sol-input 5xx population and count real nginx rejects.
set -u
LOG=/var/log/nginx/cpa_gateway.access.log
echo "===B1 sol-input 5xx hourly (admission journal, 48h)==="
journalctl -u cpa-admission --since '48 hours ago' --no-pager 2>/dev/null \
 | grep 'model=gpt-6.1-sol-input' \
 | awk '{print $1, $2, $3}' | cut -c1-13 | sort | uniq -c
echo "===B2 all passthrough 5xx hourly==="
journalctl -u cpa-admission --since '48 hours ago' --no-pager 2>/dev/null \
 | grep -E 'lane=passthrough .* status=5' | awk '{print $1,$2,$3}' | cut -c1-13 | sort | uniq -c
echo "===B3 nginx real rejects (24h)==="
awk -v c="$(date -d '24 hours ago' '+%d/%b/%Y:%H:%M:%S')" 'index($4,"["c)==1' "$LOG" 2>/dev/null \
 | grep -oE 'limit_conn=[A-Z]+' | sort | uniq -c
awk -v c="$(date -d '24 hours ago' '+%d/%b/%Y:%H:%M:%S')" 'index($4,"["c)==1' "$LOG" 2>/dev/null \
 | grep -oE 'limit_req=[A-Z]+' | sort | uniq -c
echo "===B4 nginx client IPs for 5xx (24h)==="
awk -v c="$(date -d '24 hours ago' '+%d/%b/%Y:%H:%M:%S')" 'index($4,"["c)==1' "$LOG" 2>/dev/null \
 | awk '{print $1, $9}' | grep -E ' (50[0-9])$' | sort | uniq -c | sort -rn | head -15
echo "===B5 nginx 429 lines (24h)==="
awk -v c="$(date -d '24 hours ago' '+%d/%b/%Y:%H:%M:%S')" 'index($4,"["c)==1' "$LOG" 2>/dev/null \
 | grep -E ' (429)$' | head -8
echo "===B6 nginx total lines 24h==="
awk -v c="$(date -d '24 hours ago' '+%d/%b/%Y:%H:%M:%S')" 'index($4,"["c)==1' "$LOG" 2>/dev/null | wc -l
echo "===B7 log format sample==="
tail -2 "$LOG" 2>/dev/null
echo "===B8 deployed config.yaml slot1/3 model lists==="
python3 - <<'PY' 2>/dev/null || echo PY_FAIL
import re
t=open('/opt/cliproxyapi/config.yaml',encoding='utf-8').read()
# print model name/alias entries only
for line in t.splitlines():
    s=line.strip()
    if s.startswith('- name:') or s.startswith('alias:') or s.startswith('name:') or s.startswith('base-url:') or s.startswith('prefix:'):
        print(s[:120])
PY
echo "===B9 oauth credential count / plan==="
ls -1 /opt/cliproxyapi/auth 2>/dev/null | head -20
echo "===PROBE2_DONE==="

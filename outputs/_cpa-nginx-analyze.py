#!/usr/bin/env python3
"""Read-only nginx cpa access log analyzer (last N hours)."""
import re, sys, collections, datetime, os

LOG = "/var/log/nginx/cpa_gateway.access.log"
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS)

pat = re.compile(r"time=\[(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2}) ([+-]\d{4})\]")
kv = re.compile(r"(\w+)=(\S+)")

by_status = collections.Counter()
by_up = collections.Counter()
by_ip_status = collections.Counter()
limit_req = collections.Counter()
limit_conn = collections.Counter()
by_hour_status = collections.Counter()
terra = collections.Counter()
total = 0

with open(LOG, encoding="utf-8", errors="ignore") as fh:
    for line in fh:
        m = pat.search(line)
        if not m:
            continue
        try:
            ts = datetime.datetime.strptime(m.group(1), "%d/%b/%Y:%H:%M:%S")
        except ValueError:
            continue
        ts = ts.replace(tzinfo=datetime.timezone.utc)
        if ts < cutoff:
            continue
        total += 1
        d = dict(kv.findall(line))
        st = d.get("status", "?")
        ip = line.split(" ", 1)[0]
        by_status[st] += 1
        by_up["upstream_status=" + d.get("upstream_status", "?")] += 1
        limit_req[d.get("limit_req", "?")] += 1
        limit_conn[d.get("limit_conn", "?")] += 1
        by_hour_status[(ts.strftime("%m-%d %H"), st)] += 1
        if st.startswith("5") or st == "429":
            by_ip_status[(ip, st)] += 1

print(f"total_lines={total} window={HOURS}h")
print("-- status --"); [print(" ", k, v) for k, v in by_status.most_common()]
print("-- upstream_status --"); [print(" ", k, v) for k, v in by_up.most_common()]
print("-- limit_req --"); [print(" ", k, v) for k, v in limit_req.most_common()]
print("-- limit_conn --"); [print(" ", k, v) for k, v in limit_conn.most_common()]
print("-- 5xx/429 by client ip --")
for (ip, st), n in by_ip_status.most_common(20):
    print(f"  {ip} {st} {n}")
print("-- hourly 5xx/429 --")
for k in sorted(by_hour_status):
    if k[1].startswith("5") or k[1] == "429":
        print(" ", k[0], k[1], by_hour_status[k])
print("-- 429 samples --")
shown = 0
with open(LOG, encoding="utf-8", errors="ignore") as fh:
    for line in fh:
        m = pat.search(line)
        if not m:
            continue
        try:
            ts = datetime.datetime.strptime(m.group(1), "%d/%b/%Y:%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            continue
        if re.search(r" status=429 ", line) and shown < 6:
            print("  ", line.strip()[:230]); shown += 1
print("-- 5xx samples --")
shown = 0
with open(LOG, encoding="utf-8", errors="ignore") as fh:
    for line in fh:
        m = pat.search(line)
        if not m:
            continue
        try:
            ts = datetime.datetime.strptime(m.group(1), "%d/%b/%Y:%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            continue
        if re.search(r" status=5\d\d ", line) and shown < 6:
            print("  ", line.strip()[:230]); shown += 1
print("ANALYZE_DONE")

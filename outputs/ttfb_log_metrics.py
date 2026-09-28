#!/usr/bin/env python3
"""Post-change acceptance metrics from the redacted nginx access log.

Reports the client-visible time-to-first-byte (`upstream_header_time`, the field
added on 2026-09-28) next to the completed-response time so the two are never
conflated again. Read-only; no IPs are printed.
"""

import re
from collections import Counter

LOG = "/var/log/nginx/cpa_gateway.access.log"
PATTERN = re.compile(
    r"route=(?P<route>\S+) status=(?P<status>\d+) "
    r"request_time=(?P<rt>[\d.]+) upstream_status=(?P<us>[\d-]+) "
    r"upstream_time=(?P<ut>[\d.]+) bytes=(?P<b>\d+).*"
    r"upstream_header_time=(?P<ht>[\d.-]+)"
)


def quantile(values, fraction):
    if not values:
        return 0.0
    return values[min(len(values) - 1, int(len(values) * fraction))]


def main() -> None:
    rows = []
    with_field = 0
    total = 0
    for line in open(LOG, errors="replace"):
        match = PATTERN.search(line)
        if not match:
            continue
        total += 1
        if match.group("ht") != "-":
            with_field += 1
        if match.group("status") == "200" and match.group("route") == "responses":
            rows.append(
                (
                    float(match.group("ht")),
                    float(match.group("ut")),
                    float(match.group("rt")),
                )
            )
    header = sorted(row[0] for row in rows)
    whole = sorted(row[1] for row in rows)
    print(f"log_lines={total} with_upstream_header_time={with_field}")
    print(
        "responses200 n={} ttfb p50={:.2f} p90={:.2f} max={:.2f} | "
        "total p50={:.2f} p90={:.2f}".format(
            len(rows),
            quantile(header, 0.5),
            quantile(header, 0.9),
            header[-1] if header else 0.0,
            quantile(whole, 0.5),
            quantile(whole, 0.9),
        )
    )
    statuses = Counter()
    for line in open(LOG, errors="replace"):
        match = PATTERN.search(line)
        if match:
            statuses[match.group("status")] += 1
    print("statuses", statuses.most_common(8))


if __name__ == "__main__":
    main()

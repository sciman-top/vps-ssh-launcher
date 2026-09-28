#!/usr/bin/env python3
"""Summarise CPA retained error dumps without echoing request bodies or secrets."""

import glob
import re

for path in sorted(glob.glob("/opt/cliproxyapi/auth/logs/error-*.log")):
    text = open(path, encoding="utf-8", errors="replace").read()
    ua = re.search(r"User-Agent:\s*(.+)", text)
    model = re.search(r'"model"\s*:\s*"([^"]+)"', text)
    status = re.search(r"Status(?: Code)?:\s*(\d+)", text)
    ts = re.search(r"Timestamp:\s*(.+)", text)
    print(
        path.split("/")[-1],
        "| ts:",
        (ts.group(1).strip()[:19] if ts else "-"),
        "| UA:",
        (ua.group(1).strip()[:36] if ua else "-"),
        "| model:",
        (model.group(1) if model else "-"),
        "| status:",
        (status.group(1) if status else "-"),
    )

#!/usr/bin/env python3
"""Capture one real SSE body from the gateway for replay by fault_server.py.

Reads the client token from config.toml on the fly; writes only the response
body to the path given on the command line.
"""

import json
import os
import pathlib
import re
import sys
import urllib.request

OUT = pathlib.Path(sys.argv[1])
cfg = pathlib.Path(
    os.environ.get("CODEX_SOURCE_CONFIG", "/mnt/c/Users/sciman/.codex/config.toml")
)
text = cfg.read_text(encoding="utf-8")
token = re.search(r'experimental_bearer_token\s*=\s*"([^"]+)"', text).group(1)
base = re.search(r'base_url\s*=\s*"([^"]+)"', text).group(1)

body = json.dumps(
    {
        "model": "gpt-5.6-terra",
        "input": "Reply with exactly: OK",
        "stream": True,
        "max_output_tokens": 24,
        "store": False,
    }
).encode()
request = urllib.request.Request(
    base + "/responses",
    data=body,
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=120) as response:
    payload = response.read()
OUT.write_bytes(payload)
print(json.dumps({"bytes": len(payload), "completed": b"response.completed" in payload}))

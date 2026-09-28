#!/usr/bin/env python3
"""Wait for the shared lane to leave cooldown, then replay the three-hop chain.

The chain can only be compared hop-for-hop while the upstream is healthy; a
capacity event makes every hop fail with a locally generated status and says
nothing about the proxy layers. This waits for a settled lane and then runs the
same body through CPA, admission and the public TLS entry.
"""

import json
import subprocess
import time
import urllib.request

HEALTH = "http://127.0.0.1:8318/healthz"


def settled() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=10) as resp:
            state = json.load(resp)["lanes"]["chatgpt-oauth"]["state"]
    except Exception:  # noqa: BLE001
        return False
    return (
        state["cooldown_remaining"] == 0
        and state["failure_streak"] == 0
        and not state["half_open_probe"]
    )


deadline = time.monotonic() + 240
while not settled() and time.monotonic() < deadline:
    time.sleep(5)
print(json.dumps({"lane_settled": settled()}), flush=True)
subprocess.run(["python3", "-u", "/tmp/v/probe.py"], check=False)

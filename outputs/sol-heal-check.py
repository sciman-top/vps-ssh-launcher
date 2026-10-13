#!/usr/bin/env python3
"""One-shot sol-input heal check (2026-10-13).

Runs ON the bwg VPS: single minimal non-streaming /v1/responses request for
gpt-6.1-sol-input through the loopback listener. Key never printed; prints
only status/latency/model/finish shape.
"""

import json
import time
import urllib.error
import urllib.request

import yaml

with open("/opt/cliproxyapi/config.yaml", encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)
key = cfg["access"]["api-keys"][0]

body = {
    "model": "gpt-6.1-sol-input",
    "input": [{"role": "user", "content": [{"type": "input_text", "text": "Reply with exactly: OK"}]}],
    "max_output_tokens": 256,
}
req = urllib.request.Request(
    "http://127.0.0.1:8317/v1/responses",
    data=json.dumps(body).encode(),
    headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    },
)

t0 = time.monotonic()
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    print("http_status", resp.status)
    print("latency_s", round(time.monotonic() - t0, 3))
    print("model", data.get("model"))
    print("status_field", data.get("status"))
except urllib.error.HTTPError as exc:
    print("http_status", exc.code)
    print("latency_s", round(time.monotonic() - t0, 3))
    try:
        payload = json.load(exc)
    except Exception:
        payload = {}
    message = (payload.get("error") or {}).get("message", "")
    print("error_message_head", message[:80])

#!/usr/bin/env python3
"""One-shot streaming keepalive observation (2026-10-13).

Runs ON the bwg VPS: reads the client key from the live config but never
prints it; sends a single streaming chat request to the loopback listener and
reports only SSE frame shapes and timing. Goal: observe whether CPA emits
": keep-alive" comment frames before the first data frame when the upstream
TTFB exceeds streaming.keepalive-seconds (15s).
"""

import json
import time
import urllib.request

import yaml

MODEL = "deepseek-v4.1-flash"
PROMPT = (
    "Silently reason step by step before answering. What is 37*43 + 19*87? "
    "Double-check every partial product before you reply."
)

with open("/opt/cliproxyapi/config.yaml", encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)
key = cfg["access"]["api-keys"][0]

body = {
    "model": MODEL,
    "stream": True,
    "max_tokens": 512,
    "messages": [{"role": "user", "content": PROMPT}],
}
req = urllib.request.Request(
    "http://127.0.0.1:8317/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    },
)

t0 = time.monotonic()
frames = []
with urllib.request.urlopen(req, timeout=150) as resp:
    print("http_status", resp.status)
    print("headers_at_s", round(time.monotonic() - t0, 3))
    buf = b""
    while True:
        chunk = resp.read1(4096)
        if not chunk:
            break
        now = round(time.monotonic() - t0, 3)
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for line in lines:
            s = line.strip()
            if not s:
                continue
            if s.startswith(b":"):
                frames.append(("comment", now, b""))
            elif s.startswith(b"data:"):
                payload = s[5:].strip()
                frames.append(("data", now, payload[:20]))
            elif s.startswith(b"event:"):
                frames.append(("event", now, s[6:].strip()[:20]))

seen_data = False
comments_before_ft = 0
for kind, _at, _payload in frames:
    if kind == "data":
        seen_data = True
    elif kind == "comment" and not seen_data:
        comments_before_ft += 1

print("model", MODEL)
print("frames_total", len(frames))
print("first_frame_at_s", frames[0][1] if frames else None)
first_data = next((at for kind, at, _ in frames if kind == "data"), None)
print("first_data_at_s", first_data)
print("comments_before_first_data", comments_before_ft)
print("done_frame_present", any(k == "data" and p == b"[DONE]" for k, _, p in frames))
print("timeline_head", frames[:14])

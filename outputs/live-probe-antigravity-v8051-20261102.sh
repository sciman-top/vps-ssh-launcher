#!/usr/bin/env bash
# One-shot antigravity acceptance for the bwg CPA v8.0.51 canary, 2026-11-02.
# Pinned lane model gemini-3.8-flash-high through 127.0.0.1:8318; the key is
# read on the host and never printed; exactly one request, never retried.
set -uo pipefail
python3 -u - <<'PY'
import hashlib
import http.client
import json
import time
import urllib.request

health = json.loads(
    urllib.request.urlopen("http://127.0.0.1:8318/healthz", timeout=5).read()
)
state = health.get("lanes", {}).get("antigravity-oauth", {}).get("state", {})
if health.get("status") != "ok":
    print("CONTROLLED_ACCEPT_antigravity-oauth=FAIL reason=healthz")
    raise SystemExit(3)
if state.get("hold_reason") or state.get("cooldown_active") or state.get("inflight"):
    print(
        "CONTROLLED_ACCEPT_antigravity-oauth=DEFER reason=lane_busy inflight="
        + str(state.get("inflight"))
    )
    raise SystemExit(0)

import yaml

config = yaml.safe_load(open("/opt/cliproxyapi/config.yaml", encoding="utf-8"))
key = ((config.get("access") or {}).get("api-keys") or [None])[0]
payload = json.dumps(
    {
        "model": "gemini-3.8-flash-high",
        "input": "Reply with exactly the single word pong.",
        "max_output_tokens": 16,
        "stream": True,
    }
).encode()
conn = http.client.HTTPConnection("127.0.0.1", 8318, timeout=120)
t0 = time.monotonic()
conn.request(
    "POST",
    "/v1/responses",
    payload,
    {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    },
)
resp = conn.getresponse()
body = resp.read(2 * 1024 * 1024)
status = resp.status
ms = int((time.monotonic() - t0) * 1000)
rid = resp.getheader("X-CPA-Request-Id") or ""
print("LIVE_PROBE_HTTP=" + str(status))
print("LIVE_PROBE_LATENCY_MS=" + str(ms))
print("LIVE_PROBE_REQUEST_ID_HASH=" + (hashlib.sha256(rid.encode()).hexdigest()[:12] if rid else "absent"))
completed = failed = False
text = []
events = 0
for raw in body.decode("utf-8", "replace").splitlines():
    if not raw.startswith("data: "):
        continue
    data = raw[6:]
    if data == "[DONE]":
        continue
    try:
        item = json.loads(data)
    except json.JSONDecodeError:
        failed = True
        continue
    events += 1
    kind = item.get("type")
    if kind in {"error", "response.failed", "response.incomplete"} or item.get("error"):
        failed = True
    if kind == "response.output_text.delta" and isinstance(item.get("delta"), str):
        text.append(item["delta"])
    if kind == "response.completed":
        completed = isinstance(item.get("response"), dict) and item["response"].get("status") == "completed"
print("LIVE_PROBE_EVENT_COUNT=" + str(events))
print("LIVE_PROBE_COMPLETED=" + str(completed).lower())
ok = status == 200 and completed and not failed and "".join(text).strip() == "pong"
print("CONTROLLED_ACCEPT_antigravity-oauth=" + ("PASS" if ok else "FAIL"))
conn.close()
raise SystemExit(0 if ok else 1)
PY

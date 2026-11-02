#!/usr/bin/env bash
# Controlled live acceptance for the bwg CPA v8.0.51 host canary, 2026-11-02.
# Three single-shot probes through the full admission chain on 127.0.0.1:8318:
#   1. non-OAuth generation gate (glm-5.3-flash, the updater's own gate)
#   2. workbuddy deepseek-v4.1-flash Responses SSE one-shot (pong)
#   3. antigravity gemini-3.8-flash Responses SSE one-shot (pong)
# One request per probe, never retried; the client key is read on the host and
# never printed; outputs carry status/finish/latency only, never bodies.
# The chatgpt lane keeps its no-synthetic-probe rule and is judged from the
# natural-traffic window in the ledger instead.
set -uo pipefail
umask 077

echo "== phase 1: non-OAuth generation gate =="
if python3 /opt/cliproxyapi/cpa-health.py generation; then
  echo "CONTROLLED_ACCEPT_GLM=PASS"
else
  code=$?
  echo "CONTROLLED_ACCEPT_GLM=FAIL exit=$code"
fi

probe_lane() {
  local lane="$1" model="$2"
  python3 -u - "$lane" "$model" <<'PY'
import hashlib
import http.client
import json
import sys
import time
import urllib.request

lane_name, model = sys.argv[1], sys.argv[2]

health = json.loads(
    urllib.request.urlopen("http://127.0.0.1:8318/healthz", timeout=5).read()
)
lane = health.get("lanes", {}).get(lane_name, {})
state = lane.get("state", {}) if isinstance(lane, dict) else {}
print("LIVE_PROBE_GATE=" + ("OPEN" if health.get("status") == "ok" else "FAIL"))
if health.get("status") != "ok":
    print(f"CONTROLLED_ACCEPT_{lane_name}=FAIL reason=healthz")
    raise SystemExit(3)
# Busy lane: wait briefly for inflight to drain instead of adding pressure.
for _ in range(6):
    if not (state.get("hold_reason") or state.get("cooldown_active") or state.get("inflight")):
        break
    time.sleep(5)
    health = json.loads(
        urllib.request.urlopen("http://127.0.0.1:8318/healthz", timeout=5).read()
    )
    lane = health.get("lanes", {}).get(lane_name, {})
    state = lane.get("state", {}) if isinstance(lane, dict) else {}
if state.get("hold_reason") or state.get("cooldown_active") or state.get("inflight"):
    print(f"CONTROLLED_ACCEPT_{lane_name}=DEFER reason=lane_busy inflight={state.get('inflight')}")
    raise SystemExit(0)

import yaml  # noqa: E402

config = yaml.safe_load(open("/opt/cliproxyapi/config.yaml", encoding="utf-8"))
keys = (config.get("access") or {}).get("api-keys") or []
key = keys[0] if keys else None
if not isinstance(key, str) or not key:
    print(f"CONTROLLED_ACCEPT_{lane_name}=FAIL reason=no_client_key")
    raise SystemExit(4)

payload = json.dumps(
    {
        "model": model,
        "input": "Reply with exactly the single word pong.",
        "max_output_tokens": 16,
        "stream": True,
    }
).encode()
connection = http.client.HTTPConnection("127.0.0.1", 8318, timeout=120)
started = time.monotonic()
try:
    connection.request(
        "POST",
        "/v1/responses",
        payload,
        {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    response = connection.getresponse()
    body = response.read(2 * 1024 * 1024)
    status = response.status
    request_id = response.getheader("X-CPA-Request-Id") or ""
except Exception as exc:
    print("LIVE_PROBE_TRANSPORT_ERROR=" + type(exc).__name__)
    print(f"CONTROLLED_ACCEPT_{lane_name}=FAIL reason=transport")
    raise SystemExit(10)
finally:
    connection.close()

elapsed_ms = int((time.monotonic() - started) * 1000)
print("LIVE_PROBE_HTTP=" + str(status))
print("LIVE_PROBE_LATENCY_MS=" + str(elapsed_ms))
print(
    "LIVE_PROBE_REQUEST_ID_HASH="
    + (hashlib.sha256(request_id.encode()).hexdigest()[:12] if request_id else "absent")
)

completed = False
failed = False
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
    if not isinstance(item, dict):
        failed = True
        continue
    events += 1
    kind = item.get("type")
    if kind in {"error", "response.failed", "response.incomplete"} or item.get("error"):
        failed = True
    if kind == "response.output_text.delta" and isinstance(item.get("delta"), str):
        text.append(item["delta"])
    if kind == "response.completed":
        response_obj = item.get("response")
        completed = isinstance(response_obj, dict) and response_obj.get("status") == "completed"

print("LIVE_PROBE_EVENT_COUNT=" + str(events))
print("LIVE_PROBE_COMPLETED=" + str(completed).lower())
ok = status == 200 and completed and not failed and "".join(text).strip() == "pong"
print("CONTROLLED_ACCEPT_" + lane_name + "=" + ("PASS" if ok else "FAIL"))
raise SystemExit(0 if ok else 1)
PY
  local code=$?
  if (( code == 3 || code == 4 )); then
    echo "CONTROLLED_ACCEPT_${lane}=FAIL exit=$code"
  elif (( code != 0 )); then
    echo "CONTROLLED_ACCEPT_${lane}=FAIL exit=$code"
  fi
}

echo "== phase 2: workbuddy one-shot =="
probe_lane workbuddy-oauth deepseek-v4.1-flash
echo "== phase 3: antigravity one-shot =="
probe_lane antigravity-oauth gemini-3.8-flash-high
echo "== controlled acceptance done =="

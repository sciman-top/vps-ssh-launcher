#!/usr/bin/env bash
# One-shot DeepSeek Responses SSE acceptance after the per-lane RPM guard
# projection. The key is read on the BWG host and never printed; this probe
# sends exactly one request and never retries.
set -uo pipefail

python3 -u - <<'PY'
import hashlib
import http.client
import json
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    print("LIVE_PROBE_RESULT=FAIL reason=missing_yaml")
    raise SystemExit(4)

health = json.loads(
    __import__("urllib.request").request.urlopen(
        "http://127.0.0.1:8318/healthz", timeout=5
    ).read()
)
lane = health.get("lanes", {}).get("workbuddy-oauth", {})
state = lane.get("state", {}) if isinstance(lane, dict) else {}
print("LIVE_PROBE_GATE=" + ("OPEN" if health.get("status") == "ok" else "FAIL"))
print(
    "LIVE_PROBE_RPM="
    + str(lane.get("requests_per_minute"))
    + " window_used="
    + str(state.get("rpm_window_used"))
)
if health.get("status") != "ok":
    print("LIVE_PROBE_RESULT=FAIL reason=healthz")
    raise SystemExit(3)
if state.get("hold_reason") or state.get("cooldown_active") or state.get("inflight"):
    print("LIVE_PROBE_RESULT=FAIL reason=lane_not_open")
    raise SystemExit(3)

config = yaml.safe_load(Path("/opt/cliproxyapi/config.yaml").read_text())
keys = ((config.get("access") or {}).get("api-keys") or [])
key = keys[0] if keys else None
if not isinstance(key, str) or not key:
    print("LIVE_PROBE_RESULT=FAIL reason=no_client_key")
    raise SystemExit(4)

payload = json.dumps(
    {
        "model": "deepseek-v4.1-flash",
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
    print("LIVE_PROBE_RESULT=FAIL reason=transport")
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
print("LIVE_PROBE_TEXT_MATCH=" + str("".join(text).strip() == "pong").lower())
print(
    "LIVE_PROBE_RESULT="
    + ("PASS" if status == 200 and completed and not failed and "".join(text).strip() == "pong" else "FAIL")
)
raise SystemExit(0 if status == 200 and completed and not failed and "".join(text).strip() == "pong" else 1)
PY

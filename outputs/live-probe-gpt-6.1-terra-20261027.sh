# One-shot live acceptance probe for the adopted gpt-6.1-terra OAuth route.
# Runs ON the BWG host: pre-checks the admission lane, then sends exactly one
# POST via 127.0.0.1:8318 (full admission chain). No retry: 408/429/5xx are
# final. The client key is read inside the probe process and never printed.
set -uo pipefail

curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
print("HEALTHZ_STATUS=" + str(h.get("status")))
lane = (h.get("lanes", {}) or {}).get("chatgpt-oauth", {})
s = lane.get("state", {}) if isinstance(lane, dict) else {}
print("LANE_CHATGPT=" + json.dumps(
    {k: s.get(k) for k in ("state", "inflight", "pending", "cooldown_remaining", "model_cooldowns")},
    sort_keys=True,
))
raise SystemExit(0 if str(h.get("status")) == "ok" else 1)
' || { echo PRECHECK_FAIL; exit 3; }

python3 -u - <<'PYEOF'
import hashlib
import json
import time
import urllib.error
import urllib.request

import yaml

with open("/opt/cliproxyapi/config.yaml", encoding="utf-8") as handle:
    config = yaml.safe_load(handle)
key = ((config.get("access") or {}).get("api-keys") or [None])[0]
if not key:
    print("PROBE_NO_CLIENT_KEY")
    raise SystemExit(4)

payload = {
    "model": "gpt-6.1-terra",
    "input": "Reply with the single word: ok",
    "max_output_tokens": 16,
    "stream": False,
}
request = urllib.request.Request(
    "http://127.0.0.1:8318/v1/responses",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    },
    method="POST",
)
started = time.monotonic()
request_id = ""
try:
    with urllib.request.urlopen(request, timeout=280) as response:
        status = response.status
        body = response.read().decode("utf-8", "replace")
        request_id = (response.headers or {}).get("x-request-id", "")
except urllib.error.HTTPError as error:
    status = error.code
    body = error.read().decode("utf-8", "replace")
    request_id = (error.headers or {}).get("x-request-id", "")
latency_ms = int((time.monotonic() - started) * 1000)

print("LIVE_PROBE_HTTP=" + str(status))
print("LIVE_PROBE_LATENCY_MS=" + str(latency_ms))
print(
    "LIVE_PROBE_REQUEST_ID_HASH="
    + (hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:12] if request_id else "absent")
)
envelope = None
try:
    envelope = json.loads(body)
except (ValueError, UnicodeDecodeError):
    pass
accepted = False
if isinstance(envelope, dict):
    print("LIVE_PROBE_SERVED_MODEL=" + str(envelope.get("model", "absent")))
    print("LIVE_PROBE_STATUS=" + str(envelope.get("status", "absent")))
    print("LIVE_PROBE_OUTPUT_PRESENT=" + str(bool(envelope.get("output"))).lower())
    usage = envelope.get("usage") or {}
    print("LIVE_PROBE_USAGE_TOTAL_TOKENS=" + str(usage.get("total_tokens", "absent")))
    if status != 200 or envelope.get("status") != "completed":
        error = envelope.get("error")
        detail = error.get("code") or error.get("type") if isinstance(error, dict) else None
        print("LIVE_PROBE_ERROR_DETAIL=" + str(detail or "envelope-not-completed"))
    accepted = (
        status == 200
        and envelope.get("status") == "completed"
        and envelope.get("model") == "gpt-6.1-terra"
        and bool(envelope.get("output"))
    )
else:
    print("LIVE_PROBE_BODY_NON_JSON=true")
print("LIVE_PROBE_RESULT=" + ("PASS" if accepted else "FAIL"))
PYEOF

"""Single-shot live acceptance for claude-haiku-5-5 via /v1/responses.

Runs on the VPS against the admission front on loopback 8318 (full chain).
Client key read from the remote config; the key never leaves the host.
One shot, no retry by contract.
"""

import json
import sys
import urllib.error
import urllib.request

import yaml

MODEL = "claude-haiku-5-5"

config = yaml.safe_load(open("/opt/cliproxyapi/config.yaml", encoding="utf-8"))
key = config["access"]["api-keys"][0]
body = json.dumps(
    {
        "model": MODEL,
        "input": "Reply with exactly: OK",
        "max_output_tokens": 64,
        "stream": False,
    }
).encode("utf-8")
request = urllib.request.Request(
    "http://127.0.0.1:8318/v1/responses",
    data=body,
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request, timeout=290) as response:
        payload = json.load(response)
except urllib.error.HTTPError as exc:
    detail = exc.read(400).decode("utf-8", "replace").replace("\n", " ")
    print("LIVE_REJECTED status=%d detail=%s" % (exc.code, detail))
    sys.exit(1)

text = ""
for item in payload.get("output", []):
    if isinstance(item, dict) and item.get("type") == "message":
        for part in item.get("content", []):
            if isinstance(part, dict) and part.get("type") == "output_text":
                text += part.get("text", "")
print(
    "LIVE_RESULT model=%s status=%s text=%r usage=%s"
    % (
        payload.get("model"),
        payload.get("status"),
        text[:80],
        json.dumps(payload.get("usage", {})),
    )
)
if payload.get("model") == MODEL and payload.get("status") == "completed":
    print("LIVE_ACCEPTED model=%s status=completed" % MODEL)
else:
    print("LIVE_MISMATCH")
    sys.exit(1)

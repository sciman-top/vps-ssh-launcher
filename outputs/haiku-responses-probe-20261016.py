"""One-shot /responses probe for claude-haiku-5-5 on opencode zen (2026-10-16).

Runs on the VPS with the slot-2 credential and the pinned session header.
One generation shot, no retry by contract.
"""

import json
import sys
import urllib.error
import urllib.request

import yaml

MODEL = "claude-haiku-5-5"
SESSION_HEADER = "cpa-bwg-gateway-01"

config = yaml.safe_load(open("/opt/cliproxyapi/config.yaml", encoding="utf-8"))
entry = next(
    e
    for e in config["api-keys"]["openai-compatibility"]
    if str(e.get("base-url", "")).rstrip("/") == "https://opencode.ai/zen/go/v1"
)
key = entry["keys"][0]["api-key"]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
request = urllib.request.Request(
    "https://opencode.ai/zen/go/v1/responses",
    data=json.dumps(
        {
            "model": MODEL,
            "input": "Reply with exactly: OK",
            "max_output_tokens": 64,
            "stream": False,
        }
    ).encode("utf-8"),
    headers={
        "Authorization": "Bearer " + key,
        "x-opencode-session": SESSION_HEADER,
        "Content-Type": "application/json",
    },
)
try:
    with opener.open(request, timeout=120) as response:
        payload = json.load(response)
except urllib.error.HTTPError as exc:
    detail = exc.read(400).decode("utf-8", "replace").replace("\n", " ")
    print("RESPONSES status=%s detail=%s" % (exc.code, detail[:300]))
    sys.exit(1)

text = ""
for item in payload.get("output", []):
    if isinstance(item, dict) and item.get("type") == "message":
        for part in item.get("content", []):
            if isinstance(part, dict) and part.get("type") == "output_text":
                text += part.get("text", "")
print(
    "RESPONSES_OK model=%s status=%s text=%r usage=%s"
    % (
        payload.get("model"),
        payload.get("status"),
        text[:80],
        json.dumps(payload.get("usage", {})),
    )
)
if payload.get("model") == MODEL and payload.get("status") == "completed":
    print("UPSTREAM_RESPONSES_ACCEPTED model=%s" % MODEL)
else:
    sys.exit(1)

"""Single-shot live acceptance probe for claude-haiku-5-5 (2026-10-16).

Runs on the VPS. One POST to the admission front on loopback 8318 using the
client key read from the remote config (the key never leaves the host).
No retry by contract: any HTTP error path exits non-zero after one print.
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
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 64,
        "stream": False,
    }
).encode("utf-8")
request = urllib.request.Request(
    "http://127.0.0.1:8318/v1/chat/completions",
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

model = str(payload.get("model", ""))
choice = (payload.get("choices") or [{}])[0]
finish = choice.get("finish_reason")
content = ((choice.get("message") or {}).get("content") or "")[:120]
usage = json.dumps(payload.get("usage", {}))
print(
    "LIVE_RESULT model=%s finish=%s content=%r usage=%s" % (model, finish, content, usage)
)
if model == MODEL and finish == "stop":
    print("LIVE_ACCEPTED model=%s finish=stop" % MODEL)
else:
    print("LIVE_MISMATCH model=%s finish=%s" % (model, finish))
    sys.exit(1)

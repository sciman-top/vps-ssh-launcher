"""Post-upgrade OAuth lane singles for v8.0.35 acceptance (2026-10-20).

Loopback-only, one shot per model, no retries. Prints status/latency/finish
metadata only: never response bodies, keys, or request text.
"""
import json
import sys
import time
import urllib.error
import urllib.request

import yaml

BASE = "http://127.0.0.1:8317"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fail(msg):
    print("PREFLIGHT_REFUSE " + msg)
    sys.exit(20)


h = json.load(urllib.request.urlopen(BASE + "/healthz", timeout=5))
if h.get("status") != "ok":
    fail("healthz not ok")
busy = {
    lane: (st["state"]["inflight"], st["state"]["pending"])
    for lane, st in h.get("lanes", {}).items()
    if st["state"]["inflight"] or st["state"]["pending"]
}
if busy:
    fail("active traffic " + json.dumps(busy))

cfg = yaml.safe_load(open("/opt/cliproxyapi/config.yaml"))
access = cfg.get("access") if isinstance(cfg, dict) else None
keys = access.get("api-keys") if isinstance(access, dict) else None
if not (isinstance(keys, list) and keys and isinstance(keys[0], str) and keys[0]):
    fail("no local api key")
key = keys[0]


def post(path, body, headers, label, timeout=150):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    t0 = time.time()
    try:
        with OPENER.open(req, timeout=timeout) as r:
            code, payload = r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        code, payload = e.code, {}
    except Exception as exc:
        print(f"{label} transport_error class={type(exc).__name__}")
        return
    dt = time.time() - t0
    print(f"{label} http={code} latency={dt:.1f}s " + _summarize(path, payload))


def _summarize(path, payload):
    if payload.get("error"):
        return "error_present=True"
    if path == "/v1/messages":
        return f"stop_reason={payload.get('stop_reason')} model={payload.get('model')}"
    choice = (payload.get("choices") or [{}])[0]
    return f"finish={choice.get('finish_reason')} model={payload.get('model')}"


post(
    "/v1/messages",
    {
        "model": "claude-haiku-5-5",
        "max_tokens": 32,
        "messages": [
            {"role": "user", "content": "Health probe: reply with exactly OK"}
        ],
    },
    {"x-api-key": key, "anthropic-version": "2023-06-01"},
    "CLAUDE",
)
time.sleep(3)
post(
    "/v1/chat/completions",
    {
        "model": "gemini-3.8-flash-high",
        "max_tokens": 32,
        "messages": [
            {"role": "user", "content": "Health probe: reply with exactly OK"}
        ],
    },
    {"Authorization": "Bearer " + key},
    "GEMINI",
)
time.sleep(3)
post(
    "/v1/chat/completions",
    {
        "model": "deepseek-v4.1-flash",
        "max_tokens": 64,
        "messages": [
            {"role": "user", "content": "Health probe: reply with exactly OK"}
        ],
    },
    {"Authorization": "Bearer " + key},
    "DEEPSEEK",
)

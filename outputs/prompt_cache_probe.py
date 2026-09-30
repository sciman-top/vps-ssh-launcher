#!/usr/bin/env python3
"""Prompt-cache A/B probe for the fq CPA gateway (2026-10-01).

Desktop Codex turns carry 37k-49k input tokens. If the gateway defeats upstream
prompt caching, every turn pays a full prefill and the session feels slow even
though streaming is healthy. This probe sends the SAME payload twice and reports
`cached_input_tokens` for each response, so cache preservation can be judged
directly.

Stdlib only; never prints credentials.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

PROMPT = os.environ.get(
    "PROBE_PROMPT",
    "You are a helpful assistant. " + ("Context padding sentence. " * 400) +
    "Now answer with exactly one word: ready",
)
MODEL = os.environ.get("PROBE_MODEL", "gpt-6-luna")
MAXTOK = int(os.environ.get("PROBE_MAX_TOKENS", "32"))
ROUNDS = int(os.environ.get("PROBE_ROUNDS", "3"))


def one(label, url, key, payload):
    handlers = [urllib.request.ProxyHandler({})]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)

    body = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    t0 = time.monotonic()
    try:
        resp = opener.open(req, timeout=300)
    except urllib.error.HTTPError as exc:
        print(json.dumps({"label": label, "http_status": exc.code,
                          "error": exc.read()[:160].decode("utf-8", "replace")}), flush=True)
        return None
    t_head = time.monotonic()
    raw = b""
    first_text = None
    while True:
        c = resp.read1(1 << 16)
        if not c:
            break
        raw += c
        if first_text is None and b"response.output_text.delta" in c:
            first_text = round((time.monotonic() - t0) * 1000)
    t_end = time.monotonic()

    usage = {}
    for line in raw.split(b"\n"):
        if not line.startswith(b"data:"):
            continue
        try:
            obj = json.loads(line[5:].strip())
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("type") == "response.completed":
            usage = (obj.get("response") or {}).get("usage") or {}

    out = {
        "label": label,
        "headers_ms": round((t_head - t0) * 1000),
        "first_text_ms": first_text,
        "total_ms": round((t_end - t0) * 1000),
        "input_tokens": usage.get("input_tokens"),
        # OpenAI Responses 把缓存命中放在 input_tokens_details.cached_tokens；
        # 有些实现直接放在顶层 cached_tokens。两个都读。
        "cached_input_tokens": (
            (usage.get("input_tokens_details") or {}).get("cached_tokens")
            if (usage.get("input_tokens_details") or {}).get("cached_tokens") is not None
            else usage.get("cached_tokens")
        ),
        "output_tokens": usage.get("output_tokens"),
        "usage_keys": sorted(usage.keys()),
    }
    print(json.dumps(out), flush=True)
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "public"
    if mode == "public":
        url = os.environ["PROBE_PUBLIC_URL"]
        key = os.environ["PROBE_KEY"]
    else:
        port = os.environ.get("PROBE_SIDECAR_PORT", "10909")
        url = f"http://127.0.0.1:{port}/v1/responses"
        key = os.environ["PROBE_SIDECAR_KEY"]

    payload = {
        "model": MODEL,
        "input": PROMPT,
        "stream": True,
        "max_output_tokens": MAXTOK,
        "store": False,
    }

    rows = []
    for i in range(ROUNDS):
        # identical payload every round -> upstream prefix cache should hit from round 2
        r = one(f"round{i + 1}", url, key, dict(payload))
        if r:
            rows.append(r)
        time.sleep(2)

    print("--- summary ---", flush=True)
    print(json.dumps({
        "model": MODEL,
        "prompt_chars": len(PROMPT),
        "input_tokens": [r["input_tokens"] for r in rows],
        "cached_input_tokens": [r["cached_input_tokens"] for r in rows],
        "first_text_ms": [r["first_text_ms"] for r in rows],
        "total_ms": [r["total_ms"] for r in rows],
        "cache_preserved": bool(rows and any((r["cached_input_tokens"] or 0) > 0 for r in rows[1:])),
    }), flush=True)


if __name__ == "__main__":
    main()

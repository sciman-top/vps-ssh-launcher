#!/usr/bin/env python3
"""Reasoning-effort A/B probe for the fq CPA gateway (2026-09-29).

Quantifies how much `reasoning.effort` inflates first-byte latency and total
turn time on the ChatGPT-OAuth lane, so the desktop-side
`model_reasoning_effort` setting can be chosen from numbers instead of feel.

One streaming turn per effort level, sequential (never concurrent: they share
one admission lane). Stdlib only, never prints credentials.
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
    "A train travels 120 km in 1.5 hours. What is its average speed in km/h? "
    "Answer with the number only.",
)
MODEL = os.environ.get("PROBE_MODEL", "gpt-6-luna")
MAXTOK = int(os.environ.get("PROBE_MAX_TOKENS", "512"))


def run(label, url, key, effort, proxy="", host=None):
    handlers = [
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    ]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)

    payload = {
        "model": MODEL,
        "input": PROMPT,
        "stream": True,
        "max_output_tokens": MAXTOK,
        "store": False,
    }
    if effort:
        payload["reasoning"] = {"effort": effort}
    body = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if host:
        headers["Host"] = host
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    t0 = time.monotonic()
    try:
        resp = opener.open(req, timeout=300)
    except urllib.error.HTTPError as exc:
        print(
            json.dumps(
                {
                    "effort": effort,
                    "http_status": exc.code,
                    "elapsed_ms": round((time.monotonic() - t0) * 1000),
                    "error_body": exc.read()[:180].decode("utf-8", "replace"),
                }
            ),
            flush=True,
        )
        return
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"effort": effort, "exception": type(exc).__name__}), flush=True)
        return

    t_head = time.monotonic()
    raw = b""
    first_text_ms = None
    while True:
        c = resp.read1(1 << 16)
        if not c:
            break
        raw += c
        if first_text_ms is None and b"response.output_text.delta" in c:
            first_text_ms = round((time.monotonic() - t0) * 1000)
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

    print(
        json.dumps(
            {
                "effort": effort,
                "model": MODEL,
                "http_status": resp.status,
                "headers_ms": round((t_head - t0) * 1000),
                "first_text_ms": first_text_ms,
                "total_ms": round((t_end - t0) * 1000),
                "bytes": len(raw),
                "reasoning_tokens": (usage.get("output_tokens_details") or {}).get(
                    "reasoning_tokens"
                ),
                "output_tokens": usage.get("output_tokens"),
            }
        ),
        flush=True,
    )


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "public"
    efforts = (sys.argv[2].split(",") if len(sys.argv) > 2 else
               ["minimal", "low", "medium", "high", "xhigh"])
    if mode == "public":
        url = os.environ["PROBE_PUBLIC_URL"]
        key = os.environ["PROBE_KEY"]
        proxy = os.environ.get("PROBE_PROXY", "")
        host = None
    elif mode == "sidecar":
        port = os.environ.get("PROBE_SIDECAR_PORT", "10909")
        url = f"http://127.0.0.1:{port}/v1/responses"
        key = os.environ["PROBE_SIDECAR_KEY"]
        proxy = ""
        host = None
    else:
        raise SystemExit("mode must be public|sidecar")

    for effort in efforts:
        run(effort, url, key, effort, proxy=proxy, host=host)
        time.sleep(4)


if __name__ == "__main__":
    main()

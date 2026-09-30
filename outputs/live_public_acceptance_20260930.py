"""Controlled live acceptance on the public gateway path (bwg CPA).

Reads the capability path and the client key ON THE REMOTE ONLY and never
prints either. Sends one bounded streaming /v1/responses turn per model so the
OAuth lane (ChatGPT Plus) is exercised through nginx -> admission -> CPA.

Usage: python3 live_public_acceptance_20260930.py [model ...]
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

NGINX_CONF = Path("/etc/nginx/conf.d/cpa-gateway.conf")
CPA_CONFIG = Path("/opt/cliproxyapi/config.yaml")


def load_target() -> str:
    conf = NGINX_CONF.read_text(encoding="utf-8")
    prefix = re.search(r"/([0-9a-f]{16})/v1/", conf).group(1)
    key = yaml.safe_load(CPA_CONFIG.read_text(encoding="utf-8"))["api-keys"][0]
    return f"https://127.0.0.1:8443/{prefix}/v1/responses", key


def one(url: str, key: str, model: str, ctx: ssl.SSLContext) -> dict:
    body = json.dumps(
        {
            "model": model,
            "stream": True,
            "input": "Reply with exactly the token PONG and nothing else.",
            "max_output_tokens": 24,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
            headers_ms = int((time.time() - started) * 1000)
            events = 0
            first_ms = None
            completed = False
            for raw in resp:
                if not raw.strip():
                    continue
                events += 1
                if first_ms is None:
                    first_ms = int((time.time() - started) * 1000)
                if b"response.completed" in raw:
                    completed = True
            return {
                "model": model,
                "status": resp.status,
                "headers_ms": headers_ms,
                "first_event_ms": first_ms,
                "total_ms": int((time.time() - started) * 1000),
                "events": events,
                "completed": completed,
            }
    except urllib.error.HTTPError as exc:
        return {
            "model": model,
            "status": exc.code,
            "retry_after": exc.headers.get("Retry-After"),
            "total_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - probe reports the class only
        return {"model": model, "error": type(exc).__name__}


def main() -> int:
    models = sys.argv[1:] or ["gpt-6-luna", "gpt-6.1-sol"]
    url, key = load_target()
    ctx = ssl._create_unverified_context()
    print("PROBE_BUDGET models=%s requests=%d" % (",".join(models), len(models)))
    for model in models:
        print("RESULT " + json.dumps(one(url, key, model, ctx), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Local end-to-end streaming probe (user desktop path) - 2026-09-29.

Runs from the user's Windows machine against the public fq gateway and measures
header TTFB, first-chunk time and inter-chunk cadence for one streaming turn,
either direct (proxy bypassed) or through the local Xray HTTP proxy.

URL, key and path prefix come from environment variables (never printed):
  PROBE_URL / PROBE_KEY   (required)
  PROBE_PROXY             (optional, e.g. http://127.0.0.1:10809)
  PROBE_MODEL             (default gpt-6-luna)
  PROBE_MAX_TOKENS        (default 192)
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.request

MODEL = os.environ.get("PROBE_MODEL", "gpt-6-luna")
MAXTOK = int(os.environ.get("PROBE_MAX_TOKENS", "192"))
PROMPT = "Count from 1 to 30 in words, one per line."


def main():
    url = os.environ["PROBE_URL"]
    key = os.environ["PROBE_KEY"]
    proxy = os.environ.get("PROBE_PROXY", "")
    handlers = []
    if proxy:
        handlers.append(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)

    body = json.dumps(
        {
            "model": MODEL,
            "input": PROMPT,
            "stream": True,
            "max_output_tokens": MAXTOK,
            "store": False,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    t0 = time.monotonic()
    try:
        resp = opener.open(req, timeout=300)
    except urllib.error.HTTPError as exc:
        body = exc.read(240).decode("utf-8", "replace")
        print(
            json.dumps(
                {
                    "model": MODEL,
                    "via_proxy": bool(proxy),
                    "http_status": exc.code,
                    "elapsed_ms": round((time.monotonic() - t0) * 1000),
                    "retry_after": exc.headers.get("Retry-After"),
                    "error_body": body,
                }
            ),
            flush=True,
        )
        return
    except (urllib.error.URLError, TimeoutError) as exc:
        print(
            json.dumps(
                {
                    "model": MODEL,
                    "via_proxy": bool(proxy),
                    "exception": type(exc).__name__,
                    "elapsed_ms": round((time.monotonic() - t0) * 1000),
                }
            ),
            flush=True,
        )
        return
    t_head = time.monotonic()
    arrivals = []
    while True:
        chunk = resp.read1(65536)
        if not chunk:
            break
        arrivals.append((round((time.monotonic() - t0) * 1000), len(chunk)))
    t_end = time.monotonic()
    gaps = sorted(arrivals[i][0] - arrivals[i - 1][0] for i in range(1, len(arrivals)))
    print(
        json.dumps(
            {
                "model": MODEL,
                "via_proxy": bool(proxy),
                "http_status": resp.status,
                "headers_ms": round((t_head - t0) * 1000),
                "first_chunk_ms": arrivals[0][0] if arrivals else None,
                "total_ms": round((t_end - t0) * 1000),
                "bytes": sum(a[1] for a in arrivals),
                "chunks": len(arrivals),
                "gap_p50_ms": gaps[len(gaps) // 2] if gaps else 0,
                "gap_p90_ms": gaps[int(len(gaps) * 0.9)] if gaps else 0,
                "gap_max_ms": gaps[-1] if gaps else 0,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Local client-chain streaming probe (user desktop path) - 2026-09-29.

Measures the SAME streaming turn across every local hop so slow token delivery
can be attributed to: cockpit sidecar (CLIProxyAPI fork) / local Xray proxy /
public nginx gateway / upstream generation.

Targets (selected by argv[1]):
  sidecar   -> http://127.0.0.1:<PROBE_SIDECAR_PORT>/v1/responses  (Direct API)
  public    -> PROBE_PUBLIC_URL  direct, proxy bypassed
  publicpx  -> PROBE_PUBLIC_URL  through PROBE_PROXY (local Xray)
  xray      -> probe only the Xray hop with a trivial TLS fetch

Stdlib only. Never prints credentials. One streaming turn per target.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

PROMPT = os.environ.get(
    "PROBE_PROMPT", "Count from 1 to 40 in words, one per line, no extra text."
)
MODEL = os.environ.get("PROBE_MODEL", "gpt-6-luna")
MAXTOK = int(os.environ.get("PROBE_MAX_TOKENS", "256"))


def build_opener(proxy: str):
    if proxy:
        handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})]
    else:
        handlers = [urllib.request.ProxyHandler({})]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def measure(label, url, key, proxy="", host=None, model=MODEL):
    opener = build_opener(proxy)
    body = json.dumps(
        {
            "model": model,
            "input": PROMPT,
            "stream": True,
            "max_output_tokens": MAXTOK,
            "store": False,
        }
    ).encode()
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
                    "target": label,
                    "http_status": exc.code,
                    "elapsed_ms": round((time.monotonic() - t0) * 1000),
                    "retry_after": exc.headers.get("Retry-After"),
                    "error_body": exc.read()[:200].decode("utf-8", "replace"),
                }
            ),
            flush=True,
        )
        return
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "target": label,
                    "exception": type(exc).__name__,
                    "message": str(exc)[:160],
                }
            ),
            flush=True,
        )
        return

    t_head = time.monotonic()
    arrivals = []
    first_event = None
    tail = b""
    while True:
        chunk = resp.read1(65536)
        if not chunk:
            break
        arrivals.append((round((time.monotonic() - t0) * 1000), len(chunk)))
        if first_event is None:
            tail = (tail + chunk)[:8192]
            for line in tail.split(b"\n"):
                if line.startswith(b"event:"):
                    first_event = line.decode("utf-8", "replace").strip()
                    break
    t_end = time.monotonic()
    gaps = sorted(arrivals[i][0] - arrivals[i - 1][0] for i in range(1, len(arrivals)))
    total_bytes = sum(a[1] for a in arrivals)
    print(
        json.dumps(
            {
                "target": label,
                "model": model,
                "http_status": resp.status,
                "headers_ms": round((t_head - t0) * 1000),
                "first_chunk_ms": arrivals[0][0] if arrivals else None,
                "total_ms": round((t_end - t0) * 1000),
                "bytes": total_bytes,
                "chunks": len(arrivals),
                "gap_p50_ms": gaps[len(gaps) // 2] if gaps else 0,
                "gap_p90_ms": gaps[int(len(gaps) * 0.9)] if gaps else 0,
                "gap_max_ms": gaps[-1] if gaps else 0,
                "first_event": first_event,
                "upstream_hdr": resp.headers.get("X-Upstream-Header-Time"),
                "server": resp.headers.get("Server"),
            }
        ),
        flush=True,
    )


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "sidecar"
    proxy = os.environ.get("PROBE_PROXY", "")
    if mode == "sidecar":
        port = os.environ.get("PROBE_SIDECAR_PORT", "10909")
        key = os.environ["PROBE_SIDECAR_KEY"]
        measure("sidecar-10909", f"http://127.0.0.1:{port}/v1/responses", key)
    elif mode == "public":
        measure("public-8443-direct", os.environ["PROBE_PUBLIC_URL"], os.environ["PROBE_KEY"])
    elif mode == "publicpx":
        measure(
            "public-8443-via-xray",
            os.environ["PROBE_PUBLIC_URL"],
            os.environ["PROBE_KEY"],
            proxy=proxy,
        )
    elif mode == "tls":
        # plain TLS+models fetch to isolate transport overhead
        measure(
            "models-via-xray",
            os.environ["PROBE_MODELS_URL"],
            os.environ["PROBE_KEY"],
            proxy=proxy,
            model="",
        )
    else:
        raise SystemExit("unknown mode")


if __name__ == "__main__":
    main()

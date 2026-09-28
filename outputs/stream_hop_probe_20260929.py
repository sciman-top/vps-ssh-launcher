#!/usr/bin/env python3
"""Controlled streaming hop probe for the bwg CPA chain (2026-09-29).

One-shot per (model, hop), sequential, small bounded prompts. Measures header
TTFB, first-chunk time, inter-chunk cadence and burstiness so slow token
delivery can be attributed to CPA / admission / nginx / upstream.

Stdlib only. Never prints credentials. Consumes one OAuth turn per luna hop.
"""

import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

CONF = "/opt/cliproxyapi/config.yaml"
NGINX = "/etc/nginx/conf.d/cpa-gateway.conf"

PROMPT_TINY = "Count from 1 to 12, one number per line."
PROMPT_MID = "Count from 1 to 40 in words, one per line."


def gateway_key():
    text = open(CONF, encoding="utf-8").read()
    m = re.search(r"api-keys:\s*\n\s*-\s*([A-Za-z0-9_.\-]+)", text)
    if not m:
        m = re.search(r"api-keys:\s*\[?([A-Za-z0-9_.\-]+)", text)
    if not m:
        raise SystemExit("NO_KEY")
    return m.group(1)


def public_path():
    text = open(NGINX, encoding="utf-8").read()
    m = re.search(r"\^/([A-Za-z0-9_-]{8,})/v1/", text)
    if not m:
        raise SystemExit("NO_PATH")
    return m.group(1)


def probe(label, model, prompt, max_out, url, key, host=None):
    body = json.dumps(
        {
            "model": model,
            "input": prompt,
            "stream": True,
            "max_output_tokens": max_out,
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
            **({"Host": host} if host else {}),
        },
        method="POST",
    )
    ctx = None
    if url.startswith("https"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    t0 = time.monotonic()
    try:
        resp = urllib.request.urlopen(req, timeout=300, context=ctx)
    except urllib.error.HTTPError as exc:
        print(
            json.dumps(
                {
                    "target": label,
                    "model": model,
                    "http_status": exc.code,
                    "elapsed_ms": round((time.monotonic() - t0) * 1000),
                    "error_body": exc.read()[:220].decode("utf-8", "replace"),
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
                    "model": model,
                    "exception": type(exc).__name__,
                    "message": str(exc)[:160],
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

    gaps = sorted(
        arrivals[i][0] - arrivals[i - 1][0] for i in range(1, len(arrivals))
    )
    total_bytes = sum(a[1] for a in arrivals)
    # burstiness: share of bytes arriving in the top decile of gaps
    tail = 0.0
    if arrivals and total_bytes:
        cutoff = t_end_marker = arrivals[-1][0]
        tail = sum(b for (t, b) in arrivals if t >= cutoff - 1)
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
                "final_byte_ms": arrivals[-1][0] if arrivals else None,
            }
        ),
        flush=True,
    )


def main():
    key = gateway_key()
    path = public_path()
    model = sys.argv[1] if len(sys.argv) > 1 else "luna"
    if model == "luna":
        hops = [
            ("cpa-direct-8317", "http://127.0.0.1:8317/v1/responses", None),
            ("admission-8318", "http://127.0.0.1:8318/v1/responses", None),
            (
                "public-8443",
                f"https://127.0.0.1:8443/{path}/v1/responses",
                "fq.sciman.top",
            ),
        ]
        for label, url, host in hops:
            prompt = PROMPT_MID
            probe(label, "gpt-6-luna", prompt, 256, url, key, host)
            time.sleep(4)
    elif model == "glm":
        for label, url, host in [
            ("cpa-direct-8317", "http://127.0.0.1:8317/v1/responses", None),
            ("admission-8318", "http://127.0.0.1:8318/v1/responses", None),
            (
                "public-8443",
                f"https://127.0.0.1:8443/{path}/v1/responses",
                "fq.sciman.top",
            ),
        ]:
            probe(label, "glm-5.3-flash", PROMPT_TINY, 64, url, key, host)
            time.sleep(3)
    else:
        raise SystemExit("unknown model group")


if __name__ == "__main__":
    main()

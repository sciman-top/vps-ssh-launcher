#!/usr/bin/env python3
"""Read-only streaming latency probe for the bwg CPA chain.

Measures, per hop, time-to-headers, time-to-first-chunk, inter-chunk gaps and
total duration for a single `gpt-6-luna` streaming turn. Consumes one OAuth turn
per target. Never prints credentials.
"""

import json
import ssl
import sys
import time
import urllib.error
import urllib.request

import yaml

CONF = "/opt/cliproxyapi/config.yaml"
NGINX = "/etc/nginx/conf.d/cpa-gateway.conf"

PROMPT = "List the numbers 1 through 40 in words, separated by single spaces."
BODY = json.dumps(
    {
        "model": "gpt-6-luna",
        "input": PROMPT,
        "stream": True,
        "max_output_tokens": 400,
    }
).encode()


def api_key() -> str:
    cfg = yaml.safe_load(open(CONF, encoding="utf-8"))
    keys = cfg.get("api-keys") or []
    if not keys:
        raise SystemExit("NO_KEY")
    return keys[0] if isinstance(keys[0], str) else keys[0]["key"]


def public_path() -> str:
    import re

    text = open(NGINX, encoding="utf-8").read()
    match = re.search(r"\^/([A-Za-z0-9_-]{8,})/v1/", text)
    if not match:
        raise SystemExit("NO_PATH")
    return match.group(1)


def probe(label, url, key, insecure=False, host=None):
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if host:
        headers["Host"] = host
    ctx = None
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, data=BODY, headers=headers, method="POST")
    t0 = time.monotonic()
    try:
        resp = urllib.request.urlopen(req, timeout=300, context=ctx)
    except urllib.error.HTTPError as exc:
        body = exc.read()[:400]
        print(
            json.dumps(
                {
                    "target": label,
                    "http_status": exc.code,
                    "error_body": body.decode("utf-8", "replace")[:300],
                    "elapsed_ms": round((time.monotonic() - t0) * 1000),
                }
            )
        )
        return None
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "target": label,
                    "exception": type(exc).__name__,
                    "message": str(exc)[:200],
                    "elapsed_ms": round((time.monotonic() - t0) * 1000),
                }
            )
        )
        return None

    t_headers = time.monotonic()
    arrivals = []
    total = 0
    first_bytes = b""
    while True:
        chunk = resp.read1(65536)
        if not chunk:
            break
        now = time.monotonic()
        arrivals.append((round((now - t0) * 1000), len(chunk)))
        if not first_bytes:
            first_bytes = chunk[:400]
        total += len(chunk)
    t_end = time.monotonic()

    deltas = []
    for i in range(1, len(arrivals)):
        deltas.append(arrivals[i][0] - arrivals[i - 1][0])
    deltas_sorted = sorted(deltas)
    n = len(deltas_sorted)

    def pct(p):
        if not n:
            return 0
        return deltas_sorted[min(n - 1, int(n * p))]

    text = first_bytes.decode("utf-8", "replace")
    result = {
        "target": label,
        "http_status": resp.status,
        "headers_ms": round((t_headers - t0) * 1000),
        "first_chunk_ms": arrivals[0][0] if arrivals else None,
        "total_ms": round((t_end - t0) * 1000),
        "bytes": total,
        "chunks": len(arrivals),
        "gap_p50_ms": pct(0.5),
        "gap_p90_ms": pct(0.9),
        "gap_max_ms": deltas_sorted[-1] if n else 0,
        "first_event": text.split("\n")[0][:120],
        "has_done": "[DONE]" in first_bytes.decode("utf-8", "replace")
        or b"response.completed" in first_bytes,
    }
    print(json.dumps(result))
    return result


def main():
    key = api_key()
    path = public_path()
    targets = sys.argv[1:] or ["cpa", "admission", "public"]
    if "cpa" in targets:
        probe("cpa-direct-8317", "http://127.0.0.1:8317/v1/responses", key)
        time.sleep(2)
    if "admission" in targets:
        probe("admission-8318", "http://127.0.0.1:8318/v1/responses", key)
        time.sleep(2)
    if "public" in targets:
        probe(
            "public-8443",
            f"https://127.0.0.1:8443/{path}/v1/responses",
            key,
            insecure=True,
            host="fq.sciman.top",
        )


if __name__ == "__main__":
    main()

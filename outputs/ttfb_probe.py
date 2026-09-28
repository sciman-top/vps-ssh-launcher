#!/usr/bin/env python3
"""Flexible streaming TTFB probe (read-only, consumes one turn per target)."""

import json
import sys
import time
import urllib.error
import urllib.request

import yaml

CONF = "/opt/cliproxyapi/config.yaml"


def api_key() -> str:
    cfg = yaml.safe_load(open(CONF, encoding="utf-8"))
    keys = cfg.get("api-keys") or []
    return keys[0] if isinstance(keys[0], str) else keys[0]["key"]


def probe(label, model, prompt, max_out, base):
    body = json.dumps(
        {"model": model, "input": prompt, "stream": True, "max_output_tokens": max_out}
    ).encode()
    req = urllib.request.Request(
        f"{base}/v1/responses",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    t0 = time.monotonic()
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as exc:
        print(
            json.dumps(
                {
                    "label": label,
                    "model": model,
                    "http_status": exc.code,
                    "elapsed_ms": round((time.monotonic() - t0) * 1000),
                    "body": exc.read()[:200].decode("utf-8", "replace"),
                }
            )
        )
        return
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"label": label, "model": model, "exc": type(exc).__name__}))
        return
    t_head = time.monotonic()
    arrivals = []
    first = b""
    while True:
        chunk = resp.read1(65536)
        if not chunk:
            break
        arrivals.append((round((time.monotonic() - t0) * 1000), len(chunk)))
        if not first:
            first = chunk[:200]
    t_end = time.monotonic()
    gaps = [arrivals[i][0] - arrivals[i - 1][0] for i in range(1, len(arrivals))]
    gaps.sort()
    print(
        json.dumps(
            {
                "label": label,
                "model": model,
                "http_status": resp.status,
                "headers_ms": round((t_head - t0) * 1000),
                "first_chunk_ms": arrivals[0][0] if arrivals else None,
                "total_ms": round((t_end - t0) * 1000),
                "bytes": sum(a[1] for a in arrivals),
                "chunks": len(arrivals),
                "gap_p50": gaps[len(gaps) // 2] if gaps else 0,
                "gap_max": gaps[-1] if gaps else 0,
                "first_event": first.decode("utf-8", "replace").split("\n")[0][:80],
            }
        )
    )


if __name__ == "__main__":
    which = sys.argv[1]
    base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8317"
    if which == "glm":
        probe("glm-5.3-flash", "glm-5.3-flash", "Say the word OK.", 16, base)
    elif which == "luna-tiny":
        probe("luna-tiny", "gpt-6-luna", "Say the word OK.", 16, base)
    elif which == "luna-mid":
        probe(
            "luna-mid",
            "gpt-6-luna",
            "Write a haiku about a lighthouse.",
            120,
            base,
        )

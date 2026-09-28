#!/usr/bin/env python3
"""Controlled A/B probe: N concurrent requests against one shared admission lane.

Reports per-request time-to-first-byte and total duration so the cost of the
lane's `max_inflight` bound is measurable rather than inferred. Read-only apart
from the turns it consumes. Never prints credentials.
"""

import json
import sys
import threading
import time
import urllib.error
import urllib.request

import yaml

CONF = "/opt/cliproxyapi/config.yaml"


def api_key() -> str:
    cfg = yaml.safe_load(open(CONF, encoding="utf-8"))
    keys = cfg.get("api-keys") or []
    if not keys:
        raise SystemExit("NO_KEY")
    return keys[0] if isinstance(keys[0], str) else keys[0]["key"]


def one(label, base, model, prompt, max_out, results):
    body = json.dumps(
        {
            "model": model,
            "input": prompt,
            "stream": True,
            "max_output_tokens": max_out,
        }
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
        results.append(
            {
                "label": label,
                "http_status": exc.code,
                "elapsed_ms": round((time.monotonic() - t0) * 1000),
                "body": exc.read()[:200].decode("utf-8", "replace"),
            }
        )
        return
    except Exception as exc:  # noqa: BLE001
        results.append({"label": label, "exc": type(exc).__name__})
        return
    t_head = time.monotonic()
    chunks = 0
    while True:
        chunk = resp.read1(65536)
        if not chunk:
            break
        chunks += 1
    t_end = time.monotonic()
    results.append(
        {
            "label": label,
            "http_status": resp.status,
            "ttfb_ms": round((t_head - t0) * 1000),
            "total_ms": round((t_end - t0) * 1000),
            "chunks": chunks,
        }
    )


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8318"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    model = sys.argv[3] if len(sys.argv) > 3 else "gpt-6-luna"
    prompt = "Write one short sentence about a lighthouse."
    results: list = []
    threads = [
        threading.Thread(
            target=one,
            args=(f"c{i + 1}", base, model, prompt, 60, results),
            daemon=True,
        )
        for i in range(count)
    ]
    started = time.monotonic()
    for thread in threads:
        thread.start()
        time.sleep(0.15)  # stagger so the arrival order is deterministic
    for thread in threads:
        thread.join(timeout=300)
    wall = round((time.monotonic() - started) * 1000)
    for item in sorted(results, key=lambda r: r.get("label", "")):
        print(json.dumps(item))
    ttfb = sorted(
        item["ttfb_ms"] for item in results if "ttfb_ms" in item
    )
    print(
        json.dumps(
            {
                "summary": {
                    "concurrency": count,
                    "ok": len(ttfb),
                    "wall_ms": wall,
                    "ttfb_min_ms": ttfb[0] if ttfb else None,
                    "ttfb_max_ms": ttfb[-1] if ttfb else None,
                    "errors": [r for r in results if r.get("http_status") != 200],
                }
            }
        )
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Raw SSE framing capture: sidecar (10909) vs CPA-direct vs public.

Prints the event-type histogram and delta counts for one streaming turn so we can
tell whether a hop streams token-by-token or delivers a buffered burst.
Never prints credentials.
"""

import collections
import json
import os
import ssl
import sys
import time
import urllib.request

PROMPT = os.environ.get("PROBE_PROMPT", "List 8 colors, one per line.")
MODEL = os.environ.get("PROBE_MODEL", "gpt-6-luna")
MAXTOK = int(os.environ.get("PROBE_MAX_TOKENS", "96"))


def run(label, url, key, proxy="", host=None):
    handlers = [
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    ]
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
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"target": label, "exception": type(exc).__name__, "msg": str(exc)[:120]}))
        return
    t_head = time.monotonic()
    raw = b""
    arrivals = []
    while True:
        c = resp.read1(1 << 16)
        if not c:
            break
        raw += c
        arrivals.append(round((time.monotonic() - t0) * 1000))
    t_end = time.monotonic()

    ev = collections.Counter()
    delta_chars = 0
    delta_events = 0
    for line in raw.split(b"\n"):
        if line.startswith(b"event:"):
            ev[line[6:].strip().decode("utf-8", "replace")] += 1
        elif line.startswith(b"data:"):
            payload = line[5:].strip()
            if payload == b"[DONE]":
                ev["__done__"] += 1
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            t = obj.get("type")
            if t == "response.output_text.delta":
                delta_events += 1
                delta_chars += len(obj.get("delta", "") or "")
    print(
        json.dumps(
            {
                "target": label,
                "status": resp.status,
                "headers_ms": round((t_head - t0) * 1000),
                "total_ms": round((t_end - t0) * 1000),
                "bytes": len(raw),
                "socket_reads": len(arrivals),
                "read_gap_p50": (sorted(arrivals[i] - arrivals[i - 1] for i in range(1, len(arrivals)))[len(arrivals) // 2] if len(arrivals) > 1 else 0),
                "delta_events": delta_events,
                "delta_chars": delta_chars,
                "top_events": ev.most_common(8),
                "body_head": raw[:120].decode("utf-8", "replace"),
                "body_tail": raw[-160:].decode("utf-8", "replace"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return {
        "headers_ms": round((t_head - t0) * 1000),
        "total_ms": round((t_end - t0) * 1000),
        "socket_reads": len(arrivals),
        "read_gap_p50": (sorted(arrivals[i] - arrivals[i - 1] for i in range(1, len(arrivals)))[len(arrivals) // 2] if len(arrivals) > 1 else 0),
        "delta_events": delta_events,
        "delta_chars": delta_chars,
    }


def evaluate(m: dict) -> tuple[bool, str]:
    """Acceptance thresholds for the 'sidecar must stream like the upstream' fix.

    Override via env: PROBE_MAX_HEADERS_MS / PROBE_MIN_READS / PROBE_MIN_GAP_P50
    / PROBE_MIN_DELTA_EVENTS.
    """
    max_headers = int(os.environ.get("PROBE_MAX_HEADERS_MS", "2000"))
    min_reads = int(os.environ.get("PROBE_MIN_READS", "20"))
    min_gap = int(os.environ.get("PROBE_MIN_GAP_P50", "1"))
    min_deltas = int(os.environ.get("PROBE_MIN_DELTA_EVENTS", "1"))
    fails = []
    if m["headers_ms"] > max_headers:
        fails.append(f"headers_ms={m['headers_ms']}>{max_headers}")
    if m["socket_reads"] < min_reads:
        fails.append(f"socket_reads={m['socket_reads']}<{min_reads}")
    if m["read_gap_p50"] < min_gap:
        fails.append(f"read_gap_p50={m['read_gap_p50']}<{min_gap}")
    if m["delta_events"] < min_deltas:
        fails.append(f"delta_events={m['delta_events']}<{min_deltas}")
    if fails:
        return False, ",".join(fails)
    return True, (
        f"headers_ms={m['headers_ms']}<={max_headers} "
        f"socket_reads={m['socket_reads']}>={min_reads} "
        f"read_gap_p50={m['read_gap_p50']}>={min_gap}"
    )


def main() -> int:
    mode = sys.argv[1]
    if mode == "sidecar":
        metrics = run(
            "sidecar-10909",
            f"http://127.0.0.1:{os.environ.get('PROBE_SIDECAR_PORT','10909')}/v1/responses",
            os.environ["PROBE_SIDECAR_KEY"],
        )
    elif mode == "public":
        metrics = run("public-8443", os.environ["PROBE_PUBLIC_URL"], os.environ["PROBE_KEY"])
    elif mode == "publicpx":
        metrics = run(
            "public-8443-xray",
            os.environ["PROBE_PUBLIC_URL"],
            os.environ["PROBE_KEY"],
            proxy=os.environ.get("PROBE_PROXY", ""),
        )
    else:
        raise SystemExit("unknown mode")

    if not os.environ.get("PROBE_ASSERT"):
        return 0
    if not metrics:
        print("ACCEPTANCE_RESULT=FAIL reason=no_metrics", flush=True)
        return 1
    ok, detail = evaluate(metrics)
    print(f"ACCEPTANCE_RESULT={'PASS' if ok else 'FAIL'} {detail}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

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
    delta_times = []
    delta_chars = 0
    ev = collections.Counter()
    pending = b""
    # 边读边解析：记录每个 delta 是由哪一次 socket 读取送达的 ——
    # 这才是客户端实际感知到的「token 吐出节奏」。
    while True:
        c = resp.read1(1 << 16)
        if not c:
            break
        raw += c
        now_ms = round((time.monotonic() - t0) * 1000)
        arrivals.append(now_ms)
        pending += c
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            line = line.rstrip(b"\r")
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
                if obj.get("type") == "response.output_text.delta":
                    delta_times.append(now_ms)
                    delta_chars += len(obj.get("delta", "") or "")
    t_end = time.monotonic()

    delta_events = len(delta_times)
    delta_span = (delta_times[-1] - delta_times[0]) if delta_events > 1 else 0
    headers_ms = round((t_head - t0) * 1000)
    total_ms = round((t_end - t0) * 1000)
    print(
        json.dumps(
            {
                "target": label,
                "status": resp.status,
                "headers_ms": headers_ms,
                "total_ms": total_ms,
                "bytes": len(raw),
                "socket_reads": len(arrivals),
                "read_gap_p50": (sorted(arrivals[i] - arrivals[i - 1] for i in range(1, len(arrivals)))[len(arrivals) // 2] if len(arrivals) > 1 else 0),
                "delta_events": delta_events,
                "delta_chars": delta_chars,
                "first_delta_ms": delta_times[0] if delta_times else None,
                "delta_span_ms": delta_span,
                "ms_per_delta": round(delta_span / max(1, delta_events - 1)) if delta_events > 1 else None,
                "top_events": ev.most_common(8),
                "body_head": raw[:120].decode("utf-8", "replace"),
                "body_tail": raw[-160:].decode("utf-8", "replace"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return {
        "headers_ms": headers_ms,
        "total_ms": total_ms,
        "socket_reads": len(arrivals),
        "read_gap_p50": (sorted(arrivals[i] - arrivals[i - 1] for i in range(1, len(arrivals)))[len(arrivals) // 2] if len(arrivals) > 1 else 0),
        "delta_events": delta_events,
        "delta_chars": delta_chars,
        "first_delta_ms": delta_times[0] if delta_times else 0,
        "delta_span_ms": delta_span,
    }


def evaluate(m: dict) -> tuple[bool, str]:
    """**粗筛**判据（不是决定性判据）。

    实测教训（2026-09-29，两次误判）：
      - 「`headers_ms` < 固定秒数」→ 上游首字节自身就波动 0.7–5.5 s，会误判；
      - 「`headers/total` ≤ 0.5」→ 响应很短很快时天然接近 1，会误判；
      - 「`delta_span/total` ≥ 0.3」→ 上游长时间推理后才快速吐字时跨度天然很小，也会误判。
    缺陷态与修复态的指标分布**有重叠**，任何单一阈值都不可靠。

    ⇒ 本断言只用于**抓粗大故障**：整段响应被憋到流末尾（headers 10–22 s、读取次数个位数）。
      **要判定补丁是否真的还在，请比对已装二进制的 sha256**（见 runbook：
      `sha256sum "$LOCALAPPDATA/Cockpit Tools/cockpit-cliproxy.exe"` 应等于补丁构建的哈希）。
      PASS 是必要不充分条件。

    阈值可用环境变量覆盖：
      PROBE_MAX_HEADERS_MS(8000) / PROBE_MIN_READS(10) / PROBE_MIN_DELTA_EVENTS(1)
    """
    max_headers = int(os.environ.get("PROBE_MAX_HEADERS_MS", "8000"))
    min_reads = int(os.environ.get("PROBE_MIN_READS", "10"))
    min_deltas = int(os.environ.get("PROBE_MIN_DELTA_EVENTS", "1"))
    fails = []
    if m["headers_ms"] > max_headers:
        fails.append(f"headers_ms={m['headers_ms']}>{max_headers}")
    if m["socket_reads"] < min_reads:
        fails.append(f"socket_reads={m['socket_reads']}<{min_reads}")
    if m["delta_events"] < min_deltas:
        fails.append(f"delta_events={m['delta_events']}<{min_deltas}")
    span = m.get("delta_span_ms", 0)
    if fails:
        return False, ",".join(fails)
    return True, (
        f"headers_ms={m['headers_ms']}<={max_headers} "
        f"socket_reads={m['socket_reads']}>={min_reads} "
        f"delta_span_ms={span} (informational)"
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

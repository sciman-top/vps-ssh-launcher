#!/usr/bin/env python3
"""service_tier A/B probe for the fq CPA gateway (2026-09-30).

CPA (`codex_executor_request.go:391 applyCodexRoutingHint`) converts a body
`service_tier` into the `X-Codex-Routing-Hint: model=<slug>;tier=<tier>` header
that native Codex sends to the ChatGPT backend. This probe measures whether
asking for `priority` / `ultrafast` actually lowers first-token latency on the
ChatGPT-OAuth lane.

Interleaved (A/B/A/B) ordering so upstream drift cannot masquerade as a tier
effect. Stdlib only; never prints credentials.
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
MAXTOK = int(os.environ.get("PROBE_MAX_TOKENS", "256"))


def run(label, url, key, tier):
    handlers = [urllib.request.ProxyHandler({})]
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
    if tier:
        payload["service_tier"] = tier
    body = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    t0 = time.monotonic()
    try:
        resp = opener.open(req, timeout=300)
    except urllib.error.HTTPError as exc:
        print(
            json.dumps(
                {
                    "label": label,
                    "service_tier": tier,
                    "http_status": exc.code,
                    "elapsed_ms": round((time.monotonic() - t0) * 1000),
                    "error_body": exc.read()[:200].decode("utf-8", "replace"),
                }
            ),
            flush=True,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"label": label, "service_tier": tier, "exception": type(exc).__name__}), flush=True)
        return None

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
    resp_tier = None
    for line in raw.split(b"\n"):
        if not line.startswith(b"data:"):
            continue
        try:
            obj = json.loads(line[5:].strip())
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("type") == "response.completed":
            r = obj.get("response") or {}
            usage = r.get("usage") or {}
            resp_tier = r.get("service_tier")
        if isinstance(obj, dict) and obj.get("type") == "response.created":
            resp_tier = resp_tier or ((obj.get("response") or {}).get("service_tier"))

    out = {
        "label": label,
        "requested_tier": tier,
        "response_tier": resp_tier,
        "http_status": resp.status,
        "headers_ms": round((t_head - t0) * 1000),
        "first_text_ms": first_text_ms,
        "total_ms": round((t_end - t0) * 1000),
        "bytes": len(raw),
        "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }
    print(json.dumps(out), flush=True)
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "sidecar"
    tiers = (sys.argv[2].split(",") if len(sys.argv) > 2 else ["", "priority", "ultrafast"])
    if mode == "public":
        url = os.environ["PROBE_PUBLIC_URL"]
        key = os.environ["PROBE_KEY"]
    else:
        port = os.environ.get("PROBE_SIDECAR_PORT", "10909")
        url = f"http://127.0.0.1:{port}/v1/responses"
        key = os.environ["PROBE_SIDECAR_KEY"]

    results = {}
    for rnd in range(int(os.environ.get("PROBE_ROUNDS", "1"))):
        for tier in tiers:
            label = f"r{rnd + 1}:{tier or 'default'}"
            out = run(label, url, key, tier)
            if out:
                results.setdefault(tier or "default", []).append(out)
            time.sleep(3)

    print("--- summary ---", flush=True)
    for tier, rows in results.items():
        ft = [r["first_text_ms"] for r in rows if r["first_text_ms"]]
        tt = [r["total_ms"] for r in rows]
        rt = [r["response_tier"] for r in rows]
        print(
            json.dumps(
                {
                    "tier": tier,
                    "n": len(rows),
                    "first_text_ms": ft,
                    "total_ms": tt,
                    "response_tier_echo": rt,
                    "first_text_median": sorted(ft)[len(ft) // 2] if ft else None,
                    "total_median": sorted(tt)[len(tt) // 2] if tt else None,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()

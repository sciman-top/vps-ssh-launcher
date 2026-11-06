"""One-shot controlled acceptance for the admission-capped deepseek route.

Reads the gateway key from the deployed config, sends exactly one streaming
Responses request to the local admission port, and prints a redacted summary.
The key is never printed.
"""

import json
import sys
import time
import urllib.error
import urllib.request

import yaml

CONFIG = "/opt/cliproxyapi/config.yaml"
URL = "http://127.0.0.1:8318/v1/responses"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "deepseek-v4.1-flash"


def gateway_key() -> str:
    config = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    keys = config.get("access", {}).get("api-keys") or []
    if not keys:
        raise SystemExit("no gateway key")
    return keys[0]


def main() -> int:
    body = json.dumps(
        {
            "model": MODEL,
            "input": "Reply with exactly: PONG",
            "stream": True,
        }
    ).encode()
    request = urllib.request.Request(
        URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + gateway_key(),
            "Accept": "text/event-stream",
        },
    )
    started = time.time()
    status = None
    events = 0
    terminal = "none"
    finish = "none"
    text_len = 0
    reason = "-"
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            status = response.status
            reason = response.headers.get("X-CPA-Admission-Reason") or "-"
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    terminal = "done"
                    continue
                events += 1
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                kind = event.get("type")
                if kind == "response.completed":
                    terminal = "completed"
                elif kind == "response.failed":
                    terminal = "failed"
                if kind == "response.output_text.delta":
                    text_len += len(event.get("delta") or "")
                if kind == "response.completed":
                    finish = (event.get("response") or {}).get("status") or finish
    except urllib.error.HTTPError as exc:
        status = exc.code
        reason = exc.headers.get("X-CPA-Admission-Reason") or "-"
        detail = exc.read(400).decode("utf-8", "replace")
        print(
            json.dumps(
                {
                    "status": status,
                    "admission_reason": reason,
                    "body_head": detail[:200],
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "model": MODEL,
                "status": status,
                "admission_reason": reason,
                "sse_events": events,
                "terminal": terminal,
                "finish": finish,
                "text_chars": text_len,
                "elapsed_ms": int((time.time() - started) * 1000),
            },
            ensure_ascii=False,
        )
    )
    return 0 if (status == 200 and terminal == "completed" and text_len > 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())

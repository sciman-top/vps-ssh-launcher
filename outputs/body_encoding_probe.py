#!/usr/bin/env python3
"""Probe whether admission can name the model when the body is encoded.

If a body the client compresses (or otherwise shapes) is not parseable, the lane
gate silently degrades to pass-through and the journal loses the model. This
checks the parse, not the upstream, so it uses an unserved model and consumes no
OAuth turn.
"""

import gzip
import http.client
import json
import time

KEY = None
import yaml

KEY = yaml.safe_load(open("/opt/cliproxyapi/config.yaml", encoding="utf-8"))[
    "api-keys"
][0]
PATH = "/v1/responses"
MODEL = "gpt-6-sol-91"  # deliberately unserved: no upstream turn is consumed


def post(label, body, extra_headers, path=PATH):
    conn = http.client.HTTPConnection("127.0.0.1", 8318, timeout=30)
    headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    headers.update(extra_headers)
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    payload = resp.read()
    print(
        json.dumps(
            {
                "label": label,
                "status": resp.status,
                "bytes": len(payload),
                "body": payload[:120].decode("utf-8", "replace"),
            }
        )
    )
    conn.close()


raw = json.dumps({"model": MODEL, "input": "hi", "stream": True}).encode()
post("plain-json", raw, {"Content-Length": str(len(raw))})
time.sleep(0.4)
packed = gzip.compress(raw)
post("gzip-content-encoding", packed, {"Content-Encoding": "gzip"})
time.sleep(0.4)
# Same body declared as gzip but sent verbatim: isolates "declared" from "actually packed".
post("declared-gzip-plain-body", raw, {"Content-Encoding": "gzip"})

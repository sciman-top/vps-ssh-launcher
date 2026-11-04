"""One-shot loopback acceptance for the bwg CPA admission chain.

Single attempt, no retry. Reads the gateway key from the deployed config so the
secret never appears in the repository, the command line, or this script.
"""

import json
import re
import sys
import urllib.error
import urllib.request

CONFIG = "/opt/cliproxyapi/config.yaml"
ENDPOINT = "http://127.0.0.1:8318/v1/responses"
MODEL = next((a for a in sys.argv[1:] if not a.startswith("--")), "gpt-5.6-luna")
EXPECT_REJECT = "--expect-reject" in sys.argv


def gateway_key() -> str:
    text = open(CONFIG, encoding="utf-8").read()
    match = re.search(r"api-keys:\s*\n\s*-\s*(\S+)", text)
    if match is None:
        raise SystemExit("NO_API_KEY_IN_CONFIG")
    return match.group(1)


def main() -> None:
    body = json.dumps(
        {
            "model": MODEL,
            "input": "ping",
            "stream": True,
            "store": False,
            "max_output_tokens": 32,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": "Bearer " + gateway_key(),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    terminal = None
    model_seen = None
    finish = None
    text_parts = []
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            status = response.status
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    text_parts.append(event.get("delta") or "")
                elif kind in ("response.completed", "response.failed"):
                    terminal = kind.rsplit(".", 1)[-1]
                    inner = event.get("response") or {}
                    model_seen = inner.get("model")
                    # Responses-API items carry `status`, not the
                    # chat/completions `finish_reason` field.
                    finish = inner.get("status")
                    for item in inner.get("output") or []:
                        if item.get("finish_reason"):
                            finish = item["finish_reason"]
        print(f"HTTP={status}")
        print(f"terminal={terminal}")
        print(f"model={model_seen}")
        print(f"finish={finish}")
        print(f"text_len={len(''.join(text_parts))}")
        accepted = (
            status == 200
            and terminal == "completed"
            and model_seen == MODEL
            and finish in ("completed", "stop")
        )
        print("ACCEPTANCE_RESULT=" + ("PASS" if accepted else "FAIL"))
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        print(f"HTTP={exc.code}")
        print(f"body={detail}")
        # A retired name must be refused before any upstream call.
        rejected = EXPECT_REJECT and 400 <= exc.code < 500
        print("ACCEPTANCE_RESULT=" + ("PASS" if rejected else "FAIL"))


main()

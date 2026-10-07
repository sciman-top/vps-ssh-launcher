#!/usr/bin/env python3
# Read-only probe for CPA slot-1 (ai.input.im) upstream recovery.
# Prints only model -> status code. Never prints keys or full URLs.
import json
import ssl
import sys
import urllib.request
import urllib.error

CONFIG = "/opt/cliproxyapi/config.yaml"
MODELS = ["deepseek-v4.1-flash", "gpt-6-astra", "gpt-6.1-sol"]
TARGET_HOST = "ai.input.im"
TIMEOUT = 25


def find_slot1(path):
    """Line-based scan: find '- name: ai.input.im', then within that block
    take base-url and the first 'api-key:' value."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    start = None
    for idx, line in enumerate(lines):
        if line.strip() == "- name: " + TARGET_HOST:
            start = idx
            break
    if start is None:
        return None, None

    base_url = None
    api_key = None
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("- name: "):
            break  # next provider
        # api-key entries are YAML list items: "- api-key: <value>"
        entry = stripped[2:].strip() if stripped.startswith("- ") else stripped
        if base_url is None and entry.startswith("base-url:"):
            base_url = entry.split(":", 1)[1].strip().strip("'\"")
        if api_key is None and entry.startswith("api-key:"):
            api_key = entry.split(":", 1)[1].strip().strip("'\"")
        if base_url and api_key:
            break

    if base_url and api_key:
        return base_url.rstrip("/"), api_key
    return None, None


def probe(base_url, api_key, model):
    url = base_url + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply OK"}],
        "max_tokens": 16,
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + api_key)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        return "ERR:" + type(exc).__name__


def main():
    base_url, api_key = find_slot1(CONFIG)
    if not base_url:
        print("PROBE_ERROR=slot1_provider_not_found")
        return 2
    print("SLOT1_HOST_MATCH=%s" % (TARGET_HOST in base_url))
    for model in MODELS:
        print("%s=%s" % (model, probe(base_url, api_key, model)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

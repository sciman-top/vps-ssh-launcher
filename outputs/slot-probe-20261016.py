"""Read-only slot credential extraction for providers.env rebuild (2026-10-16).

Runs on the VPS. Prints per-slot key fingerprints to stderr and a single
base64 payload line to stdout; no secret is printed in the clear.
"""

import base64
import hashlib
import json
import sys

import yaml

ROUTES = {
    1: "https://ai.input.im/v1",
    2: "https://opencode.ai/zen/go/v1",
    3: "http://35.213.82.91:8003/v1",
    4: "https://open.bigmodel.cn/api/coding/paas/v4",
}

config = yaml.safe_load(open("/opt/cliproxyapi/config.yaml", encoding="utf-8"))
entries = config["api-keys"]["openai-compatibility"]
found = {}
for entry in entries:
    if not isinstance(entry, dict):
        continue
    base_url = str(entry.get("base-url", "")).rstrip("/")
    key = None
    keys = entry.get("keys")
    if isinstance(keys, list) and keys and isinstance(keys[0], dict):
        key = keys[0].get("api-key")
    if key is None:
        key = entry.get("api-key")
    if not base_url or not key:
        continue
    for slot, route in ROUTES.items():
        if base_url == route.rstrip("/") and slot not in found:
            found[slot] = {"base_url": route, "api_key": key}

missing = sorted(set(ROUTES) - set(found))
if missing:
    print("MISSING_SLOTS=" + ",".join(map(str, missing)), file=sys.stderr)
    raise SystemExit(1)

for slot in sorted(found):
    key = found[slot]["api_key"]
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    print(
        "FP slot=%d sha256=%s tail=%s len=%d" % (slot, digest, key[-4:], len(key)),
        file=sys.stderr,
    )

payload = base64.b64encode(json.dumps(found).encode("utf-8")).decode("ascii")
print("PAYLOAD " + payload)

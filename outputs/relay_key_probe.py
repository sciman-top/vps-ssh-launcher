#!/usr/bin/env python3
"""Check whether the ai.input.im relay rejects every configured key.

Reads the keys from the local Cockpit provider config, probes each one against
the relay's model list (cheap, no completion), and prints only a key fingerprint
so no credential is echoed. Read-only: nothing is written anywhere.
"""

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

PROVIDERS = Path(
    r"C:\Users\sciman\.antigravity_cockpit\codex_model_providers.json"
)


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:10]


def probe(base: str, key: str) -> dict:
    url = base.rstrip("/") + "/v1/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read()
            return {"status": resp.status, "bytes": len(body)}
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "body": exc.read()[:140].decode("utf-8", "replace"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"exception": type(exc).__name__, "message": str(exc)[:120]}


def main() -> None:
    entries = json.loads(PROVIDERS.read_text(encoding="utf-8"))
    for entry in entries:
        name = entry.get("name", "")
        if "input.im" not in name:
            continue
        base = entry.get("baseUrl", "")
        print(json.dumps({"provider": name, "base_url": base}))
        for item in entry.get("apiKeys") or []:
            key = item.get("apiKey") if isinstance(item, dict) else item
            if not key:
                continue
            result = {"key": fingerprint(key), "len": len(key)}
            result.update(probe(base, key))
            print(json.dumps(result))


if __name__ == "__main__":
    main()

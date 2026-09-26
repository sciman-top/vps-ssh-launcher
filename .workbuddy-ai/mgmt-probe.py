import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

key = Path("/opt/cliproxyapi/management-key.txt").read_text(encoding="utf-8").strip()
opener = build_opener(ProxyHandler({}))


def get(path):
    try:
        req = Request("http://127.0.0.1:8317/v0/management" + path, headers={"X-Management-Key": key})
        with opener.open(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read(2_000_001))
    except HTTPError as exc:
        return exc.code, None
    except (URLError, OSError, ValueError) as exc:
        return "error:" + type(exc).__name__, None


def fingerprint(value):
    if not isinstance(value, str) or not value:
        return "empty"
    return "len%d:" % len(value) + hashlib.sha256(value.encode()).hexdigest()[:8]


for path in ("/request-retry", "/max-retry-interval", "/quota-exceeded/switch-project"):
    status, body = get(path)
    print("ENDPOINT%s status=%s body=%s" % (path, status, json.dumps(body) if body is not None else "-"))

status, body = get("/api-key-usage")
print("ENDPOINT/api-key-usage status=%s" % status)
if isinstance(body, dict):
    shape = {}
    for provider, buckets in body.items():
        if isinstance(buckets, dict):
            shape[provider] = {
                "buckets": len(buckets),
                "keys": [
                    {
                        "base_url": (str(k).split("|")[0] or "-"),
                        "key_fingerprint": fingerprint(str(k).split("|")[-1]),
                    }
                    for k in list(buckets)[:5]
                ],
            }
        else:
            shape[provider] = type(buckets).__name__
    print("API_KEY_USAGE_SHAPE=" + json.dumps(shape))

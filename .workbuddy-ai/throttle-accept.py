import json
import re
import ssl
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

conf = Path("/etc/nginx/conf.d/cpa-gateway.conf").read_text(encoding="utf-8")
prefix = re.search(r"/([0-9a-f]{16})/v1/", conf).group(1)
key = yaml.safe_load(
    Path("/opt/cliproxyapi/config.yaml").read_text(encoding="utf-8")
)["api-keys"][0]
url = "https://127.0.0.1:8443/%s/v1/models" % prefix
ctx = ssl._create_unverified_context()


def one(_):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return resp.status, resp.headers.get("Retry-After")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Retry-After")
    except Exception as exc:
        return "error:" + type(exc).__name__, None


with ThreadPoolExecutor(max_workers=24) as pool:
    results = list(pool.map(one, range(60)))

print("BURST_CODES=" + json.dumps(dict(Counter(str(c) for c, _ in results))))
ra = {}
for code, value in results:
    ra.setdefault(str(code), set()).add(value)
print(
    "BURST_RETRY_AFTER="
    + json.dumps({k: sorted("none" if v is None else v for v in vs) for k, vs in ra.items()})
)

time.sleep(3)
code, value = one(0)
print("CONTROL_STATUS=%s" % code)
print("CONTROL_RETRY_AFTER=%s" % ("none" if value is None else value))

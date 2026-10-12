"""Fixture-only v8.0.24 catalog/smoke diagnostic.

Runs INSIDE the disposable mount+net namespace with /opt/cliproxyapi bound to
an empty fixture dir, mirroring cpa-acceptance.py's config verbatim. Starts the
deployed binary once, prints the bare /v1/models IDs vs the cpa-health allowed
set, the required-set delta, one glm-5.3-flash smoke shape, and the binary log
tail. No production credential is read and no real provider is contacted.
"""

import http.server
import json
import runpy
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path("/opt/cliproxyapi")


class Upstream(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.rstrip("/").endswith("/chat/completions"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "id": "chatcmpl-diag",
                        "object": "chat.completion",
                        "created": 0,
                        "model": "glm-5.3-flash",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "OK"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 1,
                            "total_tokens": 11,
                        },
                    }
                ).encode()
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def api(path, body=None):
    req = urllib.request.Request(
        "http://127.0.0.1:8317/v1/" + path,
        data=json.dumps(body).encode() if body else None,
        headers={
            "Authorization": "Bearer fixture-client",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.status, json.loads(response.read().decode())


config = {
    "host": "127.0.0.1",
    "port": 8317,
    "auth-dir": str(ROOT / "auth"),
    "api-keys": ["fixture-client"],
    "request-retry": 0,
    "max-retry-credentials": 1,
    "disable-cooling": False,
    "save-cooldown-status": True,
    "transient-error-cooldown-seconds": 60,
    "upstream": {"codex": {"stream-bootstrap-buffering": False}},
    "routing": {"strategy": "fill-first", "session-affinity": True},
    "codex-api-key": [
        {
            "api-key": "fixture-upstream",
            "base-url": "http://127.0.0.1:18318/v1",
            "models": [
                {"name": "gpt-6-luna", "alias": "gpt-6-luna"},
                {"name": "gpt-6.1-sol", "alias": "gpt-6.1-sol"},
            ],
        },
        {
            "api-key": "fixture-opencode",
            "base-url": "http://127.0.0.1:18318/v1",
            "headers": {"x-opencode-session": "cpa-bwg-gateway-01"},
            "models": [
                {
                    "name": "muse-spark-1.3-contributor",
                    "alias": "muse-spark-1.3-contributor",
                },
            ],
        },
    ],
    "openai-compatibility": [
        {
            "name": "ai.input.im",
            "base-url": "http://127.0.0.1:18318/v1",
            "api-key-entries": [{"api-key": "fixture-ai-input-im"}],
            "models": [
                {"name": "gpt-6-astra", "alias": "gpt-6-astra"},
                {"name": "gpt-6.1-sol", "alias": "gpt-6.1-sol-input"},
            ],
        },
        {
            "name": "opencode-zen",
            "base-url": "http://127.0.0.1:18318/v1",
            "api-key-entries": [{"api-key": "fixture-opencode"}],
            "headers": {"x-opencode-session": "cpa-bwg-gateway-01"},
            "models": [
                {"name": "deepseek-v4.1-flash", "alias": "deepseek-v4.1-flash"},
            ],
        },
        {
            "name": "http-bridge-8003",
            "base-url": "http://127.0.0.1:18318/v1",
            "api-key-entries": [{"api-key": "fixture-http-bridge"}],
            "models": [
                {"name": "gpt-5.6-terra", "alias": "gpt-5.6-terra"},
            ],
        },
        {
            "name": "zhipu-plan",
            "base-url": "http://127.0.0.1:18318/v1",
            "api-key-entries": [{"api-key": "fixture-glm"}],
            "models": [
                {"name": "glm-5.3", "alias": "glm-5.3"},
                {"name": "glm-5.3-flash", "alias": "glm-5.3-flash"},
            ],
        },
    ],
}
(ROOT / "auth").mkdir(exist_ok=True)
server = http.server.ThreadingHTTPServer(("127.0.0.1", 18318), Upstream)
threading.Thread(target=server.serve_forever, daemon=True).start()
(ROOT / "config.yaml").write_text(yaml.safe_dump(config))
log = (ROOT / "binary.log").open("ab")
proc = subprocess.Popen(
    [str(ROOT / "CLIProxyAPI"), "-config", str(ROOT / "config.yaml")],
    cwd=ROOT,
    stdout=log,
    stderr=log,
)
log.close()
try:
    catalog = None
    for _ in range(60):
        try:
            status, catalog = api("models")
            if status == 200:
                break
        except OSError:
            pass
        time.sleep(0.5)
    assert catalog is not None, "fixture CPA never answered /v1/models"
    ids = sorted(m["id"] for m in catalog["data"])
    health = runpy.run_path(str(ROOT / "cpa-health.py"))
    allowed = set(health["_BASE_ALLOWED_MODELS"])
    channel_enabled = health["_channel_enabled"](
        yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    )
    if channel_enabled:
        allowed |= set(health["_CHANNEL_MODELS"])
    optional = set(health["_OPTIONAL_OAUTH_MODELS"])
    optional |= set(health["_OPTIONAL_CHANNEL_MODELS"])
    optional |= set(health["_OPTIONAL_PROVIDER_MODELS"])
    required = allowed - optional
    id_set = set(ids)
    print("DIAG_BARE_IDS=" + json.dumps(ids))
    print("DIAG_UNKNOWN=" + json.dumps(sorted(id_set - allowed)))
    print("DIAG_MISSING_REQUIRED=" + json.dumps(sorted(required - id_set)))
    try:
        status, smoke = api(
            "chat/completions",
            {
                "model": "glm-5.3-flash",
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 1024,
            },
        )
        choice = (smoke.get("choices") or [{}])[0]
        print(
            "DIAG_GLM_SMOKE="
            + json.dumps(
                {
                    "status": status,
                    "responded_model": smoke.get("model"),
                    "finish": choice.get("finish_reason"),
                    "content": str(choice.get("message", {}).get("content"))[:40],
                    "usage": smoke.get("usage"),
                    "error": smoke.get("error"),
                }
            )
        )
    except Exception as exc:  # noqa: BLE001 - fixture diagnostic
        print("DIAG_GLM_SMOKE_ERROR=" + repr(exc)[:200])
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
server.shutdown()
print("DIAG_BINARY_LOG_TAIL_BEGIN")
print((ROOT / "binary.log").read_text(errors="replace")[-3000:])
print("DIAG_BINARY_LOG_TAIL_END")

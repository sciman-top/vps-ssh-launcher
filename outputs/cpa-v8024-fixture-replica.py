"""Fixture-only v8.0.24 acceptance-prelude replica.

Runs INSIDE the disposable mount+net namespace. Reuses the audited
cpa-acceptance.py module objects (fake upstream, api, ready) verbatim, replays
the exact overload -> cooldown -> 62s -> recovered prelude, then runs the same
`cpa-health.py generation` subprocess while capturing stderr, plus a catalog
diff and a direct glm smoke shape, so the silent exit-20 path becomes visible.
"""

import http.server
import json
import runpy
import subprocess
import threading
import time
from pathlib import Path

import yaml

ROOT = Path("/opt/cliproxyapi")

acc = runpy.run_path(str(ROOT / "cpa-acceptance.py"))
Upstream = acc["Upstream"]
api = acc["api"]
ready = acc["ready"]
STATE = acc["STATE"]

(ROOT / "auth").mkdir(exist_ok=True)
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
(ROOT / "config.yaml").write_text(yaml.safe_dump(config))
server = http.server.ThreadingHTTPServer(("127.0.0.1", 18318), Upstream)
threading.Thread(target=server.serve_forever, daemon=True).start()
log = (ROOT / "binary.log").open("ab")
proc = subprocess.Popen(
    [str(ROOT / "CLIProxyAPI"), "-config", str(ROOT / "config.yaml")],
    cwd=ROOT,
    stdout=log,
    stderr=log,
)
log.close()
(ROOT / "pid").write_text(str(proc.pid))


def report(label, data):
    print(label + "=" + json.dumps(data), flush=True)


health = runpy.run_path(str(ROOT / "cpa-health.py"))
allowed = set(health["_BASE_ALLOWED_MODELS"])
cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
if health["_channel_enabled"](cfg):
    allowed |= set(health["_CHANNEL_MODELS"])
optional = (
    set(health["_OPTIONAL_OAUTH_MODELS"])
    | set(health["_OPTIONAL_CHANNEL_MODELS"])
    | set(health["_OPTIONAL_PROVIDER_MODELS"])
)
required = allowed - optional

try:
    ready()
    body = {"model": "gpt-6-luna", "input": "Reply OK", "stream": True}
    STATE["mode"] = "overload"
    before = STATE["calls"]
    status, text = api("responses", body)
    report(
        "REPLICA_OVERLOAD",
        {
            "status": status,
            "overload": "server_is_overloaded" in text,
            "calls": STATE["calls"] - before,
        },
    )
    STATE["mode"] = "ok"
    at = STATE["calls"]
    status, _ = api("responses", body)
    report(
        "REPLICA_COOLDOWN",
        {"status": status, "calls": STATE["calls"] - at},
    )
    time.sleep(62)
    status, text = api("responses", body)
    report(
        "REPLICA_RECOVERED",
        {
            "status": status,
            "completed": "response.completed" in text,
            "calls": STATE["calls"] - at,
            "alive": proc.poll() is None,
        },
    )

    for attempt in range(3):
        ids = set()
        try:
            _, cat_raw = api()
            try:
                catalog = json.loads(cat_raw)
                ids = {m["id"] for m in catalog["data"]}
            except (ValueError, TypeError, KeyError) as exc:
                report(
                    "REPLICA_CATALOG_RAW",
                    {
                        "attempt": attempt,
                        "parse_error": repr(exc)[:120],
                        "body_head": cat_raw[:600],
                    },
                )
            report(
                "REPLICA_CATALOG",
                {
                    "attempt": attempt,
                    "unknown": sorted(ids - allowed),
                    "missing_required": sorted(required - ids),
                },
            )
        except Exception as exc:  # noqa: BLE001 - fixture diagnostic
            report(
                "REPLICA_CATALOG_ERROR",
                {"attempt": attempt, "error": repr(exc)[:160]},
            )
        if attempt < 2:
            time.sleep(2)

    try:
        status, smoke_raw = api(
            "chat/completions",
            {
                "model": "glm-5.3-flash",
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 1024,
            },
        )
        smoke = json.loads(smoke_raw)
        choice = (smoke.get("choices") or [{}])[0]
        report(
            "REPLICA_GLM_SMOKE",
            {
                "status": status,
                "responded_model": smoke.get("model"),
                "finish": choice.get("finish_reason"),
                "content": str(choice.get("message", {}).get("content"))[:40],
                "error": smoke.get("error"),
            },
        )
    except Exception as exc:  # noqa: BLE001 - fixture diagnostic
        report("REPLICA_GLM_SMOKE_ERROR", {"error": repr(exc)[:160]})

    result = subprocess.run(
        ["python3", str(ROOT / "cpa-health.py"), "generation"],
        capture_output=True,
        text=True,
    )
    report(
        "REPLICA_HEALTH",
        {
            "exit": result.returncode,
            "stdout_tail": result.stdout.strip()[-400:],
            "stderr_tail": result.stderr.strip()[-1200:],
        },
    )
    report(
        "REPLICA_AUTH_DIR",
        sorted(p.name for p in (ROOT / "auth").glob("*")),
    )

    try:
        inproc = health["check"](cfg, "generation")
        report("REPLICA_INPROC_CHECK", {"result": inproc})
        request = health["_loopback_request"](cfg)
        catalog_view = request("models")
        report(
            "REPLICA_HEALTH_CATALOG",
            {
                "ids": sorted(
                    m.get("id") if isinstance(m, dict) else repr(m)[:60]
                    for m in catalog_view.get("data", [])
                ),
                "keys": sorted(catalog_view.keys()),
            },
        )
        smoke_view = request(
            "chat/completions",
            {
                "model": "glm-5.3-flash",
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 1024,
            },
        )
        report(
            "REPLICA_HEALTH_SMOKE_VIEW",
            {
                "model": smoke_view.get("model"),
                "error": smoke_view.get("error"),
                "choice_head": repr(smoke_view.get("choices", [])[:1])[:300]
                if isinstance(smoke_view.get("choices"), list)
                else repr(smoke_view.get("choices"))[:300],
            },
        )
    except Exception as exc:  # noqa: BLE001 - fixture diagnostic
        report("REPLICA_INPROC_ERROR", {"error": repr(exc)[:300]})
    for cds in sorted((ROOT / "auth").glob("*.cds")):
        report(
            "REPLICA_CDS",
            {
                "name": cds.name,
                "body_head": cds.read_text(errors="replace")[:400],
            },
        )
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    server.shutdown()
print("DIAG_BINARY_LOG_TAIL_BEGIN")
print((ROOT / "binary.log").read_text(errors="replace")[-4000:])
print("DIAG_BINARY_LOG_TAIL_END")

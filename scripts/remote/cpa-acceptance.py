"""Disposable, network-isolated acceptance using the deployed CPA binary.

Run only inside an isolated mount + network namespace with /opt/cliproxyapi
bound to an empty fixture directory. Never reads production credentials.

Success criterion: the literal line ``ACCEPTANCE_RESULT=PASS`` printed by this
process as its final step. Wrappers must NOT rely on ``$?`` — a
``python3 ... | tee log; echo $?`` pipeline reports tee's exit code and prints
0 even after a traceback (observed 2026-09-17). Absence of the PASS line means
failure, regardless of the wrapper's exit code.
"""

import http.server
import json
import os
import runpy
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path("/opt/cliproxyapi")
STATE = {"mode": "ok", "calls": 0}


def expected_overload_status(root: Path | None = None) -> int:
    """HTTP status the fixture CPA must return for a capacity failure.

    The observable contract depends on ``upstream.codex.stream-bootstrap-buffering``.
    While CPA buffers the bootstrap it still owns the response headers and can
    rewrite the status to 503. With buffering off (production since 2026-09-28,
    which this fixture mirrors) the headers are committed before the upstream
    failure arrives, so CPA relays the upstream's 200 with the capacity marker
    inside the body and ``cpa-admission`` performs the classification --
    observed in production as ``status=200 capacity=true``. Deriving the
    expectation from the fixture config keeps the assertion exact for either
    shape instead of pinning a status that only held under the buffered one;
    the invariants that matter are the single upstream call and the marker.

    ``root`` defaults to the fixture root; it is an explicit parameter rather
    than a module-global lookup so a test can point at an isolated config.
    """
    base = ROOT if root is None else Path(root)
    try:
        config = yaml.safe_load((base / "config.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        config = {}
    upstream = config.get("upstream") if isinstance(config, dict) else None
    codex = upstream.get("codex") if isinstance(upstream, dict) else None
    if not isinstance(codex, dict):
        codex = {}
    return 503 if codex.get("stream-bootstrap-buffering") else 200


class Upstream(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
            )
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        # Echo the requested model: the real cpa-health generation smoke does an
        # exact responded-model check, and the smoke target may move.
        requested_model = str(body.get("model") or "gpt-6-luna")
        STATE["calls"] += 1
        mode = (
            (ROOT / "upstream-mode").read_text().strip()
            if (ROOT / "upstream-mode").exists()
            else STATE["mode"]
        )
        if mode == "http503":
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"error":{"code":"server_is_overloaded","message":"fixture"}}'
            )
            return
        # Openai-compatibility lanes speak chat completions. Current CPA
        # binaries relay the upstream body verbatim for this endpoint, so the
        # synthetic upstream must answer with a real chat payload (JSON, or
        # chat chunks when the executor streams); a responses-format body here
        # would reach cpa-health without "choices" and read as upstream
        # unavailability (v7.3.16 fixture finding, 2026-09-25).
        if self.path.rstrip("/").endswith("/chat/completions"):
            self._send_chat_completion(requested_model, bool(body.get("stream")))
            return
        # Claude-family lanes speak native Anthropic Messages; the executor
        # appends /v1/messages?beta=true to the configured base-url.
        if self.path.split("?")[0].rstrip("/").endswith("/messages"):
            self._send_messages(requested_model, bool(body.get("stream")))
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        response = {
            "id": "resp_fixture",
            "object": "response",
            "model": requested_model,
            "status": "in_progress",
            "output": [],
        }
        events = [{"type": "response.created", "response": response}]
        if mode == "overload":
            events.append(
                {
                    "type": "response.failed",
                    "response": {
                        **response,
                        "status": "failed",
                        "error": {
                            "type": "server_error",
                            "code": "server_is_overloaded",
                            "message": "fixture capacity rejection",
                        },
                    },
                }
            )
        else:
            item = {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "OK", "annotations": []}],
            }
            events += [
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "status": "in_progress", "content": []},
                },
                {
                    "type": "response.content_part.added",
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": "msg_fixture",
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": "msg_fixture",
                    "delta": "OK",
                },
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        **response,
                        "status": "completed",
                        "output": [item],
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 1,
                            "total_tokens": 11,
                        },
                    },
                },
            ]
        for event in events:
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
        self.wfile.flush()

    def _send_chat_completion(self, model, stream):
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            base = {
                "id": "chatcmpl-fixture",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
            }
            chunks = [
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant"},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    **base,
                    "choices": [
                        {"index": 0, "delta": {"content": "OK"}, "finish_reason": None}
                    ],
                },
                {
                    **base,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            for chunk in chunks:
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "id": "chatcmpl-fixture",
                    "object": "chat.completion",
                    "created": 0,
                    "model": model,
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
        self.wfile.flush()

    def _send_messages(self, model, stream):
        # Mirror the measured zen contract: the Anthropic face reads the
        # credential from x-api-key only, so a claude-family group that loses
        # its pinned header fails here instead of only on the live gateway.
        # CPA still sends its own Authorization: Bearer for custom base URLs
        # (only api.anthropic.com gets the x-api-key shape), which the real
        # upstream tolerates alongside the injected header.
        if self.headers.get("x-api-key") != "fixture-opencode":
            body = json.dumps(
                {
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": "Go requires a credential",
                    },
                }
            ).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return
        usage = {"input_tokens": 10, "output_tokens": 1}
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_fixture",
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": usage,
                    },
                },
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "OK"},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
                {"type": "message_stop"},
            ]
            for event in events:
                payload = "event: %s\ndata: %s\n\n" % (
                    event["type"],
                    json.dumps(event),
                )
                self.wfile.write(payload.encode())
            self.wfile.flush()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": "OK"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": usage,
                }
            ).encode()
        )
        self.wfile.flush()


class AdmissionStub(http.server.BaseHTTPRequestHandler):
    """Fixture stand-in for the production admission front door.

    The updater's traffic-guard preflight requires a /healthz that names its
    lock path. Inside this network namespace nothing else answers on 8318, and
    the reported path stays inside the bind-mounted fixture root so the
    updater's exclusive-lock phase never touches the host's /run.
    """

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.split("?")[0].rstrip("/") != "/healthz":
            self.send_error(404)
            return
        body = json.dumps(
            {"status": "ok", "traffic_guard": str(ROOT / "requests.lock")}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def emit(**data):
    print(json.dumps(data), flush=True)


def api(path="models", body=None, headers=None):
    request_headers = {
        "Authorization": "Bearer fixture-client",
        "Content-Type": "application/json",
    }
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(
        "http://127.0.0.1:8317/v1/" + path,
        data=json.dumps(body).encode() if body else None,
        headers=request_headers,
    )
    try:
        # Fixture-only request to the loopback acceptance port.
        with urllib.request.urlopen(req, timeout=10) as response:  # nosec B310
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def ready():
    for _ in range(40):
        try:
            status, body = api()
            if status == 200 and "gpt-6-luna" in body:
                return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError("fixture CPA not ready")


def assert_catalog_contract():
    health = runpy.run_path(str(ROOT / "cpa-health.py"))
    required = (
        set(health["_BASE_ALLOWED_MODELS"]) - set(health["_OPTIONAL_OAUTH_MODELS"])
    ) | (set(health["_CHANNEL_MODELS"]) - set(health["_OPTIONAL_CHANNEL_MODELS"]))
    config = {
        "api-keys": {
            "openai-compatibility": [
                {"name": "ai.input.im", "base-url": "https://ai.input.im/v1"}
            ]
        }
    }

    def readiness_for(ids):
        def request(_path, *_args):
            return {"data": [{"id": model} for model in ids]}

        return health["check"](config, "readiness", request=request)

    assert readiness_for(required) == 0
    # gpt-6-sol retired 2026-09-30: a tombstoned name must fail closed like
    # any other unknown ID instead of silently rejoining the catalog.
    assert readiness_for(required | {"gpt-6-sol"}) == 20
    assert readiness_for(required | {"gpt-6-luna"}) == 0
    assert readiness_for(required | {"codex/gpt-6-sol"}) == 20


def assert_fixture_model_coverage(config):
    """Fail locally when the synthetic config drifts from required catalog IDs."""
    health = runpy.run_path(str(ROOT / "cpa-health.py"))
    allowed = set(health["_BASE_ALLOWED_MODELS"])
    optional = set(health["_OPTIONAL_OAUTH_MODELS"])
    if health["_channel_enabled"](config):
        allowed.update(health["_CHANNEL_MODELS"])
        optional.update(health["_OPTIONAL_CHANNEL_MODELS"])
    optional.update(health["_OPTIONAL_PROVIDER_MODELS"])
    optional.update(health["_PLUGIN_MODEL_NAMES"])
    required = allowed - optional

    fixture_models = set()
    for group in ("codex-api-key", "openai-compatibility"):
        routes = config.get(group, [])
        if not isinstance(routes, list):
            continue
        fixture_models.update(
            model["alias"]
            for route in routes
            if isinstance(route, dict)
            for model in route.get("models", [])
            if isinstance(model, dict) and isinstance(model.get("alias"), str)
        )
    missing = required - fixture_models
    unexpected = fixture_models - allowed
    assert not missing, f"fixture omits required catalog models: {sorted(missing)}"
    assert not unexpected, f"fixture declares unapproved catalog models: {sorted(unexpected)}"


def start():
    log = (ROOT / "binary.log").open("ab")
    p = subprocess.Popen(
        [str(ROOT / "CLIProxyAPI"), "-config", str(ROOT / "config.yaml")],
        cwd=ROOT,
        stdout=log,
        stderr=log,
    )
    log.close()
    (ROOT / "pid").write_text(str(p.pid))
    return p


def main():
    assert Path("/proc/self/ns/net").readlink() != Path("/proc/1/ns/net").readlink()
    assert (ROOT / "FIXTURE_ONLY").exists()
    assert_catalog_contract()
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    (ROOT / "auth").mkdir(exist_ok=True)
    config = {
        "host": "127.0.0.1",
        "port": 8317,
        "auth-dir": str(ROOT / "auth"),
        # v8 file layout: cpa-health and the scenario gates read the client
        # key from access.api-keys; the legacy top-level list is not
        # consulted there (6d88f5d migration left this fixture behind).
        "access": {"api-keys": ["fixture-client"]},
        # host/port/auth-dir above and the routing keys below are historical
        # YAML-boundary spellings: the binary migrates them on load
        # (buildV8Paths -> server./oauth./routing.retry/routing.cooldown), so
        # they load fine here, but this fixture is not the schema authority —
        # repo readers use the v8 paths (6d88f5d anchor table).
        "request-retry": 0,
        "max-retry-credentials": 1,
        "disable-cooling": False,
        "save-cooldown-status": True,
        "transient-error-cooldown-seconds": 60,
        # v8 canonical spelling; expected_overload_status() reads this path.
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
            # The muse codex lane retired with the opencode.ai channel; a
            # second entry here would read as an unknown catalog ID.
        ],
        # The claude-oauth lane retired 2026-10-20 (account suspended); a
        # fixture lane declaring claude models would read as an unknown
        # catalog ID now that the manifest no longer carries the route.
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
                # muse-spark left the catalog contract with the opencode.ai
                # retirement (2026-10-18, c894a02): declaring it here reads as
                # an unknown catalog ID and fails readiness. deepseek stays
                # because production gets it from the workbuddy plugin, which
                # the fixture does not load; without a lane declaring it the
                # required ID goes missing instead.
                "name": "workbuddy-fixture",
                "base-url": "http://127.0.0.1:18318/v1",
                "api-key-entries": [{"api-key": "fixture-workbuddy"}],
                "models": [
                    {
                        "name": "deepseek-v4.1-flash",
                        "alias": "deepseek-v4.1-flash",
                    },
                ],
            },
            {
                # OpenCode Go is API-key backed in production. Keep its required
                # catalog model on the loopback fixture so generation readiness
                # sees the same approved model set without reaching the provider.
                "name": "opencode",
                "base-url": "http://127.0.0.1:18318/zen/go/v1",
                "api-key-entries": [{"api-key": "fixture-opencode"}],
                "headers": {"x-opencode-session": "cpa-acceptance-fixture"},
                "models": [{"name": "beam-free", "alias": "beam-free"}],
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
    assert_fixture_model_coverage(config)
    (ROOT / "config.yaml").write_text(yaml.safe_dump(config))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 18318), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # cpa-update-acceptance.py points TRAFFIC_GUARD_LOCK at this same path;
    # keep the two in sync when moving the fixture root.
    (ROOT / "requests.lock").touch()
    admission = http.server.ThreadingHTTPServer(("127.0.0.1", 8318), AdmissionStub)
    threading.Thread(target=admission.serve_forever, daemon=True).start()
    proc = start()
    try:
        ready()
        if os.environ.get("CPA_ACCEPTANCE_UPDATE_ONLY") == "1":
            import runpy

            runpy.run_path(str(ROOT / "cpa-update-acceptance.py"))["run_cases"]()
        else:
            body = {"model": "gpt-6-luna", "input": "Reply OK", "stream": True}
            STATE["mode"] = "overload"
            before = STATE["calls"]
            status, text = api("responses", body)
            emit(
                stage="overload",
                status=status,
                overload="server_is_overloaded" in text,
                upstream_calls=STATE["calls"] - before,
            )
            assert (
                status == expected_overload_status()
                and "server_is_overloaded" in text
                and STATE["calls"] - before == 1
            )
            STATE["mode"] = "ok"
            at = STATE["calls"]
            status, _ = api("responses", body)
            emit(stage="cooldown", status=status, upstream_calls=STATE["calls"] - at)
            assert status != 200 and STATE["calls"] == at
            emit(stage="waiting", seconds=62)
            time.sleep(62)
            status, text = api("responses", body)
            emit(
                stage="recovered",
                status=status,
                completed="response.completed" in text,
                upstream_calls=STATE["calls"] - at,
                same_process=proc.poll() is None,
            )
            assert (
                status == 200
                and "response.completed" in text
                and STATE["calls"] == at + 1
            )
            result = subprocess.run(
                ["python3", str(ROOT / "cpa-health.py"), "generation"],
                capture_output=True,
                text=True,
            )
            emit(
                stage="actual_health",
                exit=result.returncode,
                result=result.stdout.strip(),
            )
            assert result.returncode == 0
            import runpy

            runpy.run_path(str(ROOT / "cpa-update-acceptance.py"))["run_cases"]()
        # Machine-readable completion marker: printed only when every scenario
        # above finished without assertion failure. See the module docstring.
        print("ACCEPTANCE_RESULT=PASS", flush=True)
    finally:
        import signal

        try:
            os.kill(int((ROOT / "pid").read_text()), signal.SIGTERM)
        except ProcessLookupError:
            pass
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=10)
        server.shutdown()
        admission.shutdown()


if __name__ == "__main__":
    main()

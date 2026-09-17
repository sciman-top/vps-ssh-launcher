"""Disposable, network-isolated acceptance using the deployed CPA binary.

Run only inside an isolated mount + network namespace with /opt/cliproxyapi
bound to an empty fixture directory. Never reads production credentials.
"""

import http.server
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.request

import yaml

ROOT = Path("/opt/cliproxyapi")
STATE = {"mode": "ok", "calls": 0}


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
        # Echo the requested model: the real cpa-health generation smoke does an
        # exact responded-model check, and the smoke target may move.
        requested_model = str(body.get("model") or "gpt-5.6-luna")
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


def emit(**data):
    print(json.dumps(data), flush=True)


def api(path="models", body=None):
    req = urllib.request.Request(
        "http://127.0.0.1:8317/v1/" + path,
        data=json.dumps(body).encode() if body else None,
        headers={
            "Authorization": "Bearer fixture-client",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def ready():
    for _ in range(40):
        try:
            status, body = api()
            if status == 200 and "gpt-5.6-luna" in body:
                return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError("fixture CPA not ready")


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
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
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
        "codex": {"stream-bootstrap-buffering": True},
        "routing": {"strategy": "fill-first", "session-affinity": True},
        "codex-api-key": [
            {
                "api-key": "fixture-upstream",
                "base-url": "http://127.0.0.1:18318/v1",
                "models": [
                    {"name": "gpt-5.6-luna", "alias": "gpt-5.6-luna"},
                    {"name": "gpt-5.6-sol", "alias": "gpt-5.6-sol"},
                    {"name": "gpt-5.6-terra", "alias": "gpt-5.6-terra"},
                    {"name": "gpt-6-astra", "alias": "gpt-6-astra"},
                ],
            }
        ],
        "openai-compatibility": [
            {
                "name": "fixture-glm",
                "base-url": "http://127.0.0.1:18318/v1",
                "api-key-entries": [{"api-key": "fixture-glm"}],
                "models": [
                    {"name": "glm-5.3-flash", "alias": "glm-5.3-flash"},
                    {"name": "glm-5.3-flash", "alias": "gpt-5.2"},
                    {"name": "glm-5.3-flash", "alias": "gpt-5.5"},
                ],
            },
            {
                "name": "fixture-deepseek",
                "base-url": "http://127.0.0.1:18318/v1",
                "api-key-entries": [{"api-key": "fixture-deepseek"}],
                "models": [
                    {"name": "deepseek-flash", "alias": "deepseek-flash"},
                    {"name": "deepseek-v4-pro", "alias": "deepseek-v4-pro"},
                    {"name": "deepseek-flash", "alias": "gpt-5.3-codex-spark"},
                ],
            },
        ],
    }
    (ROOT / "config.yaml").write_text(yaml.safe_dump(config))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 18318), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proc = start()
    try:
        ready()
        if os.environ.get("CPA_ACCEPTANCE_UPDATE_ONLY") == "1":
            import runpy

            runpy.run_path(str(ROOT / "cpa-update-acceptance.py"))["run_cases"]()
            return
        body = {"model": "gpt-5.6-luna", "input": "Reply OK", "stream": True}
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
            status == 503
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
            status == 200 and "response.completed" in text and STATE["calls"] == at + 1
        )
        result = subprocess.run(
            ["python3", str(ROOT / "cpa-health.py"), "generation"],
            capture_output=True,
            text=True,
        )
        emit(
            stage="actual_health", exit=result.returncode, result=result.stdout.strip()
        )
        assert result.returncode == 0
        import runpy

        runpy.run_path(str(ROOT / "cpa-update-acceptance.py"))["run_cases"]()
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


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Prove the 2026-11-16 admission branches against the deployed source.

The four changes shipped that day (standing-restriction markers, the Gemini
RESOURCE_EXHAUSTED marker, pooled-alias cooldown scope, and an untyped
Google retryDelay) had unit tests but no request had ever reached them in
production. This runs *the deployed file* as a shadow admission on a
throwaway port with throwaway state files, and drives it with a stub
upstream, so every verdict below is produced by the same code the gateway
runs -- not by a copy of it.

Success criterion: the literal line ``BRANCH_ACCEPTANCE=PASS`` last.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ADMISSION = Path("/opt/cliproxyapi/cpa-admission.py")
CONFIG = Path("/opt/cliproxyapi/cpa-admission.json")
ROUTES = Path("/opt/cliproxyapi/cpa_provider_routes.json")
LANES = ("antigravity", "chatgpt", "claude", "workbuddy", "workbuddy-intl")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Upstream(BaseHTTPRequestHandler):
    """Answers every request with whatever the current scenario queued."""

    body = b"{}"
    status = 200

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


def write_config(workdir, upstream_port):
    # The production file pins listen_port to 8318 in code, so the shadow runs
    # on that port inside its own network namespace instead of on another port.
    # upstream_port is also pinned in code (8317), so the stub upstream binds it
    # in this same namespace; the body keeps the production file byte-identical.
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    path = workdir / "cpa-admission.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    shutil.copy(ROUTES, workdir / "cpa_provider_routes.json")
    return path, config["listen_port"]  # 8318, only inside the shadow netns


def start_shadow(config_path, port, workdir):
    command = [sys.executable, "-P", str(ADMISSION), str(config_path)]
    # A missing circuit-state file is a refusal, not "start clean" (that is how
    # an operator hold survives a restart), so the shadow seeds a clear marker.
    for lane in LANES:
        (workdir / ("%s-risk.json" % lane)).write_text(
            json.dumps({"version": 1, "not_before": 0, "hold_reason": None}),
            encoding="utf-8",
        )
    # antigravity takes the plain --state-file; the others have their own flags.
    for flag, lane in (
        ("--state-file", "antigravity"),
        ("--chatgpt-state-file", "chatgpt"),
        ("--claude-state-file", "claude"),
        ("--workbuddy-state-file", "workbuddy"),
        ("--workbuddy-intl-state-file", "workbuddy-intl"),
    ):
        command += [flag, str(workdir / ("%s-risk.json" % lane))]
    log = open(workdir / "shadow.log", "wb")
    env = dict(os.environ)
    env.pop("VPS_SSH_LAUNCHER_ADMISSION_PROCESS_MARKER", None)
    env["PYTHONPATH"] = "/usr/local/libexec"
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                "SHADOW_EXITED early: "
                + (workdir / "shadow.log").read_text(errors="replace")[-800:]
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return process, log
        except OSError:
            time.sleep(0.2)
    raise SystemExit("SHADOW_NOT_LISTENING")


def post(port, model, body=None, timeout=90):
    import http.client

    payload = body if body is not None else json.dumps({"model": model, "input": "x"})
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        client.request(
            "POST", "/v1/responses", payload, {"Content-Type": "application/json"}
        )
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()


def health(port):
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:%d/healthz" % port, timeout=10) as response:
        return json.load(response)


def lane_state(port, lane):
    return health(port)["lanes"][lane]["state"]


def wait_for(predicate, seconds=10.0, interval=0.2):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def settle(port, lane, seconds=6.0):
    """Give the request time to finish and return the final lane state."""
    predicate = lambda: lane_state(port, lane)  # noqa: E731
    wait_for(lambda: lane_state(port, lane)["inflight"] == 0, seconds)
    return predicate()


def main():
    if Path("/proc/self/ns/net").readlink() == Path("/proc/1/ns/net").readlink():
        raise SystemExit("REFUSE_NOT_IN_NAMESPACE: run inside the disposable netns")
    checks = []
    workdir = Path(tempfile.mkdtemp(prefix="cpa-branch-acceptance-"))
    upstream = ThreadingHTTPServer(("127.0.0.1", 8317), Upstream)
    upstream.daemon_threads = True
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    config_path, port = write_config(workdir, upstream.server_address[1])
    if upstream.server_address[1] != 8317:
        raise SystemExit("STUB_UPSTREAM_NOT_ON_8317")
    process, log = start_shadow(config_path, port, workdir)
    try:
        # 1. account-shaped 6003 on the CN bare name stays lane-scoped and
        #    rides the transient capacity ladder. tests/test_cpa_admission.py
        #    pins capacity_scope == "lane" for 6003 ("no proven model-only
        #    wording"); only 6004 acquires the model shortcut. The pre-split
        #    pooled-models narrowing no longer exists -- the CN lane is
        #    single-model after the 12c487f CN/INTL split, so the lane
        #    breaker is the correct blast radius for an account-shaped signal.
        Upstream.status = 200
        Upstream.body = b'{"code":6003,"msg":"frequency limited"}'
        status, _ = post(port, "deepseek-v4.1-flash")
        state = settle(port, "workbuddy-oauth")
        checks.append(
            (
                "cn-6003-lane-scoped",
                status == 200
                and state.get("cooldown_scope") == "lane"
                and state.get("cooldown_active") is True
                and not state.get("model_cooldowns")
                and state.get("hold_reason") is None,
                "status=%s scope=%s active=%s remaining=%s cooldowns=%s"
                % (
                    status,
                    state.get("cooldown_scope"),
                    state.get("cooldown_active"),
                    state.get("cooldown_remaining"),
                    state.get("model_cooldowns"),
                ),
            )
        )

        # 2. a standing restriction on the INTL lane must park that lane, not
        # the CN lane. This is the controlled proof that the second bucket is
        # an independent admission/state boundary.
        Upstream.body = json.dumps(
            {"error": {"message": "Your account has been disabled.", "code": 403}}
        ).encode()
        post(port, "deepseek-v4.1-flash-sg")
        state = settle(port, "workbuddy-intl-oauth")
        cn_state = settle(port, "workbuddy-oauth")
        checks.append(
            (
                "intl-standing-restriction-isolated",
                state.get("hold_reason") == "account_restricted"
                and cn_state.get("hold_reason") is None,
                "intl_hold_reason=%s cn_hold_reason=%s"
                % (state.get("hold_reason"), cn_state.get("hold_reason")),
            )
        )

        # 3. Gemini RESOURCE_EXHAUSTED must reach the ladder even when it
        #    arrives as a non-429 status with no rate-limit wording.
        Upstream.status = 400
        Upstream.body = json.dumps(
            {
                "error": {
                    "code": 400,
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "RESOURCE_EXHAUSTED",
                        }
                    ],
                }
            }
        ).encode()
        post(port, "gemini-3.8-flash-high")
        state = settle(port, "antigravity-oauth")
        checks.append(
            (
                "gemini-resource-exhausted",
                bool(state.get("cooldown_active")),
                "cooldown_active=%s remaining=%s"
                % (state.get("cooldown_active"), state.get("cooldown_remaining")),
            )
        )

        # 4. an untyped RetryInfo still supplies the provider deadline, so the
        #    strict lane waits at least as long as the provider asked.
        Upstream.status = 429
        Upstream.body = json.dumps(
            {"error": {"details": [{"retryDelay": "20s"}]}}
        ).encode()
        post(port, "claude-sonnet-5-5-high")
        state = settle(port, "antigravity-oauth")
        checks.append(
            (
                "untyped-retry-delay",
                state.get("server_retry_after_remaining", 0) >= 15,
                "server_retry_after_remaining=%s"
                % state.get("server_retry_after_remaining"),
            )
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        upstream.shutdown()

    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(
            "BRANCH %-28s %s %s" % (name, "PASS" if ok else "FAIL", detail),
            flush=True,
        )
    print("BRANCHES_CHECKED=%d FAILED=%d" % (len(checks), len(failed)), flush=True)
    shutil.rmtree(workdir, ignore_errors=True)
    if failed:
        print("BRANCH_ACCEPTANCE=FAIL " + ",".join(failed), flush=True)
        return 1
    print("BRANCH_ACCEPTANCE=PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

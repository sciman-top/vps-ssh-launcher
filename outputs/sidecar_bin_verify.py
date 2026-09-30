#!/usr/bin/env python3
"""Isolated startup probe for a rebuilt cockpit-cliproxy sidecar binary (2026-09-30).

Starts the given binary against a *copy* of a live sidecar config on a scratch
port, checks that it binds and answers /v1/models, then terminates it. Never
touches the live sidecar (different port, and the copied config keeps
disable-auth-auto-refresh=true so no credential is refreshed).

Usage:
  python outputs/sidecar_bin_verify.py <exe> <config.json> <manifest.json> <port>
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

TIMEOUT_S = 20


def wait_port(port: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        s = socket.socket()
        s.settimeout(0.5)
        try:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        finally:
            s.close()
        time.sleep(0.4)
    return False


def main() -> int:
    exe, cfg, man, port = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    log_path = os.path.join(tempfile.gettempdir(), f"sidecar-iso-{port}.log")
    log = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [exe, "--config", cfg, "--manifest", man, "--parent-pid", str(os.getpid())],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    result = {"exe": os.path.basename(exe), "port": port, "pid": proc.pid, "log": log_path}
    try:
        result["bound"] = wait_port(port, time.monotonic() + TIMEOUT_S)
        result["alive_after_bind"] = proc.poll() is None
        if result["bound"]:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/models", method="GET")
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    body = resp.read()
                result["models_http"] = resp.status
                result["models_bytes"] = len(body)
                try:
                    data = json.loads(body)
                    ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)]
                    result["model_count"] = len(ids)
                except Exception:
                    result["model_count"] = None
            except urllib.error.HTTPError as exc:
                result["models_http"] = exc.code
            except Exception as exc:  # noqa: BLE001
                result["models_error"] = type(exc).__name__
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        result["exit_code"] = proc.poll()
        log.close()

    tail = ""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            tail = fh.read()[-1200:]
    except OSError:
        pass
    result["log_tail"] = tail
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("bound") and result.get("models_http") == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Controlled acceptance for the r3 local-gate wait cap (2026-10-02).

Question
--------
The provider-gateway sidecar's manifest still says `accountConcurrencyWaitMs:
120000` because the official generator never copies the account-concurrency
family into that collection (and did not even rewrite the file on the 22:30
restart). r3 caps the budget at 45000 ms in the sidecar entry. Does the cap
actually change the observed 429 latency?

Design
------
Isolated scratch instance, no upstream quota consumed:

  * a local stub upstream that accepts a connection and never replies, so each
    admitted request holds its account slot for as long as we want;
  * a copy of the live provider-gateway config + manifest, with the port moved
    to a scratch port, `proxy-url` removed (the stub is on loopback) and the
    stream-open timeout raised so the holders do not self-release;
  * identical config and manifest for both runs, so the *only* variable is the
    binary: r2 (no cap) vs r3 (cap at 45000 ms);
  * 3 concurrent requests to occupy maxAccountConcurrency=3, then a 4th whose
    429 latency is measured.

Expected: r2 ~120 s, r3 ~45 s.
"""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRATCH_PORT = 19109
STUB_PORT = 19110
HOLDERS = 3
WAIT_OBSERVE_LIMIT_S = 200.0

R3 = pathlib.Path(
    r"C:\Users\sciman\AppData\Local\Temp\cockpit-tools-v1.3.65-build-20261002"
    r"\sidecars\cockpit-cliproxy\cockpit-cliproxy-v135-gate-r3-20261002.exe"
)
R2 = pathlib.Path(
    r"C:\Users\sciman\AppData\Local\Cockpit Tools"
    r"\cockpit-cliproxy.exe.v135-gate-r2-20261002.bak"
)
LIVE_DIR = pathlib.Path(
    r"C:\Users\sciman\.cockpit_tools\codex_provider_gateway_sidecars"
    r"\36218dcc01e42a7a11f4730c7aca7f9508c68f8f9473f7532754771b5c587eee"
)


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        # Deliberately never respond: the slot must stay occupied.
        time.sleep(300)

    def log_message(self, *args: object) -> None:  # silence stderr
        return


def wait_port(port: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        sock = socket.socket()
        sock.settimeout(0.4)
        try:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        finally:
            sock.close()
        time.sleep(0.3)
    return False


def build_scratch(root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, str]:
    config = json.loads((LIVE_DIR / "config.json").read_text(encoding="utf-8"))
    manifest = json.loads((LIVE_DIR / "manifest.json").read_text(encoding="utf-8"))

    config["port"] = SCRATCH_PORT
    config.pop("proxy-url", None)
    config.setdefault("streaming", {})
    config["streaming"]["stream-open-timeout-ms"] = 600000
    config["streaming"]["stream-idle-timeout-ms"] = 600000
    config["logging-to-file"] = False

    key = config["api-keys"][0]
    for spec in manifest.get("apiKeys", []):
        if spec.get("providerGateway"):
            spec["providerGateway"]["baseUrl"] = f"http://127.0.0.1:{STUB_PORT}/v1"

    config_path = root / "config.json"
    manifest_path = root / "manifest.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return config_path, manifest_path, key


def launch(exe: pathlib.Path, config: pathlib.Path, manifest: pathlib.Path, root: pathlib.Path):
    log = (root / f"{exe.name}.log").open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [
            str(exe),
            "--config", str(config),
            "--manifest", str(manifest),
            "--quota-reserve-state", str(root / "quota-reserve.json"),
            "--quota-pool-state", str(root / "quota-pool-state.json"),
            "--parent-pid", str(os.getpid()),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc, log


def fire(key: str, model: str, label: str, results: dict, index: int) -> None:
    started = time.monotonic()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", SCRATCH_PORT, timeout=WAIT_OBSERVE_LIMIT_S)
        body = json.dumps({"model": model, "input": "hold", "stream": True})
        conn.request(
            "POST", "/v1/responses", body=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        elapsed = time.monotonic() - started
        payload = resp.read(400).decode("utf-8", "replace")
        results[index] = {"label": label, "status": resp.status,
                          "elapsed_s": round(elapsed, 3), "body": payload}
    except Exception as exc:  # noqa: BLE001 - report, never crash the run
        results[index] = {"label": label, "status": None,
                          "elapsed_s": round(time.monotonic() - started, 3),
                          "body": f"<{type(exc).__name__}: {exc}>"}


def run_case(exe: pathlib.Path, key: str, model: str) -> dict:
    root = pathlib.Path(tempfile.mkdtemp(prefix=f"gatecap-{exe.stem[:12]}-"))
    config, manifest, _ = build_scratch(root)
    proc, log = launch(exe, config, manifest, root)
    outcome: dict = {"exe": exe.name, "scratch": str(root)}
    try:
        if not wait_port(SCRATCH_PORT, time.monotonic() + 25):
            outcome["error"] = "scratch sidecar did not bind"
            return outcome
        time.sleep(1.0)
        results: dict = {}
        threads = []
        for i in range(HOLDERS):
            t = threading.Thread(target=fire, args=(key, model, f"holder{i}", results, i))
            t.start()
            threads.append(t)
            time.sleep(0.35)
        time.sleep(2.0)
        waiter = threading.Thread(target=fire, args=(key, model, "waiter", results, 99))
        waiter.start()
        waiter.join(timeout=WAIT_OBSERVE_LIMIT_S)
        for t in threads:
            t.join(timeout=1.0)
        outcome["holders"] = [results.get(i) for i in range(HOLDERS)]
        outcome["waiter"] = results.get(99)
        return outcome
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        log.close()
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    for path in (R3, R2, LIVE_DIR / "config.json", LIVE_DIR / "manifest.json"):
        if not path.exists():
            print(f"FAIL missing: {path}")
            return 2

    _, _, key = build_scratch(pathlib.Path(tempfile.mkdtemp(prefix="gatecap-probe-")))
    print(f"key length = {len(key)} (value not printed)")
    model = "gpt-6.1-sol"

    stub = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), StubHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    print(f"stub upstream listening on 127.0.0.1:{STUB_PORT} (never replies)")
    print(f"scratch sidecar port {SCRATCH_PORT}; holders={HOLDERS}; model={model}")
    print()

    cases = [("r3 (cap 45000ms)", R3), ("r2 (no cap)", R2)]
    report = []
    for label, exe in cases:
        print(f"--- running {label} ---")
        result = run_case(exe, key, model)
        report.append((label, result))
        waiter = result.get("waiter") or {}
        print(f"    holders: {[ (h or {}).get('status') for h in result.get('holders', []) ]}")
        print(f"    waiter : status={waiter.get('status')} elapsed={waiter.get('elapsed_s')}s")
        print(f"    waiter body: {str(waiter.get('body'))[:160]}")
        print()

    stub.shutdown()
    print("=== comparison ===")
    for label, result in report:
        waiter = result.get("waiter") or {}
        print(f"{label:>20} : status={waiter.get('status')} latency={waiter.get('elapsed_s')}s")

    ok = True
    r3 = report[0][1].get("waiter") or {}
    r2 = report[1][1].get("waiter") or {}
    if r3.get("status") != 429:
        print("FAIL r3 waiter did not get 429")
        ok = False
    elif not (35.0 <= (r3.get("elapsed_s") or 0) <= 70.0):
        print(f"FAIL r3 waiter latency {r3.get('elapsed_s')}s is not near the 45s bound")
        ok = False
    if r2.get("status") == 429 and (r2.get("elapsed_s") or 0) >= 100.0:
        print("control confirmed: r2 honoured the manifest's 120000 ms")
    else:
        print(f"NOTE r2 control was inconclusive: status={r2.get('status')} "
              f"latency={r2.get('elapsed_s')}s")
    print("ACCEPTANCE_PASS" if ok else "ACCEPTANCE_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

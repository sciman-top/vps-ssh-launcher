#!/usr/bin/env python3
"""Controlled acceptance for the sidecar local-gate wait cap (zero quota).

Promoted from outputs/gate-wait-cap-acceptance-20261002.py (2026-10-02,
r2-vs-r3 A/B) into a maintained tool: run it after re-applying the sidecar
patch (e.g. a Cockpit official update replaced the exe) to prove the wait
cap still bounds the 429 latency. The remote chain is never touched.

Design (unchanged from the proven harness):
  * a stub upstream on loopback that accepts connections and never replies,
    so every admitted request holds its account slot indefinitely;
  * a scratch copy of the LIVE provider-gateway config + manifest with the
    port moved to a scratch port, proxy-url removed and stream timeouts
    raised, so holders never self-release;
  * 3 concurrent requests occupy maxAccountConcurrency=3 (clamped), then a
    4th measures the 429 latency.

Single-run mode (default): the candidate is the installed exe; pass/fail is
429 within [0.7x, 1.6x] the expected cap. A/B mode (--control <exe>) adds
the unpatched/previous build as a control, exactly like the r2-vs-r3 run.

Usage:
  ./.venv/Scripts/python.exe scripts/cockpit_gate_wait_cap_check.py
  ./.venv/Scripts/python.exe scripts/cockpit_gate_wait_cap_check.py \
      --expect-cap-s 45 --control "C:/path/to/old.exe"
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import IO

DEFAULT_SCRATCH_PORT = 19109
DEFAULT_STUB_PORT = 19110
HOLDERS = 3
WAIT_OBSERVE_LIMIT_S = 200.0
INSTALL_DIR = pathlib.Path("~/AppData/Local/Cockpit Tools").expanduser()
GATEWAY_ROOT = pathlib.Path(
    "~/.cockpit_tools/codex_provider_gateway_sidecars"
).expanduser()
POLICY_PATH = pathlib.Path(__file__).with_name("cockpit_sidecar_policy.json")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_log_tail(log: IO[str], lines: int = 20) -> str:
    log.flush()
    try:
        entries = (
            pathlib.Path(log.name)
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()
        )
    except OSError as exc:
        return f"<log unavailable: {type(exc).__name__}: {exc}>"
    tail = "\n".join(entries[-lines:])
    return re.sub(r'("apiKey(?:Id|Label)"\s*:\s*")[^"]*', r"\1<redacted>", tail)


def expected_sidecar_sha256() -> str | None:
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expected = policy.get("sidecarSha256")
    return expected.upper() if isinstance(expected, str) else None


def capture_diagnostics(
    outcome: dict, proc: subprocess.Popen[bytes], log: IO[str]
) -> None:
    outcome["process_id"] = proc.pid
    outcome["process_exit_code"] = proc.poll()
    outcome["process_alive"] = proc.poll() is None
    waiter = outcome.get("waiter")
    if outcome.get("error") or waiter is None or waiter.get("status") is None:
        outcome["log_tail"] = read_log_tail(log)


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


def find_live_dir() -> pathlib.Path:
    # The provider-gateway config lives under an install-specific hash dir.
    for config in sorted(GATEWAY_ROOT.glob("*/config.json")):
        return config.parent
    raise SystemExit(f"FAIL no provider-gateway config under {GATEWAY_ROOT}")


def build_scratch(
    live_dir: pathlib.Path, root: pathlib.Path, scratch_port: int, stub_port: int
) -> tuple[pathlib.Path, pathlib.Path, str]:
    config = json.loads((live_dir / "config.json").read_text(encoding="utf-8"))
    manifest = json.loads((live_dir / "manifest.json").read_text(encoding="utf-8"))

    config["port"] = scratch_port
    config.pop("proxy-url", None)
    config.setdefault("streaming", {})
    config["streaming"]["stream-open-timeout-ms"] = 600000
    config["streaming"]["stream-idle-timeout-ms"] = 600000
    config["logging-to-file"] = False

    key = config["api-keys"][0]
    for spec in manifest.get("apiKeys", []):
        if spec.get("providerGateway"):
            spec["providerGateway"]["baseUrl"] = f"http://127.0.0.1:{stub_port}/v1"

    config_path = root / "config.json"
    manifest_path = root / "manifest.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # The sidecar treats these as persistent state files.  Supplying an empty
    # valid state keeps a scratch run self-contained and avoids startup errors
    # that obscure the admission result under test.
    empty_state = json.dumps({"accounts": {}}, indent=2) + "\n"
    (root / "quota-reserve.json").write_text(empty_state, encoding="utf-8")
    (root / "quota-pool-state.json").write_text(empty_state, encoding="utf-8")
    return config_path, manifest_path, key


def launch(
    exe: pathlib.Path,
    config: pathlib.Path,
    manifest: pathlib.Path,
    root: pathlib.Path,
) -> tuple[subprocess.Popen[bytes], IO[str]]:
    log = (root / f"{exe.name}.log").open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [
            str(exe),
            "--config",
            str(config),
            "--manifest",
            str(manifest),
            "--quota-reserve-state",
            str(root / "quota-reserve.json"),
            "--quota-pool-state",
            str(root / "quota-pool-state.json"),
            "--parent-pid",
            str(os.getpid()),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc, log


def fire(key: str, model: str, scratch_port: int, results: dict, index: int) -> None:
    started = time.monotonic()
    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", scratch_port, timeout=WAIT_OBSERVE_LIMIT_S
        )
        body = json.dumps({"model": model, "input": "hold", "stream": True})
        conn.request(
            "POST",
            "/v1/responses",
            body=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        resp = conn.getresponse()
        elapsed = time.monotonic() - started
        payload = resp.read(400).decode("utf-8", "replace")
        results[index] = {
            "status": resp.status,
            "elapsed_s": round(elapsed, 3),
            "body": payload,
        }
    except Exception as exc:  # noqa: BLE001 - report, never crash the run
        results[index] = {
            "status": None,
            "elapsed_s": round(time.monotonic() - started, 3),
            "body": f"<{type(exc).__name__}: {exc}>",
        }
    finally:
        if conn is not None:
            conn.close()


def run_case(
    exe: pathlib.Path,
    live_dir: pathlib.Path,
    key: str,
    model: str,
    scratch_port: int,
    stub_port: int,
) -> dict:
    root = pathlib.Path(tempfile.mkdtemp(prefix=f"gatecap-{exe.stem[:16]}-"))
    config, manifest, _ = build_scratch(live_dir, root, scratch_port, stub_port)
    proc, log = launch(exe, config, manifest, root)
    outcome: dict = {"exe": exe.name}
    try:
        if not wait_port(scratch_port, time.monotonic() + 25):
            outcome["error"] = "scratch sidecar did not bind"
            capture_diagnostics(outcome, proc, log)
            return outcome
        time.sleep(1.0)
        results: dict = {}
        threads = []
        for i in range(HOLDERS):
            thread = threading.Thread(
                target=fire, args=(key, model, scratch_port, results, i)
            )
            thread.start()
            threads.append(thread)
            time.sleep(0.35)
        time.sleep(2.0)
        waiter = threading.Thread(
            target=fire, args=(key, model, scratch_port, results, 99)
        )
        waiter.start()
        waiter.join(timeout=WAIT_OBSERVE_LIMIT_S)
        for thread in threads:
            thread.join(timeout=1.0)
        outcome["holders"] = [results.get(i) for i in range(HOLDERS)]
        outcome["waiter"] = results.get(99)
        capture_diagnostics(outcome, proc, log)
        return outcome
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        log.close()
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--candidate",
        default="",
        help="exe under test (default: installed, policy-pinned cockpit-cliproxy.exe)",
    )
    parser.add_argument(
        "--control",
        default="",
        help="optional second exe (e.g. previous/official build) for A/B",
    )
    parser.add_argument(
        "--expect-cap-s",
        type=float,
        default=45.0,
        help="expected wait cap in seconds (default 45)",
    )
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--scratch-port", type=int, default=DEFAULT_SCRATCH_PORT)
    parser.add_argument("--stub-port", type=int, default=DEFAULT_STUB_PORT)
    args = parser.parse_args()

    candidate_arg = args.candidate.strip()
    candidate = pathlib.Path(
        candidate_arg or (INSTALL_DIR / "cockpit-cliproxy.exe")
    ).expanduser()
    if not candidate.exists():
        print(f"FAIL missing candidate exe: {candidate}")
        return 2
    live_dir = find_live_dir()
    candidate_sha = sha256_file(candidate)
    print(f"live config dir = {live_dir.name}/  candidate = {candidate.name}")
    print(f"candidate sha256 = {candidate_sha}")
    if not candidate_arg:
        expected_sha = expected_sidecar_sha256()
        if expected_sha and candidate_sha != expected_sha:
            print(
                "FAIL installed candidate sha256 mismatch: "
                f"expected {expected_sha}, got {candidate_sha}"
            )
            print(
                "provide --candidate with the verified sidecar build for A/B simulation"
            )
            return 2
    print(
        f"scratch port {args.scratch_port}; stub port {args.stub_port}; "
        f"holders={HOLDERS}; model={args.model}"
    )

    scratch_probe = pathlib.Path(tempfile.mkdtemp(prefix="gatecap-probe-"))
    _, _, key = build_scratch(
        live_dir, scratch_probe, args.scratch_port, args.stub_port
    )
    shutil.rmtree(scratch_probe, ignore_errors=True)
    print(f"key length = {len(key)} (value not printed)")

    stub = ThreadingHTTPServer(("127.0.0.1", args.stub_port), StubHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    print(f"stub upstream listening on 127.0.0.1:{args.stub_port} (never replies)")
    print()

    ok = True
    cases = [("candidate", candidate)]
    if args.control:
        cases.append(("control", pathlib.Path(args.control).expanduser()))

    outcomes = {}
    for label, exe in cases:
        print(f"--- running {label}: {exe.name} ---")
        result = run_case(
            exe, live_dir, key, args.model, args.scratch_port, args.stub_port
        )
        outcomes[label] = result
        waiter = result.get("waiter") or {}
        print(
            f"    holders: {[h.get('status') if h else None for h in result.get('holders', [])]}"
        )
        print(
            f"    waiter : status={waiter.get('status')} elapsed={waiter.get('elapsed_s')}s"
        )
        print(f"    waiter body: {str(waiter.get('body'))[:160]}")
        if result.get("error"):
            print(
                f"    process: pid={result.get('process_id')} exit={result.get('process_exit_code')}"
            )
            print(f"    error  : {result['error']}")
        elif result.get("process_exit_code") is not None or result.get("log_tail"):
            print(
                "    process: "
                f"pid={result.get('process_id')} "
                f"exit={result.get('process_exit_code')} "
                f"alive={result.get('process_alive')}"
            )
            if result.get("log_tail"):
                print("    log tail:\n" + result["log_tail"])
        print()

    stub.shutdown()

    print("=== verdict ===")
    waiter = outcomes["candidate"].get("waiter") or {}
    lower, upper = args.expect_cap_s * 0.7, args.expect_cap_s * 1.6
    if waiter.get("status") != 429:
        print(f"FAIL candidate waiter did not get 429 (status={waiter.get('status')})")
        ok = False
    elif not (lower <= (waiter.get("elapsed_s") or 0) <= upper):
        print(
            f"FAIL candidate waiter latency {waiter.get('elapsed_s')}s outside "
            f"[{lower:.0f}s, {upper:.0f}s] around the {args.expect_cap_s}s cap"
        )
        ok = False
    else:
        print(
            f"candidate: 429 at {waiter.get('elapsed_s')}s "
            f"(cap {args.expect_cap_s}s honoured)"
        )
    if "control" in outcomes:
        control = outcomes["control"].get("waiter") or {}
        print(
            f"control  : status={control.get('status')} latency={control.get('elapsed_s')}s"
        )

    print("ACCEPTANCE_PASS" if ok else "ACCEPTANCE_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

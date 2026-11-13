#!/usr/bin/env python3
"""Start the single request-lock owner before admission worker threads."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

REQUEST_LOCK_PATH = "/run/cpa-admission/requests.lock"
REQUEST_GUARD_PATH = "/usr/local/libexec/cpa_request_guard.py"
ADMISSION_PATH = "/opt/cliproxyapi/cpa-admission.py"
REQUEST_GUARD_FD = 9
SOCKET_FAMILY = cast(int, vars(socket).get("AF_UNIX", 1))
RUNTIME_TOKEN_PATH = "/run/cpa-admission/runtime-token"
RUNTIME_RECEIPT_PATH = "/run/cpa-admission/runtime-receipt"
STARTUP_IDENTITY_PATH = "/run/cpa-admission/startup-generation"


def _linux_stat(pid: int) -> list[str]:
    return (
        Path(f"/proc/{pid}/task/{pid}/stat")
        .read_text(encoding="ascii")
        .rsplit(")", 1)[1]
        .split()
    )


def _linux_start(pid: int) -> str:
    return _linux_stat(pid)[19]


def _has_start(pid: int, start: str) -> bool:
    try:
        fields = _linux_stat(pid)
        return fields[1] != "1" and fields[19].casefold() == start.casefold()
    except (IndexError, OSError, UnicodeError):
        return False


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    result = b""
    while len(result) < size:
        part = sock.recv(size - len(result))
        if not part:
            raise OSError("request guard socket closed")
        result += part
    return result


def _log(message: str) -> None:
    sys.stderr.write(f"cpa-admission-bootstrap {message}\n")
    sys.stderr.flush()


def _load_guard() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_cpa_request_guard_audited", REQUEST_GUARD_PATH
    )
    if spec is None or spec.loader is None:
        raise OSError("request guard module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not isinstance(getattr(module, "SOURCE_SHA256", ""), str):
        raise OSError("request guard generation unavailable")
    if not callable(getattr(module, "serve", None)):
        raise OSError("request guard entrypoint unavailable")
    return module


def _write_atomic(path: str, value: str) -> None:
    target = Path(path)
    temporary = target.with_name(target.name + ".new")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.write(descriptor, value.encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, target)


def validate_startup_token(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def main() -> int:
    if os.name != "posix" or not hasattr(os, "fork"):
        return 1
    admission: subprocess.Popen[bytes] | None = None
    guard_pid: int | None = None
    marker = Path("/run/cpa-admission/process")
    bridge: socket.socket | None = None
    admission_bridge: socket.socket | None = None
    try:
        guard = _load_guard()
        source_sha256 = cast(str, guard.SOURCE_SHA256)
        if len(source_sha256) != 64:
            raise OSError("request guard generation invalid")
        identity = secrets.token_hex(16)
        runtime_token = secrets.token_hex(16)
        runtime_binding = {
            "token": runtime_token,
            "identity": identity,
            "parent_pid": 0,
            "guard_pid": 0,
            "guard_start": "",
        }
        _write_atomic(STARTUP_IDENTITY_PATH, identity + "\n")
        _write_atomic(RUNTIME_TOKEN_PATH, json.dumps(runtime_binding))
        Path(RUNTIME_RECEIPT_PATH).unlink(missing_ok=True)
        marker = Path(
            os.environ.get(
                "VPS_SSH_LAUNCHER_ADMISSION_PROCESS_MARKER",
                "/run/cpa-admission/process",
            )
        )
        marker.unlink(missing_ok=True)
        parent_pid = os.getpid()
        parent_start = _linux_start(parent_pid)
        broker_sock, guard_sock = socket.socketpair(SOCKET_FAMILY, socket.SOCK_STREAM)
        guard_fd = guard_sock.detach()
        os.set_inheritable(guard_fd, False)
        guard_pid = os.fork()
        if guard_pid == 0:
            broker_fd = broker_sock.detach()
            os.close(broker_fd)
            try:
                status = guard.serve(
                    parent_pid,
                    guard_fd,
                    REQUEST_LOCK_PATH,
                    parent_start,
                )
            except BaseException as exc:
                _log(f"guard_failed error={type(exc).__name__}")
                os._exit(1)
            os._exit(cast(int, status))
        try:
            os.close(guard_fd)
        except OSError:
            pass
        broker_fd = broker_sock.detach()
        if broker_fd != REQUEST_GUARD_FD:
            os.dup2(broker_fd, REQUEST_GUARD_FD, inheritable=True)
            os.close(broker_fd)
        broker_sock = socket.socket(fileno=REQUEST_GUARD_FD)
        broker_sock.settimeout(5)
        broker_sock.sendall(b"C" + b"0" * 15)
        if _recv_exact(broker_sock, 6) != b"\0" * 6:
            raise OSError("guard handshake failed")
        guard_start = _linux_start(guard_pid)
        runtime_binding = {
            "token": runtime_token,
            "identity": identity,
            "parent_pid": parent_pid,
            "guard_pid": guard_pid,
            "guard_start": guard_start,
        }
        _write_atomic(RUNTIME_TOKEN_PATH, json.dumps(runtime_binding))
        bridge, admission_bridge = socket.socketpair(SOCKET_FAMILY, socket.SOCK_STREAM)
        # The bridge carries only visible readiness. Mutable request leases
        # live exclusively in the single-threaded guard endpoint above.
        # This byte authorizes the inherited guard descriptor; it carries no
        # descriptor. Leases are transferred later on that guard socket.
        bridge.sendall(b"f")
        bridge.close()
        bridge = None
        bridge_fd = admission_bridge.detach()
        os.set_inheritable(bridge_fd, True)
        target = Path(
            os.environ.get("VPS_SSH_LAUNCHER_ADMISSION_TARGET", ADMISSION_PATH)
        )
        if hashlib.sha256(target.read_bytes()).hexdigest() != source_sha256:
            raise OSError("admission generation mismatch")
        admission = subprocess.Popen(
            [
                sys.executable,
                "-P",
                "-c",
                (
                    "import runpy, sys; "
                    "namespace = runpy.run_path(sys.argv[1], run_name='admission'); "
                    "raise SystemExit(namespace['main']("
                    "int(sys.argv[3]), sys.argv[4], "
                    "int(sys.argv[5]), int(sys.argv[6])))"
                ),
                str(target),
                str(target.parent),
                str(parent_pid),
                parent_start,
                str(bridge_fd),
                str(REQUEST_GUARD_FD),
                os.environ.get(
                    "VPS_SSH_LAUNCHER_ADMISSION_CONFIG",
                    "/opt/cliproxyapi/cpa-admission.json",
                ),
                "--state-file",
                "/var/lib/cpa-admission/antigravity-risk.json",
                "--claude-state-file",
                "/var/lib/cpa-admission/claude-risk.json",
                "--chatgpt-state-file",
                "/var/lib/cpa-admission/chatgpt-risk.json",
                "--workbuddy-state-file",
                "/var/lib/cpa-admission/workbuddy-risk.json",
            ],
            stdin=subprocess.DEVNULL,
            pass_fds=(REQUEST_GUARD_FD, bridge_fd),
        )
        os.close(bridge_fd)
        deadline = time.monotonic() + 30
        while not Path(RUNTIME_RECEIPT_PATH).exists():
            if admission.poll() is not None:
                raise OSError("admission exited before readiness")
            if time.monotonic() >= deadline:
                raise OSError("admission readiness receipt unavailable")
            time.sleep(0.02)
        admission_start = _linux_start(admission.pid)
        if not _has_start(admission.pid, admission_start):
            raise OSError("admission process generation missing")
        try:
            runtime = json.loads(Path(RUNTIME_RECEIPT_PATH).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise OSError("admission runtime receipt unavailable") from None
        if (
            not isinstance(runtime, dict)
            or runtime.get("token") != runtime_token
            or runtime.get("identity") != identity
            or runtime.get("parent_pid") != parent_pid
            or runtime.get("admission_pid") != admission.pid
            or runtime.get("guard_pid") != guard_pid
            or runtime.get("guard_start") != guard_start
        ):
            raise OSError("admission runtime receipt invalid")
        writing = marker.with_name(marker.name + ".new")
        with writing.open("w", encoding="ascii") as output:
            stamp = os.fstat(output.fileno()).st_mtime_ns
            output.write(
                f"1 {stamp} {REQUEST_GUARD_FD} {guard_pid} "
                f"{parent_pid} {parent_start} {admission.pid} {admission_start}\n"
            )
        os.replace(writing, marker)
        _log(
            f"guard_ready parent={parent_pid} guard={guard_pid} "
            f"admission={admission.pid} socket_fd={REQUEST_GUARD_FD}"
        )
        return admission.wait()
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as exc:
        _log(f"startup_failed error={type(exc).__name__} detail={exc}")
        if admission is not None and admission.poll() is None:
            admission.terminate()
            try:
                admission.wait(timeout=10)
            except subprocess.TimeoutExpired:
                admission.kill()
                admission.wait()
        if guard_pid is not None:
            try:
                os.kill(guard_pid, 15)
            except ProcessLookupError:
                pass
        for bridge_socket in (bridge, admission_bridge):
            if bridge_socket is not None:
                try:
                    bridge_socket.close()
                except OSError:
                    pass
        for path in (
            marker,
            Path(RUNTIME_RECEIPT_PATH),
            Path(RUNTIME_TOKEN_PATH),
            Path(STARTUP_IDENTITY_PATH),
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

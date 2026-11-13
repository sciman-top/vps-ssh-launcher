#!/usr/bin/env python3
"""Start the lock guard before the admission's request threads exist."""

from __future__ import annotations

import hashlib
import os
import socket
import struct
import subprocess
import sys
from pathlib import Path
from typing import cast

REQUEST_LOCK_PATH = "/run/cpa-admission/requests.lock"
REQUEST_GUARD_PATH = "/usr/local/libexec/cpa_request_guard.py"
ADMISSION_PATH = "/opt/cliproxyapi/cpa-admission.py"
# The private broker socket crosses one exec. Do not turn this into a
# subprocess.Popen pass_fds handoff: CPython may renumber those descriptors.
REQUEST_GUARD_FD = 9
O_CLOEXEC = cast(int, vars(os).get("O_CLOEXEC", 0))
SO_PEERCRED = cast(int, vars(socket).get("SO_PEERCRED", 17))
SOCKET_FAMILY = socket.AF_UNIX if sys.platform.startswith("linux") else 1


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
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise OSError("guard socket closed")
        result += chunk
    return result


def _log(message: str) -> None:
    sys.stderr.write(f"cpa-admission-bootstrap {message}\n")
    sys.stderr.flush()


def _peer_pid(fd: int) -> int:
    descriptor = os.open(f"/proc/self/fd/{fd}", os.O_RDWR | O_CLOEXEC)
    try:
        return struct.unpack(
            "3i",
            socket.getsockopt(  # type: ignore[attr-defined]
                descriptor, socket.SOL_SOCKET, SO_PEERCRED, 12
            ),
        )[0]
    finally:
        os.close(descriptor)


def _pin_fd(sock: socket.socket) -> socket.socket:
    """Own exactly one inherited broker descriptor, pinned to fd 9."""
    source_fd = sock.fileno()
    source_link = os.readlink(f"/proc/self/fd/{source_fd}")
    if not source_link.startswith("socket:["):
        raise OSError("broker generation is not a socket")
    if source_fd != REQUEST_GUARD_FD:
        saved = Path(f"/proc/self/fd/{REQUEST_GUARD_FD}")
        if saved.exists():
            saved_link = os.readlink(saved)
            if saved_link.startswith("socket:[") and saved_link != source_link:
                raise OSError("fd9 is a distinct live socket")
        # The pin is allocated only after moving the original socket object.
        sock.detach()
        try:
            os.dup2(source_fd, REQUEST_GUARD_FD, inheritable=True)
        except OSError:
            os.close(source_fd)
            raise
        sock.close()
    os.set_inheritable(REQUEST_GUARD_FD, True)
    result = socket.socket(fileno=REQUEST_GUARD_FD)
    if os.readlink(f"/proc/self/fd/{REQUEST_GUARD_FD}") != source_link:
        result.close()
        raise OSError("broker descriptor pin mismatch")
    return result


def _load_guard() -> object:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_cpa_request_guard_audited", REQUEST_GUARD_PATH
    )
    if spec is None or spec.loader is None:
        raise OSError("audited request guard unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = getattr(module, "SOURCE_SHA256", "")
    if not isinstance(source, str) or len(source) != 64:
        raise OSError("guard source generation is invalid")
    if not hasattr(module, "serve"):
        raise OSError("guard serve entry point unavailable")
    return module


def main() -> int:
    if os.name != "posix" or not hasattr(os, "fork"):
        return 1
    admission: subprocess.Popen[bytes] | None = None
    guard_pid: int | None = None
    marker_path = Path("/run/cpa-admission/process")
    try:
        guard_module = _load_guard()
        module_source = cast(str, getattr(guard_module, "SOURCE_SHA256"))
        marker_path = Path(
            os.environ.get(
                "VPS_SSH_LAUNCHER_ADMISSION_PROCESS_MARKER",
                "/run/cpa-admission/process",
            )
        )
        marker_path.unlink(missing_ok=True)
        parent_pid = os.getpid()
        parent_start = _linux_start(parent_pid)
        parent_sock, guard_sock = socket.socketpair(
            SOCKET_FAMILY, socket.SOCK_STREAM
        )
        guard_fd = guard_sock.fileno()
        os.set_inheritable(guard_fd, True)
        guard_pid = os.fork()
        if guard_pid == 0:
            parent_sock.close()
            try:
                result = getattr(guard_module, "serve")(
                    parent_pid, guard_sock.detach(), REQUEST_LOCK_PATH, parent_start
                )
            except BaseException as exc:
                _log(f"guard_failed error={type(exc).__name__}")
                os._exit(1)
            os._exit(result)
        guard_sock.close()
        broker = _pin_fd(parent_sock)
        broker.settimeout(5)
        if _peer_pid(REQUEST_GUARD_FD) != guard_pid:
            raise OSError("guard peer identity mismatch")
        broker.sendall(b"C" + b"0" * 15)
        if _recv_exact(broker, 6) != b"\0" * 6:
            raise OSError("guard handshake failed")
        target = Path(ADMISSION_PATH)
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != module_source:
            raise OSError("admission generation mismatch")
        admission = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "import runpy, sys; "
                    "namespace = runpy.run_path(sys.argv[1], run_name='admission'); "
                    "raise SystemExit(namespace['main']("
                    "int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), int(sys.argv[5])))"
                ),
                str(target),
                str(parent_pid),
                parent_start,
                str(REQUEST_GUARD_FD),
                str(REQUEST_GUARD_FD),
            ],
            pass_fds=(REQUEST_GUARD_FD,),
        )
        broker.settimeout(30)
        try:
            receipt = _recv_exact(broker, 6)
        except OSError:
            admission.terminate()
            admission.wait(timeout=10)
            raise OSError("admission startup receipt missing") from None
        if receipt != b"R" + b"0" * 5 or admission.poll() is not None:
            admission.terminate()
            admission.wait(timeout=10)
            raise OSError("admission startup receipt invalid")
        admission_start = _linux_start(admission.pid)
        if not _has_start(admission.pid, admission_start) or admission.poll() is not None:
            raise OSError("admission generation exited")
        # Commit replaces a fully written file. Its parent fsyncs the directory
        # in the deployment transaction, so consumers never see a partial line.
        waiting = marker_path.with_name(marker_path.name + ".new")
        waiting.write_text(
            f"1 {waiting.stat().st_mtime_ns} {REQUEST_GUARD_FD} "
            f"{guard_pid} {parent_pid} {parent_start} "
            f"{admission.pid} {admission_start}\n",
            encoding="ascii",
        )
        os.replace(waiting, marker_path)
        _log(
            f"guard_ready parent={parent_pid} guard={guard_pid} "
            f"admission={admission.pid} socket_fd={REQUEST_GUARD_FD}"
        )
        return admission.wait()
    except (OSError, UnicodeError, ValueError) as exc:
        _log(f"startup_failed error={type(exc).__name__} detail={exc}")
        if admission is not None and admission.poll() is None:
            admission.terminate()
            admission.wait(timeout=10)
        try:
            marker_path.unlink(missing_ok=True)
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

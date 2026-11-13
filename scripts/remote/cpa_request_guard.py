"""The sole owner of request-lock descriptors for one admission generation."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import socket
import struct
from pathlib import Path
from typing import TextIO

SOURCE_SHA256 = "__SOURCE_SHA256__"


def _parent_death_signal() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, 15, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PDEATHSIG")


def _send(sock: socket.socket, value: bytes) -> None:
    pending = memoryview(value)
    while pending:
        sent = sock.send(pending)
        if sent <= 0:
            raise OSError(errno.ECONNRESET, "guard client disconnected")
        pending = pending[sent:]


def serve(
    parent_pid: int,
    fd: int,
    lock_path: str,
    expected_parent_start: str = "",
) -> int:
    """Serve the admission's lease client without spawning another owner."""
    _parent_death_signal()
    if fd < 3 or os.getppid() != parent_pid:
        return 1
    if expected_parent_start:
        try:
            start = (
                Path(f"/proc/{parent_pid}/task/{parent_pid}/stat")
                .read_text(encoding="ascii")
                .rsplit(")", 1)[1]
                .split()[19]
            )
        except (OSError, UnicodeError, IndexError):
            return 1
        if start != expected_parent_start:
            return 1
    broker = socket.socket(fileno=fd)
    os.set_inheritable(fd, False)
    leases: dict[int, TextIO] = {}

    def error_reply(error: int) -> bytes:
        return struct.pack("!iH", error, 0)

    try:
        while True:
            frame = b""
            while len(frame) < 16:
                part = broker.recv(16 - len(frame))
                if not part:
                    return 1
                frame += part
            opcode = chr(frame[0])
            if opcode not in {"C", "A", "R", "X"}:
                return 1
            if opcode == "C":
                lock_id, reply = 0, b"\0" * 6
            elif opcode == "X":
                lock_id, reply = 0, b"\0" * 6
            else:
                try:
                    lock_id = int(frame[1:].decode("ascii"))
                except (UnicodeError, ValueError):
                    return 1
                reply = b""
            if opcode == "C":
                _send(broker, reply)
            elif opcode == "A":
                if lock_id in leases:
                    _send(broker, error_reply(errno.EEXIST))
                    continue
                handle: TextIO | None = None
                try:
                    handle = open(lock_path, "a", encoding="utf-8")
                    fcntl.flock(  # type: ignore[attr-defined]
                        handle,
                        fcntl.LOCK_SH | fcntl.LOCK_NB,  # type: ignore[attr-defined]
                    )
                except OSError as exc:
                    if handle is not None:
                        handle.close()
                    _send(broker, error_reply(exc.errno or errno.EIO))
                    continue
                leases[lock_id] = handle
                # Deliver the shared-lock open file description itself. The
                # guard keeps the owning handle and releases it only on R.
                socket.send_fds(  # type: ignore[attr-defined]
                    broker,
                    [struct.pack("!iH", 0, 0)],
                    [handle.fileno()],
                )
            elif opcode == "R":
                handle = leases.pop(lock_id, None)
                if handle is not None:
                    try:
                        fcntl.flock(  # type: ignore[attr-defined]
                            handle,
                            fcntl.LOCK_UN,  # type: ignore[attr-defined]
                        )
                    finally:
                        handle.close()
                _send(broker, b"\0" * 6)
            elif opcode == "X":
                _send(broker, reply)
                return 0
            else:
                return 1
    finally:
        try:
            broker.close()
        except (OSError, ValueError):
            pass
        for handle in leases.values():
            try:
                fcntl.flock(  # type: ignore[attr-defined]
                    handle,
                    fcntl.LOCK_UN,  # type: ignore[attr-defined]
                )
            finally:
                handle.close()


def listen(parent_pid: int, address: str, expected_parent_start: str = "") -> int:
    """Accept one parent connection so SO_PEERCRED names this guard process."""
    _parent_death_signal()
    if os.getppid() != parent_pid:
        return 1
    if expected_parent_start:
        try:
            start = (
                Path(f"/proc/{parent_pid}/task/{parent_pid}/stat")
                .read_text(encoding="ascii")
                .rsplit(")", 1)[1]
                .split()[19]
            )
        except (OSError, UnicodeError, IndexError):
            return 1
        if start != expected_parent_start:
            return 1
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]
    try:
        listener.bind(address)
        listener.listen(1)
        connection, _ = listener.accept()
    finally:
        listener.close()
    connection.setblocking(True)
    return connection.detach()

"""Single-threaded request-lock owner started by cpa_admission_bootstrap."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import select
import socket
import struct
from pathlib import Path
from typing import IO


SOURCE_SHA256 = "__SOURCE_SHA256__"
# A private descriptor above Python's standard streams is intentionally
# inherited from the reviewed bootstrap. Descriptor numbers are meaningful
# only when their socket identities and process generation are also checked
# by the admission-side broker consumer.


def _set_parent_death_signal() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, 15, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PDEATHSIG")


def serve(
    parent_pid: int, fd: int, lock_path: str, expected_parent_start: str = ""
) -> int:
    _set_parent_death_signal()
    if fd < 3:
        return 1
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
    # Transfer the raw descriptor without letting Python close it while this
    # function's local socket object is still being initialized.
    sock = socket.socket(fileno=fd)
    os.set_inheritable(fd, False)
    leases: dict[int, list[IO[str] | None]] = {}
    try:
        while True:
            header = b""
            while len(header) < 16:
                try:
                    chunk = sock.recv(16 - len(header))
                except BlockingIOError:
                    select.select([sock], [], [], 1)
                    continue
                if not chunk:
                    return 1
                header += chunk
            opcode = chr(header[0])
            if opcode == "C":
                sock.sendall(b"\0" * 6)
                continue
            if opcode == "H":
                # Keepalive is fenced to the bootstrap's lock lifetime. It
                # neither creates a lease nor changes maintenance exclusivity.
                sock.sendall(b"\0" * 6)
                continue
            if opcode == "E":
                # This is admission's positive acknowledgement that the
                # inherited broker fd belongs to its live second exec.
                sock.sendall(b"R00000")
                continue
            try:
                lock_id = int(header[1:].decode("ascii"))
            except (UnicodeError, ValueError):
                return 1
            if opcode == "A":
                handle = open(lock_path, "a", encoding="utf-8")
                try:
                    fcntl.flock(  # type: ignore[attr-defined]
                        handle,
                        fcntl.LOCK_SH | fcntl.LOCK_NB,  # type: ignore[attr-defined]
                    )
                except OSError as exc:
                    handle.close()
                    sock.sendall(struct.pack("!iH", exc.errno or errno.EIO, 0))
                    continue
                leases[lock_id] = [handle]
                sock.sendall(struct.pack("!iH", 0, handle.fileno() & 0xFFFF))
            elif opcode == "R":
                lease = leases.pop(lock_id, None)
                released_handle: IO[str] | None = lease[0] if lease else None
                if released_handle is not None:
                    try:
                        fcntl.flock(  # type: ignore[attr-defined]
                            released_handle,
                            fcntl.LOCK_UN,  # type: ignore[attr-defined]
                        )
                    finally:
                        released_handle.close()
                sock.sendall(b"\0" * 6)
            elif opcode == "X":
                sock.sendall(b"\0" * 6)
                for lease in leases.values():
                    try:
                        held = lease[0]
                        if held is not None:
                            fcntl.flock(  # type: ignore[attr-defined]
                                held,
                                fcntl.LOCK_UN,  # type: ignore[attr-defined]
                            )
                            held.close()
                    except (OSError, ValueError):
                        pass
                leases.clear()
                return 0
            else:
                return 1
    finally:
        try:
            sock.close()
        except (OSError, ValueError):
            pass
        for lease in leases.values():
            try:
                held = lease[0]
                if held is not None:
                    fcntl.flock(  # type: ignore[attr-defined]
                        held,
                        fcntl.LOCK_UN,  # type: ignore[attr-defined]
                    )
                    held.close()
            except (OSError, ValueError):
                pass

from __future__ import annotations

import hashlib
import importlib.util
import os
import runpy
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

fcntl = pytest.importorskip(
    "fcntl", reason="request guard locking tests require POSIX fcntl"
)

ROOT = Path(__file__).parents[1]
GUARD_PATH = ROOT / "scripts/remote/cpa_request_guard.py"
BOOTSTRAP_PATH = ROOT / "scripts/remote/cpa_admission_bootstrap.py"


def _load_guard() -> Any:
    spec = importlib.util.spec_from_file_location("cpa_request_guard_test", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _recv_guard_reply(sock: socket.socket) -> tuple[bytes, int | None]:
    readable, _, _ = select.select([sock], [], [], 2)
    if not readable:
        raise TimeoutError("request guard reply timed out")
    reply, ancillary, _, _ = socket.recv_fds(  # type: ignore[attr-defined]
        sock,
        6,
        1,
        socket.MSG_CMSG_CLOEXEC,  # type: ignore[attr-defined]
    )
    received = [int(item) for item in ancillary]
    if len(received) > 1:
        for item in received:
            os.close(item)
        raise OSError("request guard sent too many descriptors")
    return reply, received[0] if received else None


def _exclusive_available(path: Path) -> bool:
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(handle, fcntl.LOCK_UN)
    return True


def _acquire(client: socket.socket, lock_id: int) -> int:
    client.sendall(f"A{lock_id:015d}".encode("ascii"))
    reply, received = _recv_guard_reply(client)
    error = struct.unpack("!i", reply[:4])[0]
    assert error == 0, (error, received)
    assert received is not None
    return received


def _release(client: socket.socket, lock_id: int, lease_fd: int | None) -> None:
    client.sendall(f"R{lock_id:015d}".encode("ascii"))
    reply, received = _recv_guard_reply(client)
    assert reply == b"\0" * 6
    assert received is None
    if lease_fd is not None:
        os.close(lease_fd)


def _bootstrap_fixture(
    tmp_path: Path, admission_text: str
) -> tuple[Path, Path, Path, Path]:
    admission = tmp_path / "admission.py"
    admission.write_text(admission_text, encoding="utf-8")
    guard_template = GUARD_PATH.read_text(encoding="utf-8")
    marker = 'SOURCE_SHA256 = "__SOURCE_SHA256__"'
    assert marker in guard_template
    guard = tmp_path / "guard.py"
    guard.write_text(
        guard_template.replace(
            marker,
            f'SOURCE_SHA256 = "{hashlib.sha256(admission.read_bytes()).hexdigest()}"',
        ),
        encoding="utf-8",
    )
    bootstrap_template = BOOTSTRAP_PATH.read_text(encoding="utf-8")
    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text(
        bootstrap_template.replace(
            "/run/cpa-admission/requests.lock", str(tmp_path / "requests.lock")
        )
        .replace("/usr/local/libexec/cpa_request_guard.py", str(guard))
        .replace("/run/cpa-admission/runtime-token", str(tmp_path / "runtime-token"))
        .replace(
            "/run/cpa-admission/runtime-receipt", str(tmp_path / "runtime-receipt")
        )
        .replace(
            "/run/cpa-admission/startup-generation",
            str(tmp_path / "startup-generation"),
        )
        .replace(
            'ADMISSION_PATH = "/opt/cliproxyapi/cpa-admission.py"',
            f"ADMISSION_PATH = {str(admission)!r}",
        ),
        encoding="utf-8",
    )
    return bootstrap, tmp_path / "process", tmp_path / "requests.lock", admission


def _bootstrap_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "VPS_SSH_LAUNCHER_ADMISSION_PROCESS_MARKER": str(tmp_path / "process"),
        "VPS_TEST_RUNTIME_TOKEN_PATH": str(tmp_path / "runtime-token"),
        "VPS_TEST_RUNTIME_RECEIPT_PATH": str(tmp_path / "runtime-receipt"),
        "VPS_TEST_STARTUP_IDENTITY_PATH": str(tmp_path / "startup-generation"),
    }


REALISTIC_ADMISSION = r"""
import json, os, signal, socket, time
import sys
from pathlib import Path


def main(parent, _start, bridge_fd, guard_fd, *_rest):
    parent = int(parent)
    bridge_fd = int(bridge_fd)
    guard_fd = int(guard_fd)
    guard = socket.socket(fileno=guard_fd)
    guard.settimeout(2)
    guard.sendall(b"C" + b"0" * 15)
    assert guard.recv(6) == b"\0" * 6
    bridge = socket.socket(fileno=bridge_fd)
    assert bridge.recv(1) == b"f"
    binding = json.loads(Path(os.environ["VPS_TEST_RUNTIME_TOKEN_PATH"]).read_text())
    identity = Path(os.environ["VPS_TEST_STARTUP_IDENTITY_PATH"]).read_text().strip()
    runtime = {
        "token": binding["token"],
        "identity": identity,
        "parent_pid": parent,
        "admission_pid": os.getpid(),
        "guard_pid": binding["guard_pid"],
        "guard_start": binding["guard_start"],
        "lease_probe": True,
    }
    Path(os.environ["VPS_TEST_RUNTIME_RECEIPT_PATH"]).write_text(json.dumps(runtime))
    signal.signal(signal.SIGTERM, lambda *_: os._exit(143))
    time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[2:6]))
"""


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SCM_RIGHTS")
def test_guard_uses_independent_leases_and_releases_maintenance() -> None:
    guard = _load_guard()
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "requests.lock"
        server, client = socket.socketpair()
        thread = threading.Thread(
            target=guard.serve,
            args=(os.getppid(), server.detach(), str(lock)),
        )
        thread.start()
        leases: list[int] = []
        try:
            client.sendall(b"C" + b"0" * 15)
            assert _recv_guard_reply(client)[0] == b"\0" * 6
            for identifier in (1, 2):
                leases.append(_acquire(client, identifier))
            assert len(set(leases)) == 2
            assert not _exclusive_available(lock)
            _release(client, 1, leases[0])
            assert not _exclusive_available(lock)
            _release(client, 2, leases[1])
            assert _exclusive_available(lock)
        finally:
            client.close()
            for lease_fd in leases:
                try:
                    os.close(lease_fd)
                except OSError:
                    pass
            thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SCM_RIGHTS")
def test_guard_refusal_never_unlocks_maintenance() -> None:
    guard = _load_guard()
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "requests.lock"
        server, client = socket.socketpair()
        with lock.open("a") as exclusive:
            fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
            thread = threading.Thread(
                target=guard.serve,
                args=(os.getppid(), server.detach(), str(lock)),
            )
            thread.start()
            try:
                client.sendall(b"C" + b"0" * 15)
                assert _recv_guard_reply(client)[0] == b"\0" * 6
                client.sendall(b"A" + b"1".rjust(15, b"0"))
                reply, received = _recv_guard_reply(client)
                assert struct.unpack("!i", reply[:4])[0] in (11, 13)
                assert received is None
                fcntl.flock(exclusive, fcntl.LOCK_UN)
                lease_fd = _acquire(client, 2)
                assert not _exclusive_available(lock)
                _release(client, 2, lease_fd)
                assert _exclusive_available(lock)
            finally:
                client.close()
                thread.join(timeout=2)


@pytest.mark.skipif(os.name == "nt", reason="Linux so_peercred")
def test_broker_identity_includes_linux_process_start(tmp_path: Path) -> None:
    admission_module = runpy.run_path(str(ROOT / "scripts/remote/cpa-admission.py"))
    broker, guard = socket.socketpair()
    try:
        pid, start = admission_module["_guard_peer_identity"](broker)
        assert pid == os.getpid()
        assert start == admission_module["_linux_process_start"](os.getpid())
    finally:
        broker.close()
        guard.close()


@pytest.mark.skipif(os.name == "nt", reason="Linux bootstrap and process lineage")
def test_bootstrap_publishes_one_guard_generation(tmp_path: Path) -> None:
    bootstrap, marker, lock, _admission = _bootstrap_fixture(
        tmp_path, REALISTIC_ADMISSION
    )
    process = subprocess.Popen(
        [sys.executable, str(bootstrap)],
        env=_bootstrap_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 8
        fields: list[str] = []
        while time.monotonic() < deadline:
            try:
                fields = marker.read_text(encoding="ascii").split()
            except OSError:
                fields = []
            if len(fields) == 8 or process.poll() is not None:
                break
            time.sleep(0.02)
        if len(fields) != 8:
            if process.poll() is None:
                os.killpg(process.pid, 9)  # type: ignore[attr-defined]
            raise AssertionError(process.communicate(timeout=3))
        guard_pid = int(fields[3])
        admission_pid = int(fields[6])
        assert fields[2] == "9"
        assert len(list(Path(f"/proc/{guard_pid}/task").iterdir())) == 1
        children = (
            Path(f"/proc/{fields[4]}/task/{fields[4]}/children").read_text().split()
        )
        assert children == [fields[3], fields[6]]
        assert _exclusive_available(lock)
        os.killpg(process.pid, 15)  # type: ignore[attr-defined]
        process.wait(timeout=4)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and (
            Path(f"/proc/{guard_pid}").exists()
            or Path(f"/proc/{admission_pid}").exists()
        ):
            time.sleep(0.01)
        assert not Path(f"/proc/{guard_pid}").exists()
        assert not Path(f"/proc/{admission_pid}").exists()
        assert _exclusive_available(lock)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, 9)  # type: ignore[attr-defined]
            process.wait(timeout=3)


BAD_RECEIPT_CASES = [
    (
        "raise RuntimeError('fixture failure')\n",
        "admission exited before readiness",
    ),
    (
        "import json, os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "def main(*_args):\n"
        "    Path(os.environ['VPS_TEST_RUNTIME_RECEIPT_PATH']).write_text("
        "json.dumps({'token': 'wrong'}))\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main(*sys.argv[2:6]))\n",
        "admission runtime receipt invalid",
    ),
]


@pytest.mark.skipif(os.name == "nt", reason="Linux bootstrap receipt validation")
@pytest.mark.parametrize("admission_text,expected_error", BAD_RECEIPT_CASES)
def test_bootstrap_does_not_publish_bad_receipt(
    tmp_path: Path, admission_text: str, expected_error: str
) -> None:
    bootstrap, marker, lock, _admission = _bootstrap_fixture(tmp_path, admission_text)
    result = subprocess.run(
        [sys.executable, str(bootstrap)],
        env=_bootstrap_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "startup_failed" in result.stderr
    assert expected_error in result.stderr
    assert not marker.exists()
    assert _exclusive_available(lock)

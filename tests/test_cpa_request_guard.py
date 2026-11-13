from __future__ import annotations

import hashlib
import importlib.util
import os
import runpy
import select
import signal
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

# The guard under test relies on POSIX file locking; its socket-handoff tests
# cannot run on Windows where fcntl does not exist. Skip the whole module.
fcntl = pytest.importorskip("fcntl", reason="cpa_request_guard locking tests require POSIX fcntl")


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


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        readable, _, _ = select.select([sock], [], [], 2)
        if not readable:
            raise TimeoutError("request-guard broker response timed out")
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise TimeoutError("request-guard broker closed early")
        data += chunk
    return data


def _is_exclusive_available(path: Path) -> bool:
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(handle, fcntl.LOCK_UN)
    return True


def _fixture_bootstrap(tmp_path: Path) -> tuple[Path, Path, Path]:
    marker = tmp_path / "process"
    lock = tmp_path / "requests.lock"
    bootstrap_path = tmp_path / "bootstrap.py"
    guard = _fixture_guard(tmp_path, "0" * 64)
    bootstrap_path.write_text(
        BOOTSTRAP_PATH.read_text(encoding="utf-8")
        .replace("/run/cpa-admission/requests.lock", str(lock))
        .replace("/usr/local/libexec/cpa_request_guard.py", str(guard))
        .replace('"/run/cpa-admission/process"', repr(str(marker))),
        encoding="utf-8",
    )
    return marker, lock, bootstrap_path


def _fixture_guard(tmp_path: Path, admission_sha256: str) -> Path:
    guard = tmp_path / "guard.py"
    template = GUARD_PATH.read_text(encoding="utf-8")
    marker = 'SOURCE_SHA256 = "__SOURCE_SHA256__"'
    assert marker in template
    guard.write_text(
        template.replace(marker, f'SOURCE_SHA256 = "{admission_sha256}"'),
        encoding="utf-8",
    )
    return guard


def _fixture_admission(tmp_path: Path) -> Path:
    admission = tmp_path / "cpa-admission-fence.py"
    admission.write_text(
        "import os, signal, time\n"
        "def main(_parent_pid, _parent_start, _fd, broker_fd):\n"
        "    assert broker_fd == 9\n"
        "    os.write(9, b'E' + b'0' * 15)\n"
        "    assert os.read(9, 6) == b'R00000'\n"
        "    os.write(9, b'E' + b'0' * 15)\n"
        "    assert os.read(9, 6) == b'R00000'\n"
        "    signal.signal(signal.SIGTERM, lambda *_: os._exit(143))\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )
    return admission


def _bind_fixture_admission(tmp_path: Path, admission: Path) -> Path:
    _fixture_guard(tmp_path, hashlib.sha256(admission.read_bytes()).hexdigest())
    return admission


@pytest.mark.skipif(os.name == "nt", reason="guard broker is a POSIX control plane")
def test_guard_leases_do_not_share_one_request_lock() -> None:
    module = _load_guard()
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = Path(tmp) / "requests.lock"
        server, client = socket.socketpair()
        thread = threading.Thread(
            target=module.serve,
            args=(os.getppid(), server.detach(), str(lock_path)),
        )
        thread.start()
        try:
            client.sendall(b"C" + b"0" * 15)
            assert _recv_exact(client, 6) == b"\0" * 6
            client.sendall(b"A" + b"1".rjust(15, b"0"))
            assert _recv_exact(client, 6)[:4] == b"\0" * 4
            client.sendall(b"A" + b"2".rjust(15, b"0"))
            assert _recv_exact(client, 6)[:4] == b"\0" * 4
            assert not _is_exclusive_available(lock_path)
            client.sendall(b"R" + b"1".rjust(15, b"0"))
            assert _recv_exact(client, 6) == b"\0" * 6
            assert not _is_exclusive_available(lock_path)
            client.sendall(b"R" + b"2".rjust(15, b"0"))
            assert _recv_exact(client, 6) == b"\0" * 6
            assert _is_exclusive_available(lock_path)
            client.sendall(b"X" + b"0" * 15)
            assert _recv_exact(client, 6) == b"\0" * 6
            thread.join(timeout=2)
            assert not thread.is_alive()
        finally:
            client.close()
            thread.join(timeout=2)


@pytest.mark.skipif(os.name == "nt", reason="guard broker is a POSIX control plane")
def test_guard_refusal_keeps_exclusive_maintenance_and_frame_aligned() -> None:
    module = _load_guard()
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = Path(tmp) / "requests.lock"
        server, client = socket.socketpair()
        thread = threading.Thread(
            target=module.serve,
            args=(os.getppid(), server.detach(), str(lock_path)),
        )
        with lock_path.open("a") as exclusive:
            fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
            thread.start()
            try:
                client.sendall(b"C" + b"0" * 15)
                assert _recv_exact(client, 6) == b"\0" * 6
                client.sendall(b"A" + b"1".rjust(15, b"0"))
                assert struct.unpack("!i", _recv_exact(client, 6)[:4])[0] == 11
                fcntl.flock(exclusive, fcntl.LOCK_UN)
                client.sendall(b"A" + b"2".rjust(15, b"0"))
                assert _recv_exact(client, 6)[:4] == b"\0" * 4
            finally:
                client.close()
                thread.join(timeout=2)


@pytest.mark.skipif(os.name == "nt", reason="broker is a Linux process fence")
def test_single_threaded_guard_exits_after_parent_exit(tmp_path: Path) -> None:
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import os, subprocess, sys, time\n"
        "import importlib.util, pathlib\n"
        "spec = importlib.util.spec_from_file_location('module', sys.argv[2])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "def stat(pid):\n"
        "    return pathlib.Path('/proc/' + str(pid) + '/stat').read_text(encoding='ascii').rsplit(')', 1)[1].split()\n"
        "def start(pid):\n"
        "    return stat(pid)[19]\n"
        "current_pid, current_start = os.getpid(), start(os.getpid())\n"
        "server, client = __import__('socket').socketpair()\n"
        "source = sys.argv[1]\n"
        "guard = subprocess.Popen([sys.executable, '-c', "
        '\'import importlib.util,sys; s=importlib.util.spec_from_file_location('
        '"guard", sys.argv[1]); m=importlib.util.module_from_spec(s); '
        's.loader.exec_module(m); raise SystemExit(m.serve(int(sys.argv[2]), '
        "int(sys.argv[3]), sys.argv[4]))', source, str(os.getpid()), "
        "str(client.fileno()), sys.argv[2]], pass_fds=(client.fileno(),))\n"
        "client.close()\n"
        "child = subprocess.Popen([sys.executable, '-I', '-c', "
        '"import runpy,sys; n=runpy.run_path(sys.argv[1]); raise SystemExit(n[\'main\'](int(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])))", '
        "sys.argv[2], str(current_pid), current_start, "
        "'{\"admission_path\":\"' + sys.argv[3] + '\",\"source_sha256\":\"'\n"
        "+ __import__('hashlib').sha256(pathlib.Path(sys.argv[3]).read_bytes()).hexdigest()\n"
        "+ '\",\"marker\":\"' + sys.argv[4] + '\"}', "
        "str(server.fileno()), str(server.fileno())], pass_fds=(server.fileno(),))\n"
        "server.close()\n"
        "print(guard.pid, flush=True)\n"
        "print(child.pid, flush=True)\n"
        "print(child.wait(), flush=True)\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(launcher),
            str(GUARD_PATH),
            str(BOOTSTRAP_PATH),
            str(tmp_path / "admission.py"),
            str(tmp_path / "process"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    guard_pid = int(process.stdout.readline().strip())
    admission_pid = int(process.stdout.readline().strip())
    assert len(list(Path(f"/proc/{guard_pid}/task").iterdir())) == 1
    assert Path(f"/proc/{admission_pid}/stat").exists()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and Path(f"/proc/{guard_pid}").exists():
        time.sleep(0.01)
    assert not Path(f"/proc/{guard_pid}").exists()
    stdout, stderr = process.communicate(timeout=2)
    assert stdout.strip() == "1", (stdout, stderr)


@pytest.mark.skipif(os.name == "nt", reason="Linux process credentials")
def test_broker_reads_ucred_before_guard_start_time(tmp_path: Path) -> None:
    module = _load_guard()
    admission = runpy.run_path(str(ROOT / "scripts/remote/cpa-admission.py"))
    parent, child = socket.socketpair()
    started = threading.Event()

    def serve_guard() -> None:
        inherited = child.detach()
        started.set()
        module.serve(os.getppid(), inherited, str(tmp_path / "requests.lock"))

    guard = threading.Thread(target=serve_guard, daemon=True)
    try:
        guard.start()
        assert started.wait(2)
        guard_start = (
            Path(f"/proc/{os.getpid()}/stat")
            .read_text(encoding="ascii")
            .rsplit(")", 1)[1]
            .split()[19]
        )
        parent.sendall(b"C" + b"0" * 15)
        assert _recv_exact(parent, 6) == b"\0" * 6
        peer_pid, peer_start = admission["_guard_peer_identity"](parent)
        assert peer_pid == os.getpid()
        assert peer_start == guard_start
        assert admission["_linux_process_start"](peer_pid) == guard_start
        parent.sendall(b"X" + b"0" * 15)
        assert _recv_exact(parent, 6) == b"\0" * 6
    finally:
        parent.close()
        guard.join(timeout=2)


@pytest.mark.skipif(os.name == "nt", reason="bootstrap is a Linux launcher")
def test_bootstrap_hands_one_fd_to_second_exec_and_releases_lock(
    tmp_path: Path,
) -> None:
    marker, lock, bootstrap_path = _fixture_bootstrap(tmp_path)
    admission_path = _fixture_admission(tmp_path)
    bootstrap_path.write_text(
        bootstrap_path.read_text(encoding="utf-8").replace(
            "ADMISSION_PATH = \"/opt/cliproxyapi/cpa-admission.py\"",
            f"ADMISSION_PATH = {str(admission_path)!r}",
        ),
        encoding="utf-8",
    )
    _bind_fixture_admission(tmp_path, admission_path)
    extra, extra_peer = socket.socketpair()
    extra_pass_fd = extra.fileno()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os, pathlib, sys, time\n"
                "Popen = __import__('subprocess').Popen\n"
                "def checked(*args, **kwargs):\n"
                "    if len(args) > 0 and isinstance(args[0], list) and "
                f"any(str(item).endswith('{admission_path.name}') for item in args[0]):\n"
                "        assert kwargs['pass_fds'] == (9,), kwargs['pass_fds']\n"
                "    return Popen(*args, **kwargs)\n"
                "__import__('subprocess').Popen = checked\n"
                "source = pathlib.Path(sys.argv[1])\n"
                "module = __import__('types').ModuleType('bootstrap')\n"
                "module.__file__ = str(source)\n"
                "code = compile(source.read_text(), str(source), 'exec')\n"
                "_ = exec(code, module.__dict__)\n"
                "result = module.main()\n"
                "children = pathlib.Path('/proc/' + str(os.getpid()) + '/task/' + str(os.getpid()) + '/children').read_text().split()\n"
                "for pid in children:\n"
                "    pid = int(pid); stat = pathlib.Path('/proc/' + str(pid) + '/stat').read_text()\n"
                "    print(pid, stat, module._linux_start(pid), sep='|', flush=True)\n"
                "print(result, flush=True)\n"
                "time.sleep(60)\n"
            ),
            str(bootstrap_path),
            str(extra_pass_fd),
        ],
        env={
            **os.environ,
            "VPS_SSH_LAUNCHER_ADMISSION_PROCESS_MARKER": str(marker),
        },
        pass_fds=(extra_pass_fd,),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    extra.close()
    extra_peer.close()
    try:
        deadline = time.monotonic() + 8
        fields: list[str] = []
        while time.monotonic() < deadline and process.poll() is None:
            if process.poll() is not None:
                break
            if not lock.exists():
                time.sleep(0.02)
                continue
            try:
                fields = marker.read_text(encoding="ascii").split()
            except OSError:
                fields = []
            if len(fields) == 7:
                break
            if process.poll() is not None:
                break
            time.sleep(0.02)
        if len(fields) != 7:
            if process.poll() is not None:
                raise AssertionError((fields, process.communicate(timeout=3)))
            raise AssertionError(fields)
        assert fields[2] == "9"
        assert len(list(Path(f"/proc/{fields[3]}/task").iterdir())) == 1
        assert (
            Path(f"/proc/{fields[4]}/task/{fields[4]}/children")
            .read_text()
            .split()[0]
            == fields[3]
        )
        assert _is_exclusive_available(lock)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=3)
        assert "pass_fds" not in stderr, (stdout, stderr)


@pytest.mark.skipif(os.name == "nt", reason="bootstrap is a Linux launcher")
def test_bootstrap_does_not_publish_an_exited_admission(tmp_path: Path) -> None:
    marker, lock, bootstrap_path = _fixture_bootstrap(tmp_path)
    admission_path = _fixture_admission(tmp_path)
    bootstrap_path.write_text(
        bootstrap_path.read_text(encoding="utf-8").replace(
            'ADMISSION_PATH = "/opt/cliproxyapi/cpa-admission.py"',
            f"ADMISSION_PATH = {str(admission_path)!r}",
        ),
        encoding="utf-8",
    )
    extra, peer = socket.socketpair()
    try:
        completed = subprocess.run(
            [sys.executable, str(bootstrap_path)],
            env={
                **os.environ,
                "VPS_SSH_LAUNCHER_ADMISSION_PROCESS_MARKER": str(marker),
            },
            pass_fds=(extra.fileno(),),
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        extra.close()
        peer.close()
        raise
    extra.close()
    peer.close()
    assert completed.returncode in (0, 1), (completed.stdout, completed.stderr)
    assert completed.returncode == 1, (completed.stdout, completed.stderr)
    assert "FileNotFoundError" not in completed.stderr, completed.stderr
    assert not marker.exists()
    assert _is_exclusive_available(lock)


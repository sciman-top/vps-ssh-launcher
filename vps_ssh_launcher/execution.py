"""Remote command submission, concurrent stream draining and bounded capture."""

from __future__ import annotations

import codecs
import logging
import sys
import threading
import time
from contextlib import suppress
from typing import Any, Callable

from .contracts import (
    CMD_TIMEOUT,
    CHANNEL_POLL_INTERVAL,
    CHANNEL_READ_BURST,
    EXIT_OK,
    MAX_REMOTE_EXIT_CODE,
    RemoteCommandClient,
)

logger = logging.getLogger("ssh_tool")


def _redact_command_for_log(command: str) -> str:
    return f"<redacted remote command; length={len(command)}>"


def _coerce_timeout(value: Any, *, context: str) -> int:
    """Normalize command timeout seconds; 0 disables command timeout."""
    if isinstance(value, bool):
        raise ValueError(f"{context}: timeout must be an integer >= 0, got {value!r}.")
    if isinstance(value, int):
        timeout = value
    elif isinstance(value, str) and value.strip().isdigit():
        timeout = int(value.strip())
    else:
        raise ValueError(f"{context}: timeout must be an integer >= 0, got {value!r}.")
    if timeout < 0:
        raise ValueError(
            f"{context}: timeout must be an integer >= 0, got {timeout!r}."
        )
    return timeout


def _command_timeout_arg(args: Any) -> int:
    raw_timeout = getattr(args, "command_timeout", None)
    if raw_timeout is None:
        return CMD_TIMEOUT
    return _coerce_timeout(raw_timeout, context="Command timeout")


def _command_hard_timeout_arg(args: Any) -> int:
    raw_timeout = getattr(args, "command_hard_timeout", None)
    if raw_timeout is None:
        return 0
    return _coerce_timeout(raw_timeout, context="Command hard timeout")


class _DecodedOutput:
    """Decode one remote stream and optionally emit/capture it with a hard cap."""

    def __init__(
        self,
        stream_name: str,
        *,
        writer: Callable[[str], Any] | None,
        capture_limit: int | None,
    ) -> None:
        self._stream_name = stream_name
        self._writer = writer
        self._capture_limit = capture_limit
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._chunks: list[str] = []
        self._captured_chars = 0
        self.truncated = False

    def feed(self, data: bytes) -> None:
        self._emit(self._decoder.decode(data))

    def finish(self) -> None:
        self._emit(self._decoder.decode(b"", final=True))

    def _emit(self, text: str) -> None:
        if not text:
            return
        if self._writer is not None:
            self._writer(text)
        if self._capture_limit == 0:
            return
        if self._capture_limit is None:
            self._chunks.append(text)
            self._captured_chars += len(text)
            return

        remaining = self._capture_limit - self._captured_chars
        if remaining > 0:
            captured = text[:remaining]
            self._chunks.append(captured)
            self._captured_chars += len(captured)
        if len(text) > max(remaining, 0):
            self.truncated = True

    def render(self) -> str:
        text = "".join(self._chunks)
        if not self.truncated or self._capture_limit is None:
            return text
        marker = (
            f"\n[{self._stream_name} truncated at {self._capture_limit} chars; "
            "showing prefix only]\n"
        )
        prefix_limit = max(self._capture_limit - len(marker), 0)
        return text[:prefix_limit] + marker


def _read_available_channel_data(
    channel: Any,
    stdout_output: _DecodedOutput,
    stderr_output: _DecodedOutput,
) -> bool:
    progressed = False
    for _ in range(CHANNEL_READ_BURST):
        if not channel.recv_ready():
            break
        data = channel.recv(32768)
        if not data:
            break
        stdout_output.feed(data)
        progressed = True

    for _ in range(CHANNEL_READ_BURST):
        if not channel.recv_stderr_ready():
            break
        data = channel.recv_stderr(32768)
        if not data:
            break
        stderr_output.feed(data)
        progressed = True

    return progressed


def _drain_channel(
    channel: Any,
    *,
    command_timeout: int = CMD_TIMEOUT,
    command_hard_timeout: int = 0,
    stdout_writer: Callable[[str], Any] | None = None,
    stderr_writer: Callable[[str], Any] | None = None,
    capture_limit: int | None = None,
    hard_deadline: float | None = None,
) -> tuple[str, str, int, bool, bool]:
    """Read stdout and stderr without deadlocking the SSH channel.

    Uses a two-tier timeout:
    - Idle timeout: resets on every data received.
    - Hard total timeout: optional absolute upper bound regardless of activity.
    """
    stdout_output = _DecodedOutput(
        "stdout", writer=stdout_writer, capture_limit=capture_limit
    )
    stderr_output = _DecodedOutput(
        "stderr", writer=stderr_writer, capture_limit=capture_limit
    )
    idle_deadline: float | None = None
    if command_timeout > 0:
        now = time.monotonic()
        idle_deadline = now + command_timeout
    if command_hard_timeout > 0 and hard_deadline is None:
        hard_deadline = time.monotonic() + command_hard_timeout

    while True:
        if hard_deadline is not None and time.monotonic() >= hard_deadline:
            raise TimeoutError(
                f"Remote command exceeded hard timeout of {command_hard_timeout}s."
            )
        progressed = _read_available_channel_data(
            channel,
            stdout_output,
            stderr_output,
        )
        if progressed and idle_deadline is not None:
            idle_deadline = time.monotonic() + command_timeout

        if (
            channel.exit_status_ready()
            and not channel.recv_ready()
            and not channel.recv_stderr_ready()
        ):
            break

        now = time.monotonic()
        if hard_deadline is not None and now > hard_deadline:
            raise TimeoutError(
                f"Remote command exceeded hard timeout of {command_hard_timeout}s."
            )
        if idle_deadline is not None and now > idle_deadline:
            raise TimeoutError(
                f"Remote command timed out after {command_timeout}s idle."
            )

        if not progressed:
            time.sleep(CHANNEL_POLL_INTERVAL)

    stdout_output.finish()
    stderr_output.finish()
    return (
        stdout_output.render(),
        stderr_output.render(),
        channel.recv_exit_status(),
        stdout_output.truncated,
        stderr_output.truncated,
    )


def _submit_remote_command(
    client: RemoteCommandClient,
    command: str,
    *,
    timeout: float | None,
) -> tuple[Any, Any, Any]:
    # Paramiko's exec acknowledgement waits without a timeout. Closing the
    # client releases both that wait and a pending channel-open request.
    if timeout is None:
        return client.exec_command(command)  # nosec B601
    expired = threading.Event()
    lock = threading.Lock()
    active = True

    def cancel() -> None:
        with lock:
            if not active:
                return
            expired.set()
        close = getattr(client, "close", None)
        if callable(close):
            with suppress(Exception):
                close()

    timer = threading.Timer(max(0, timeout), cancel)
    timer.daemon = True
    timer.start()
    try:
        try:
            streams = client.exec_command(command)  # nosec B601
        except Exception as exc:
            if expired.is_set():
                raise TimeoutError("Remote command submission timed out.") from exc
            raise
    finally:
        timer.cancel()
        with lock:
            active = False
            timed_out = expired.is_set()
    if timed_out:
        for stream in streams:
            with suppress(Exception):
                stream.close()
        raise TimeoutError("Remote command submission timed out.")
    return streams


def _execute_remote(
    client: RemoteCommandClient,
    command: str,
    *,
    command_timeout: int = CMD_TIMEOUT,
    command_hard_timeout: int = 0,
    stdout_writer: Callable[[str], Any] | None = None,
    stderr_writer: Callable[[str], Any] | None = None,
    capture_limit: int | None = None,
) -> tuple[int, str, str, bool, bool]:
    """Execute one command through the shared streaming/capture seam."""
    command_timeout = _coerce_timeout(command_timeout, context="Command timeout")
    command_hard_timeout = _coerce_timeout(
        command_hard_timeout,
        context="Command hard timeout",
    )
    logger.debug("Running: %s", _redact_command_for_log(command))
    # This tool intentionally executes the explicit command supplied by the user.
    started = time.monotonic()
    hard_deadline = started + command_hard_timeout if command_hard_timeout else None
    submission_limits = [v for v in (command_timeout, command_hard_timeout) if v > 0]
    stdin, stdout, _stderr = _submit_remote_command(
        client, command, timeout=min(submission_limits) if submission_limits else None
    )
    channel = stdout.channel
    try:
        stdin.close()  # Prevent hangs on commands that read stdin
        if command_timeout > 0:
            channel.settimeout(command_timeout)

        # Drain both streams incrementally to avoid filling one buffer while
        # waiting on the other. This keeps stderr-heavy commands safe.
        out, err, code, stdout_truncated, stderr_truncated = _drain_channel(
            channel,
            command_timeout=command_timeout,
            command_hard_timeout=command_hard_timeout,
            stdout_writer=stdout_writer,
            stderr_writer=stderr_writer,
            capture_limit=capture_limit,
            hard_deadline=hard_deadline,
        )
        if not EXIT_OK <= code <= MAX_REMOTE_EXIT_CODE:
            raise RuntimeError(f"Remote command returned invalid exit status: {code}.")
        return code, out, err, stdout_truncated, stderr_truncated
    finally:
        try:
            stdout.close()
        finally:
            try:
                _stderr.close()
            finally:
                # Closing the SSH channel is the portable cancellation boundary
                # after an exec request. The remote process may still outlive
                # it, so timed-out commands must be idempotent and are never
                # retried by this client.
                with suppress(Exception):
                    channel.close()


def exec_remote(
    client: RemoteCommandClient,
    command: str,
    *,
    command_timeout: int = CMD_TIMEOUT,
    command_hard_timeout: int = 0,
) -> tuple[int, str, str]:
    """Execute command and return its complete stdout/stderr for programmatic use."""
    code, out, err, _stdout_truncated, _stderr_truncated = _execute_remote(
        client,
        command,
        command_timeout=command_timeout,
        command_hard_timeout=command_hard_timeout,
    )
    return code, out, err


def _write_stream(stream: Any, text: str) -> None:
    stream.write(text)
    stream.flush()


def run_command(
    client: RemoteCommandClient,
    command: str,
    *,
    command_timeout: int = CMD_TIMEOUT,
    command_hard_timeout: int = 0,
) -> int:
    """Execute command, stream output. Returns exit code."""
    code, _out, _err, _stdout_truncated, _stderr_truncated = _execute_remote(
        client,
        command,
        command_timeout=command_timeout,
        command_hard_timeout=command_hard_timeout,
        stdout_writer=lambda text: _write_stream(sys.stdout, text),
        stderr_writer=lambda text: _write_stream(sys.stderr, text),
        capture_limit=0,
    )
    logger.debug("Exit code: %d", code)
    return code

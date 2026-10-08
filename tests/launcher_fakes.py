"""Shared in-memory doubles and patch helpers for the launcher unit tests."""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any

_MISSING = object()


class RecordedCall:
    def __init__(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.args = args
        self.kwargs = kwargs


class StubCallable:
    def __init__(
        self,
        *,
        return_value: Any = None,
        side_effect: Any = None,
    ) -> None:
        self.return_value = return_value
        self.side_effect = side_effect
        self.calls: list[RecordedCall] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(RecordedCall(args, kwargs))
        if self.side_effect is not None:
            if isinstance(self.side_effect, BaseException):
                raise self.side_effect
            return self.side_effect(*args, **kwargs)
        return self.return_value

    @property
    def call_args(self) -> RecordedCall:
        if not self.calls:
            raise AssertionError("stub was not called")
        return self.calls[-1]

    def assert_not_called(self) -> None:
        if self.calls:
            raise AssertionError(f"expected no calls, got {len(self.calls)}")

    def assert_called_once(self) -> None:
        if len(self.calls) != 1:
            raise AssertionError(f"expected one call, got {len(self.calls)}")

    def assert_called_once_with(self, *args: Any, **kwargs: Any) -> None:
        self.assert_called_once()
        call = self.call_args
        if call.args != args or call.kwargs != kwargs:
            raise AssertionError(
                f"expected call args={args!r} kwargs={kwargs!r}, "
                f"got args={call.args!r} kwargs={call.kwargs!r}"
            )


@contextmanager
def patch_attr(
    target: Any,
    name: str,
    value: Any = _MISSING,
    *,
    return_value: Any = _MISSING,
    side_effect: Any = _MISSING,
) -> Iterator[Any]:
    original = getattr(target, name)
    replacement = value
    if replacement is _MISSING:
        replacement = StubCallable(
            return_value=None if return_value is _MISSING else return_value,
            side_effect=None if side_effect is _MISSING else side_effect,
        )
    setattr(target, name, replacement)
    try:
        yield replacement
    finally:
        setattr(target, name, original)


@contextmanager
def patch_env(
    mapping: MutableMapping[str, str],
    values: dict[str, str],
    *,
    clear: bool = False,
) -> Iterator[None]:
    original = dict(mapping)
    if clear:
        mapping.clear()
    mapping.update(values)
    try:
        yield
    finally:
        mapping.clear()
        mapping.update(original)


class FakeAuthenticationException(Exception):
    pass


class FakeRejectPolicy:
    pass


class FakeAutoAddPolicy:
    pass


class FakeSSHClient:
    def __init__(self) -> None:
        self.closed = False
        self.loaded_system_host_keys = False
        self.loaded_host_key_files: list[str] = []
        self.missing_host_key_policy: Any = None

    def load_system_host_keys(self) -> None:
        self.loaded_system_host_keys = True

    def load_host_keys(self, filename: str) -> None:
        self.loaded_host_key_files.append(filename)

    def set_missing_host_key_policy(self, policy: Any) -> None:
        self.missing_host_key_policy = policy

    def connect(self, **_kwargs: Any) -> None:
        pass

    def get_transport(self) -> Any:
        return None

    def close(self) -> None:
        self.closed = True


class FakeParamikoModule:
    SSHClient: type[FakeSSHClient] = FakeSSHClient
    RejectPolicy: type[FakeRejectPolicy] = FakeRejectPolicy
    AutoAddPolicy: type[FakeAutoAddPolicy] = FakeAutoAddPolicy
    AuthenticationException: type[FakeAuthenticationException] = (
        FakeAuthenticationException
    )


class FakeStdin:
    def close(self) -> None:
        pass


class FakeChannel:
    def __init__(
        self,
        stdout_chunks: list[bytes] | None = None,
        stderr_chunks: list[bytes] | None = None,
        exit_status: int = 0,
    ) -> None:
        self._stdout_chunks = list(stdout_chunks or [])
        self._stderr_chunks = list(stderr_chunks or [])
        self._exit_status = exit_status
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def recv_ready(self) -> bool:
        return bool(self._stdout_chunks)

    def recv(self, _size: int) -> bytes:
        return self._stdout_chunks.pop(0)

    def recv_stderr_ready(self) -> bool:
        return bool(self._stderr_chunks)

    def recv_stderr(self, _size: int) -> bytes:
        return self._stderr_chunks.pop(0)

    def exit_status_ready(self) -> bool:
        return not self._stdout_chunks and not self._stderr_chunks

    def recv_exit_status(self) -> int:
        return self._exit_status

    def close(self) -> None:
        self.closed = True


class BlockingChannel(FakeChannel):
    def recv_ready(self) -> bool:
        return False

    def recv_stderr_ready(self) -> bool:
        return False

    def exit_status_ready(self) -> bool:
        return False


class FakeFile:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel
        self.closed = False

    def exec_command(self, command: str) -> tuple[FakeStdin, FakeFile, FakeFile]:
        return FakeStdin(), FakeFile(self._channel), FakeFile(self._channel)

    def close(self) -> None:
        self.closed = True

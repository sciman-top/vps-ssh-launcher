"""Typing-only access to Windows ctypes APIs that Linux stubs omit.

The Windows ACL implementation stays importable on Linux for cross-platform
configuration tooling. Native loading happens only when the DACL path runs.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, cast


class _WinDLL(Protocol):
    def __getattr__(self, name: str) -> Any: ...


class _CtypesWindows(Protocol):
    WinDLL: Callable[..., Any]
    WinError: Callable[[int], OSError]
    get_last_error: Callable[[], int]


class WinDLL:
    """Compatibility seam; native loading occurs only on an actual call."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        import ctypes

        return cast(_CtypesWindows, ctypes).WinDLL(*args, **kwargs)


def win_dll(name: str) -> _WinDLL:
    """Load a Windows DLL through the runtime's platform implementation."""
    import ctypes

    windows = cast(_CtypesWindows, ctypes)
    return cast(_WinDLL, windows.WinDLL(name, use_last_error=True))


def win_error(code: int) -> OSError:
    """Raise the Win32 error representation while keeping this code typed."""
    import ctypes

    windows = cast(_CtypesWindows, ctypes)
    raise windows.WinError(code)


def last_error() -> int:
    """Read Win32's thread-local error without typing a Linux-only ctypes."""
    import ctypes

    windows = cast(_CtypesWindows, ctypes)
    return int(windows.get_last_error())

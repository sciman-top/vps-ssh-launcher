"""Windows DACL inspection for user-private credential files.

Kept separate from configuration precedence: this module answers only whether
another Windows principal has read access to a file, and has no knowledge of
profiles, credentials, SSH, or CLI arguments.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any, Callable

from .windows_ctypes import last_error, win_dll, win_error


def _wintypes() -> Any:
    """Import the Windows-only ctypes names only when this path is called."""
    from ctypes import wintypes

    return wintypes


def _sid_text(kernel32: Any, sid_address: int) -> str:
    wintypes = _wintypes()
    api = win_dll("advapi32")
    api.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    api.ConvertSidToStringSidW.restype = wintypes.BOOL
    result = wintypes.LPWSTR()
    if not api.ConvertSidToStringSidW(
        ctypes.c_void_p(sid_address), ctypes.byref(result)
    ):
        raise win_error(last_error())
    try:
        return (result.value or "").upper()
    finally:
        kernel32.LocalFree(result)


def _ensure_windows_private_dacl(
    path: Path, tighten: Callable[[Path], int | None]
) -> None:
    wintypes = _wintypes()
    api = win_dll("advapi32")
    api.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    api.GetNamedSecurityInfoW.restype = wintypes.DWORD
    api.GetSecurityDescriptorDacl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    ]
    api.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    api.GetAce.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    api.GetAce.restype = wintypes.BOOL

    kernel32 = win_dll("kernel32")
    # Token APIs live in advapi32, not kernel32. Declare them on the same
    # advapi32 handle used for DACL inspection.
    api.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    api.OpenProcessToken.restype = wintypes.BOOL
    api.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.GetTokenInformation.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    token = wintypes.HANDLE()
    if not api.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        raise win_error(last_error())
    try:
        needed = wintypes.DWORD()
        api.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if needed.value == 0:
            raise win_error(last_error())
        token_data = ctypes.create_string_buffer(needed.value)
        if not api.GetTokenInformation(
            token, 1, token_data, needed.value, ctypes.byref(needed)
        ):
            raise win_error(last_error())
        current_sid_pointer = ctypes.c_void_p.from_buffer(token_data).value
        if current_sid_pointer is None:
            raise OSError("Windows token did not contain a user SID")
        current_sid_value = _sid_text(kernel32, current_sid_pointer)
    finally:
        kernel32.CloseHandle(token)

    class Acl(ctypes.Structure):
        _fields_ = [
            ("revision", ctypes.c_ubyte),
            ("reserved", ctypes.c_ubyte),
            ("size", wintypes.WORD),
            ("ace_count", wintypes.WORD),
            ("reserved2", wintypes.WORD),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("ace_type", ctypes.c_ubyte),
            ("ace_flags", ctypes.c_ubyte),
            ("ace_size", ctypes.c_ushort),
        ]

    class AccessAce(ctypes.Structure):
        _fields_ = [
            ("header", AceHeader),
            ("mask", wintypes.DWORD),
            ("sid", ctypes.c_void_p),
        ]

    class Ace(ctypes.Union):
        _fields_ = [("header", AceHeader), ("access", AccessAce)]

    descriptor = wintypes.LPVOID()
    error = api.GetNamedSecurityInfoW(
        str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor)
    )
    if error:
        raise OSError(error, "Unable to read Windows target DACL")

    def private_ace_state() -> tuple[bool, set[str]]:
        present = wintypes.BOOL()
        dacl = wintypes.LPVOID()
        defaulted = wintypes.BOOL()
        if not api.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(present),
            ctypes.byref(dacl),
            ctypes.byref(defaulted),
        ):
            raise win_error(last_error())
        if not present.value or not dacl.value:
            raise ValueError(f"Refusing target config with a NULL DACL: {path}")
        acl_header = ctypes.cast(dacl, ctypes.POINTER(Acl)).contents
        if acl_header.revision != 2 or acl_header.size < 8:
            raise OSError("Windows target descriptor has an invalid DACL")
        unsafe_sids: set[str] = set()
        current_user_has_full_control = False
        denied: dict[str, int] = {}
        for index in range(acl_header.ace_count):
            pointer = wintypes.LPVOID()
            if not api.GetAce(dacl, index, ctypes.byref(pointer)):
                raise win_error(last_error())
            ace = ctypes.cast(pointer, ctypes.POINTER(Ace)).contents
            if ace.header.ace_size < 16:
                continue
            sid = _sid_text(kernel32, ctypes.addressof(ace) + AccessAce.sid.offset)
            if ace.header.ace_type == 1:
                denied[sid] = denied.get(sid, 0) | ace.access.mask
                continue
            if ace.header.ace_type != 0:
                continue
            if sid == current_sid_value and ace.access.mask & 0x001F01FF:
                current_user_has_full_control = True
            # OWNER RIGHTS applies only to the object's owner. The sandbox
            # owner placeholder therefore does not denote another principal.
            if sid == "S-1-3-4" and ace.access.mask & 0x001F01FF:
                current_user_has_full_control = True
                continue
            if sid in {current_sid_value, "S-1-5-18", "S-1-5-32-544"}:
                continue
            if ace.access.mask & (0x00130000 | 2) & ~denied.get(sid, 0):
                unsafe_sids.add(sid)
        return current_user_has_full_control, unsafe_sids

    try:
        current_user_has_full_control, unsafe_sids = private_ace_state()
    finally:
        kernel32.LocalFree(descriptor)
    # Explicit DACLs commonly include a GENERIC_ALL placeholder to which
    # Windows materializes a logon-session SID. Tighten only when that creates
    # a foreign read grant, or when this user lacks control of their own file.
    if unsafe_sids or not current_user_has_full_control:
        mutating_error = tighten(path)
        if mutating_error is not None and mutating_error != 0:
            raise ValueError(
                f"Refusing target config with another principal's read access "
                f"({min(unsafe_sids) if unsafe_sids else 'CURRENT_USER'}); "
                f"unable to tighten its DACL "
                f"(error {mutating_error}): {path}"
            )
        descriptor = wintypes.LPVOID()
        error = api.GetNamedSecurityInfoW(
            str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor)
        )
        if error:
            raise OSError(error, "Unable to re-read Windows target DACL")
        try:
            current_user_has_full_control, unsafe_sids = private_ace_state()
        finally:
            kernel32.LocalFree(descriptor)
    if current_user_has_full_control and not unsafe_sids:
        return
    raise ValueError(
        f"Refusing target config with another principal's read access "
        f"({min(unsafe_sids) if unsafe_sids else 'CURRENT_USER'}): {path}"
    )


def _tighten_windows_dacl(path: Path) -> int | None:
    """Remove inherited and broad ACEs without resolving a mutable user SID."""
    wintypes = _wintypes()
    api = win_dll("advapi32")
    api.SetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    api.SetSecurityInfo.restype = wintypes.DWORD
    api.GetSecurityDescriptorDacl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    ]
    api.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel32 = win_dll("kernel32")
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    descriptor = wintypes.LPVOID()
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        "D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FA;;;BA)",
        1,
        ctypes.byref(descriptor),
        None,
    ):
        return last_error()
    try:
        handle = wintypes.HANDLE(
            kernel32.CreateFileW(
                str(path),
                # READ_CONTROL | WRITE_DAC | SYNCHRONIZE only: the owner
                # always retains those implicitly, so the tighten can also
                # repair a file whose DACL was previously mangled to empty.
                # WRITE_OWNER is never needed and would deny that repair.
                0x00020000 | 0x00040000 | 0x00100000,
                0x00000001 | 0x00000002,
                None,
                3,
                0x00200000 | 0x02000000,
                None,
            )
        )
        if handle.value == wintypes.HANDLE(-1).value:
            return last_error()
        try:
            # SetSecurityInfo wants the PACL, not the whole self-relative
            # security descriptor: overlaying descriptor bytes misparses the
            # header. Extract the ACL the SDDL descriptor actually carries.
            present = wintypes.BOOL()
            dacl = wintypes.LPVOID()
            defaulted = wintypes.BOOL()
            if not api.GetSecurityDescriptorDacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            ):
                return last_error()
            if not present.value or not dacl.value:
                return 1
            # SetSecurityInfo never reads the descriptor's Control bits, so
            # "P" in the SDDL alone is ignored. PROTECTED_DACL_SECURITY_INFORMATION
            # stops inherited ACEs from re-propagating over the replacement.
            protected_dacl = 4 | 0x80000000
            return int(
                api.SetSecurityInfo(handle, 1, protected_dacl, None, None, dacl, None)
            )
        finally:
            kernel32.CloseHandle(handle)
    finally:
        kernel32.LocalFree(descriptor)

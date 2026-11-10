"""Fail-closed authorization and locking for unattended maintenance."""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, cast

from .fingerprint import fingerprint
from .models import MaintenanceAction, MaintenancePlan, MaintenancePolicy
from .resources import resource_names
from .state import load_automation_target

AUTO_PROFILE = "bwg"
AUTO_RESOURCES = resource_names()
_LOCK_OWNER_KEYS = frozenset({"pid", "created_at", "token"})
_LOCK_RECOVERY_ATTEMPTS = 3


@dataclass(frozen=True)
class AutomationAuthorization:
    profile: str
    resource: str
    pin_fingerprint: str


def pin_fingerprint(policy: MaintenancePolicy, action: MaintenanceAction) -> str:
    pin = policy.pins.get(action.resource)
    if not isinstance(pin, dict):
        raise ValueError(
            f"Unattended maintenance requires an explicit {action.resource} pin."
        )
    return fingerprint({"resource": action.resource, "pin": pin})


def _parse_time(value: str) -> tuple[int, int]:
    hour, minute = value.split(":", 1)
    return int(hour), int(minute)


def _in_window(now: datetime, *, start: str, end: str) -> bool:
    current = now.hour * 60 + now.minute
    start_hour, start_minute = _parse_time(start)
    end_hour, end_minute = _parse_time(end)
    start_minutes = start_hour * 60 + start_minute
    end_minutes = end_hour * 60 + end_minute
    return start_minutes <= current < end_minutes


def _ensure_plan_age(plan: MaintenancePlan, *, now: datetime, max_minutes: int) -> None:
    try:
        created = datetime.fromisoformat(plan.created_at)
    except ValueError as exc:
        raise ValueError(
            "Unattended maintenance requires an ISO-8601 plan time."
        ) from exc
    if created.tzinfo is None:
        created = created.astimezone()
    age = now.astimezone(created.tzinfo) - created
    if age < timedelta(0) or age > timedelta(minutes=max_minutes):
        raise ValueError(
            "Stored plan is outside the unattended maintenance age window; rebuild it."
        )


def _ensure_cooldown(
    target: dict[str, object],
    *,
    now: datetime,
    cooldown_minutes: int,
) -> None:
    attempted = datetime.fromisoformat(str(target["last_attempt_at"]))
    if attempted.tzinfo is None:
        attempted = attempted.astimezone()
    elapsed = now.astimezone(attempted.tzinfo) - attempted
    if elapsed < timedelta(minutes=cooldown_minutes):
        raise ValueError(
            "The pinned target is inside its unattended retry cooldown; no remote write was attempted."
        )


def authorize_unattended_apply(
    policy: MaintenancePolicy,
    plan: MaintenancePlan,
    state_path: Path,
    *,
    now: datetime | None = None,
) -> AutomationAuthorization:
    automation = policy.automation
    if not automation.unattended_apply:
        raise ValueError(
            "Unattended apply is disabled; set the explicit automation acknowledgement in policy."
        )
    current = now or datetime.now().astimezone()
    if not _in_window(
        current,
        start=automation.window_start,
        end=automation.window_end,
    ):
        raise ValueError(
            "Current local time is outside the unattended maintenance window; no remote write was attempted."
        )
    _ensure_plan_age(
        plan,
        now=current,
        max_minutes=automation.max_plan_age_minutes,
    )
    profiles = {action.profile for action in plan.actions}
    if profiles != {AUTO_PROFILE}:
        raise ValueError("Unattended apply is restricted to exactly one BWG profile.")
    if AUTO_PROFILE not in automation.profiles:
        raise ValueError("The BWG profile is not in the unattended allowlist.")
    planned = [action for action in plan.actions if action.status == "planned"]
    if len(planned) != 1:
        raise ValueError(
            "Unattended apply requires exactly one planned BWG action; no remote write was attempted."
        )
    action = planned[0]
    if (
        action.resource not in AUTO_RESOURCES
        or action.resource not in automation.resources
    ):
        raise ValueError(
            f"Resource {action.resource} is not in the unattended allowlist."
        )
    target = pin_fingerprint(policy, action)
    previous = load_automation_target(
        state_path,
        profile=action.profile,
        resource=action.resource,
        pin_fingerprint=target,
    )
    if previous is not None:
        if previous["last_outcome"] == "verified":
            raise ValueError(
                "This pinned target was already verified; it is not a new unattended target."
            )
        if int(previous["attempt_count"]) >= automation.max_attempts_per_pin:
            raise ValueError(
                "This pinned target already reached the unattended attempt limit; manual review is required."
            )
        _ensure_cooldown(
            previous,
            now=current,
            cooldown_minutes=automation.cooldown_minutes,
        )
    return AutomationAuthorization(
        profile=action.profile,
        resource=action.resource,
        pin_fingerprint=target,
    )


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        # os.kill(pid, 0) is not a liveness probe on Windows: non-CTRL signals
        # route through TerminateProcess and a reaped pid can report as alive,
        # so a crashed run's lock would never be recovered. Query the process
        # exit state instead; an unanswerable query fails closed as "alive".
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 0x00000103
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_bool,
            ctypes.c_uint32,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_bool
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            # Access denied is not proof of a dead owner. Only Windows'
            # invalid-process-id result permits stale-lock recovery.
            return ctypes.get_last_error() != 87
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _new_lock_owner() -> dict[str, object]:
    return {
        "pid": os.getpid(),
        "created_at": datetime.now().astimezone().isoformat(),
        "token": secrets.token_hex(16),
    }


def _read_lock_owner(path: Path) -> dict[str, object]:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or set(existing) - _LOCK_OWNER_KEYS:
            raise ValueError("Lock owner must be a recognized object.")
        pid = existing.get("pid")
        token = existing.get("token")
        if type(pid) is not int or pid <= 0:
            raise ValueError("Lock owner must have a positive process id.")
        if token is not None and (
            not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None
        ):
            raise ValueError("Lock owner token is invalid.")
        created = datetime.fromisoformat(str(existing["created_at"]))
        if created.tzinfo is None:
            raise ValueError("Lock owner creation time must include a timezone.")
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError(
            "Unattended maintenance lock is unreadable; remove it only after manual review."
        ) from None
    return existing


def _owner_age(owner: dict[str, object]) -> timedelta:
    created = datetime.fromisoformat(str(owner["created_at"]))
    return datetime.now().astimezone() - created.astimezone()


def _recover_dead_owner(path: Path) -> None:
    owner = _read_lock_owner(path)
    if _pid_alive(cast(int, owner["pid"])):
        return
    if owner.get("token") is None:
        raise ValueError(
            "The legacy dead lock owner has no fencing token; preserve it for manual review."
        )

    candidate = path.with_name(f".{path.name}.recovery-{secrets.token_hex(8)}")
    try:
        os.replace(path, candidate)
    except (FileNotFoundError, PermissionError):
        return
    try:
        try:
            moved = _read_lock_owner(candidate)
        except ValueError:
            restore_moved_owner(path, candidate)
            raise
        if not secrets.compare_digest(str(moved.get("token")), str(owner["token"])):
            restore_moved_owner(path, candidate)
            return
        if _pid_alive(cast(int, moved["pid"])):
            restore_moved_owner(path, candidate)
            return
    finally:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def restore_moved_owner(path: Path, candidate: Path) -> None:
    """Return a moved owner unless a successor already occupies the owner path."""
    try:
        os.link(candidate, path)
    except FileExistsError:
        return
    except OSError as exc:
        raise ValueError(
            "Recovered lock owner could not be restored safely; preserve it for manual review."
        ) from exc


@contextmanager
def unattended_lock(path: Path, *, stale_after_minutes: int = 120) -> Iterator[None]:
    """Serialize acquisition, stale-owner recovery and writes with an OS lock."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    # This inode must persist: unlinking it would let another process lock a
    # replacement while the first still holds the original OS lock.
    guard = path.with_name(path.name + ".guard")
    descriptor = os.open(guard, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError(
                "Another unattended maintenance run owns the lock; no remote write was attempted."
            ) from None
        with _unattended_owner_lock(path, stale_after_minutes=stale_after_minutes):
            yield
    finally:
        # Closing releases the native lock even after failure or process exit.
        os.close(descriptor)


@contextmanager
def _unattended_owner_lock(path: Path, *, stale_after_minutes: int) -> Iterator[None]:
    """Acquire a recoverable local lock, failing closed while an owner is alive."""

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = _new_lock_owner()
    for _ in range(_LOCK_RECOVERY_ATTEMPTS):
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            try:
                existing = _read_lock_owner(path)
            except FileNotFoundError:
                continue
            if _owner_age(existing) > timedelta(
                minutes=stale_after_minutes
            ) and not _pid_alive(cast(int, existing["pid"])):
                _recover_dead_owner(path)
                continue
            # `from None`: holding the lock is expected control flow, not an
            # error worth chaining into the operator-facing message.
            raise ValueError(
                "Another unattended maintenance run owns the lock; no remote write was attempted."
            ) from None
        else:
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(owner, stream, ensure_ascii=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                yield
            finally:
                # A rename by stale recovery may briefly hide this owner. Only
                # delete a file that still contains this holder's fencing token.
                try:
                    current = _read_lock_owner(path)
                    if secrets.compare_digest(
                        str(current.get("token")), str(owner["token"])
                    ):
                        path.unlink()
                except (FileNotFoundError, ValueError):
                    pass
            return
    raise ValueError("Unattended maintenance lock could not be acquired safely.")

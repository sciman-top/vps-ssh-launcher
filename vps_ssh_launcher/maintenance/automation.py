"""Fail-closed authorization and locking for unattended maintenance."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .fingerprint import fingerprint
from .models import MaintenanceAction, MaintenancePlan, MaintenancePolicy
from .state import load_automation_target

AUTO_PROFILE = "bwg"
AUTO_RESOURCES = frozenset({"xray", "docker"})


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
    start_minutes = _parse_time(start)[0] * 60 + _parse_time(start)[1]
    end_minutes = _parse_time(end)[0] * 60 + _parse_time(end)[1]
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
    if os.name == "nt":
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
            return False
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


@contextmanager
def unattended_lock(path: Path, *, stale_after_minutes: int = 120) -> Iterator[None]:
    """Acquire a recoverable local lock, failing closed while an owner is alive."""

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = {"pid": os.getpid(), "created_at": datetime.now().astimezone().isoformat()}
    for _ in range(2):
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                pid = int(existing.get("pid", -1))
                created = datetime.fromisoformat(str(existing["created_at"]))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                raise ValueError(
                    "Unattended maintenance lock is unreadable; remove it only after manual review."
                ) from None
            age = datetime.now().astimezone() - created.astimezone()
            if age > timedelta(minutes=stale_after_minutes) and not _pid_alive(pid):
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                continue
            raise ValueError(
                "Another unattended maintenance run owns the lock; no remote write was attempted."
            )
        else:
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(owner, stream, ensure_ascii=True)
                    stream.write("\n")
                yield
            finally:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            return
    raise ValueError("Unattended maintenance lock could not be acquired safely.")

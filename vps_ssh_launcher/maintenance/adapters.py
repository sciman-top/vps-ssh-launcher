"""Adapter dispatch and outcome markers; legacy upgrade-command builders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .models import ActionStatus, MaintenanceAction
from .xray import build_xray_upgrade_command as build_xray_upgrade_command
from .docker import build_docker_upgrade_command as build_docker_upgrade_command
from .resources import RESOURCES


@dataclass(frozen=True)
class AdapterResult:
    status: ActionStatus
    reason: str


class MaintenanceBusy(RuntimeError):
    """The host's shared maintenance lock is held elsewhere.

    Both adapters take the flock before any mutation, so this outcome means
    nothing was written and the plan stays retryable.
    """


RemoteExecutor = Callable[[str], tuple[int, str, str]]


def execute_action(
    action: MaintenanceAction,
    *,
    pins: dict[str, dict[str, Any]],
    executor: RemoteExecutor,
) -> AdapterResult:
    spec = RESOURCES.get(action.resource)
    if spec is None:
        raise ValueError(f"No remote adapter is admitted for {action.resource}.")
    pin = pins.get(action.resource)
    if not pin:
        raise ValueError(spec.missing_pin_error)
    command = spec.build_command(pin)

    code, stdout, stderr = executor(command)
    marker_lines = {line.strip() for line in f"{stdout}\n{stderr}".splitlines()}
    if code == 75 and "MAINTENANCE_BUSY" in marker_lines:
        raise MaintenanceBusy(
            "Host busy: another maintenance job holds the maintenance lock; "
            "no remote change was attempted."
        )
    # Markers are protocol receipts, not diagnostic log text. Prefer the final
    # stdout line and require it to be exact, so a remote command that merely
    # echoes a phrase such as "echo APPLY_VERIFIED" cannot become a success.
    # The refusal marker may be the final line of either stream (the docker
    # adapter echoes it to stderr); it wins over every other marker because a
    # pre-mutation refusal is the strongest claim about the host state. The
    # stderr receipt widens ONLY the refusal check: apply/rollback markers
    # keep their historical stdout-only contract.
    stdout_lines = stdout.splitlines()
    marker_lines = (
        {stdout_lines[-1].strip()}
        if stdout_lines and stdout_lines[-1].strip()
        else set()
    )
    stderr_lines = stderr.splitlines()
    stderr_receipt = (
        {stderr_lines[-1].strip()}
        if stderr_lines and stderr_lines[-1].strip()
        else set()
    )
    if "APPLY_REFUSED_BEFORE_MUTATION" in (marker_lines | stderr_receipt):
        return AdapterResult(
            "refused_before_mutation",
            "Remote adapter refused the change before any mutation; the host "
            "is untouched and the pin mismatch needs manual review.",
        )
    if code == 0 and "APPLY_VERIFIED" in marker_lines:
        return AdapterResult(
            "verified",
            "Remote adapter completed and read back the target state.",
        )
    if "ROLLBACK_VERIFIED" in marker_lines:
        return AdapterResult(
            "rolled_back",
            "Remote adapter failed; its scoped rollback was verified.",
        )
    return AdapterResult(
        "unverified",
        "Remote adapter did not produce a verified success or rollback marker.",
    )

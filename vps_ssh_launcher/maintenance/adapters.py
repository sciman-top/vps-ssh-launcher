"""Adapter dispatch and outcome markers; legacy builder/validator imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .models import ActionStatus, MaintenanceAction
from .pins import (
    normalize_version as normalize_version,
    normalize_sha256 as normalize_sha256,
    normalize_digest as normalize_digest,
    normalize_compose_file as normalize_compose_file,
    normalize_services as normalize_services,
    normalize_digests as normalize_digests,
)
from .xray import (
    build_xray_upgrade_command as build_xray_upgrade_command,
    XRAY_BINARY as XRAY_BINARY,
    XRAY_CONFDIR as XRAY_CONFDIR,
    XRAY_SERVICE as XRAY_SERVICE,
)
from .docker import build_docker_upgrade_command as build_docker_upgrade_command
from .resources import RESOURCES


@dataclass(frozen=True)
class AdapterResult:
    status: ActionStatus
    reason: str


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

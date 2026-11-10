"""Single-profile SSH execution for an already reviewed maintenance plan."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from .. import config as target_config, connection, execution
from .adapters import MaintenanceBusy, execute_action
from .inventory import collect_inventory, profile_args
from .models import MaintenancePlan, MaintenancePolicy
from .planner import updated_plan
from .state import load_plan, save_plan

# Remote adapters may download artifacts and restart services during long
# silent intervals, so the interactive command timeout is not sufficient.
ADAPTER_IDLE_TIMEOUT_SECONDS = 300
ADAPTER_HARD_TIMEOUT_SECONDS = 900

RemoteStart = Callable[[], None]


class RemotePlanRefusal(ValueError):
    """Pre-write guard refusal: no remote write connection was attempted.

    Raised for the predictable revalidation guards between the read-only
    probe and the write connection (integration opt-in, inventory fingerprint
    drift, unreachable host, missing target profile). Callers translate it
    into a structured refused outcome instead of an unexpected local error.
    """


def execute_remote_plan(
    policy: MaintenancePolicy,
    plan: MaintenancePlan,
    *,
    target: Path,
    state_path: Path,
    profile: str,
    on_remote_start: RemoteStart | None = None,
) -> tuple[MaintenancePlan, str, str, int]:
    """Revalidate one host, run its admitted adapters, and persist transitions."""
    # Fail closed on a publicly readable target config BEFORE any SSH
    # activity: assert_private_target_config promises not to read a target
    # other users can access, so the fresh-inventory probe must not outrun it.
    target_config.assert_private_target_config(target)
    fresh = collect_inventory(policy, target, profile=profile)
    if fresh.fingerprint != plan.inventory_fingerprint:
        raise RemotePlanRefusal(
            "Fresh inventory fingerprint does not match the stored plan; rebuild the plan."
        )
    record = fresh.records[0] if fresh.records else None
    if record is None or not record.reachable:
        raise RemotePlanRefusal("Fresh pre-apply inventory is not reachable.")

    config = target_config.load_config(target)
    target_config.assert_private_target_config(target)
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or profile not in profiles:
        raise RemotePlanRefusal("Target config profile is missing for remote apply.")
    entry = profiles[profile]
    if not isinstance(entry, dict):
        raise RemotePlanRefusal("Target config profile must be an object.")
    target_config.validate_profile(entry, profile, require_auth=True)
    connection_args = profile_args(
        profile,
        entry,
        target_config=target,
        policy=policy,
    )
    client = connection.connect_with_retry(connection_args)
    # This plan was freshly loaded and has not yet acquired a refundable
    # attempt. A busy result must not inherit that old authority.
    updated = replace(
        plan,
        may_refund_unattended_attempt=False,
    )
    try:
        # Count an unattended attempt only after SSH connects; keep the callback
        # inside the close boundary so state errors cannot leak the client.
        if on_remote_start is not None:
            on_remote_start()
        snapshot = load_plan(state_path, plan.plan_id)
        updated = snapshot
        for index, action in enumerate(plan.actions):
            if action.status != "planned":
                continue
            updated = updated_plan(
                updated,
                action_index=index,
                status="applied",
                reason=(
                    "Remote adapter started; outcome remains unresolved until its "
                    "success or rollback marker is verified."
                ),
            )
            # Save before the adapter can mutate the host. A crash must not make
            # this action look planned and eligible for an automatic retry.
            save_plan(state_path, updated)
            try:
                result = execute_action(
                    action,
                    pins=policy.pins,
                    executor=lambda command: execution.exec_remote(
                        client,
                        command,
                        command_timeout=max(
                            policy.command_timeout,
                            ADAPTER_IDLE_TIMEOUT_SECONDS,
                        ),
                        command_hard_timeout=max(
                            policy.command_timeout * 2,
                            ADAPTER_HARD_TIMEOUT_SECONDS,
                        ),
                    ),
                )
            except MaintenanceBusy:
                # The CLI restores the exact durable action and performs the
                # attempt/plan refund as one transaction. Do not erase the
                # current invocation's refund receipt with an intermediate save.
                raise
            updated = updated_plan(
                updated,
                action_index=index,
                status=result.status,
                reason=result.reason,
            )
            save_plan(state_path, updated)
            if result.status != "verified":
                break
    finally:
        client.close()

    if any(action.status == "unverified" for action in updated.actions):
        return (
            updated,
            "unverified",
            "Remote execution did not establish a verified apply or rollback.",
            1,
        )
    if any(action.status == "rolled_back" for action in updated.actions):
        return (
            updated,
            "rolled_back",
            "Remote execution failed and the adapter verified its rollback.",
            1,
        )
    return (
        updated,
        "verified",
        "Remote execution completed with adapter readback verification.",
        0,
    )

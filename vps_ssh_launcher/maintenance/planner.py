"""Deterministic policy-to-plan translation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from .fingerprint import fingerprint, inventory_fingerprint
from .models import (
    ActionStatus,
    InventoryRecord,
    InventorySnapshot,
    MaintenanceAction,
    MaintenancePlan,
    MaintenancePolicy,
    PlanStatus,
)
from .resources import RESOURCES


def _observed(record: InventoryRecord, resource: str) -> str:
    return record.facts.get(resource, "unknown")


def _action_for(
    profile: str,
    resource: str,
    desired: str,
    record: InventoryRecord | None,
    pins: dict[str, dict[str, Any]],
) -> MaintenanceAction:
    if record is None or not record.reachable:
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed="unreachable",
            status="blocked",
            reason="Fresh reachable inventory is required before planning changes.",
        )
    observed = _observed(record, resource)
    spec = RESOURCES.get(resource)
    if spec is not None and spec.observed_fact_key and desired == "upgrade":
        observed = record.facts.get(spec.observed_fact_key, "unknown")
    if record.facts.get("reboot_required") == "present" and desired in {
        "upgrade",
        "present",
        "managed",
    }:
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="blocked",
            reason=(
                "Host reports /run/reboot-required; complete the reviewed manual "
                "reboot and collect fresh inventory before applying changes."
            ),
        )
    if desired in {"deferred", "manual"}:
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="deferred",
            reason="Resource remains on its existing guarded/manual maintenance path.",
        )
    if resource == "proxy_core":
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="blocked",
            reason="No reviewed proxy-core adapter is admitted by this control plane.",
        )

    if desired == "upgrade" and spec is not None:
        return spec.plan_upgrade(profile, desired, observed, pins.get(resource))
    if observed == desired or (
        desired == "present" and observed in {"active", "present"}
    ):
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="noop",
            reason="Observed state already satisfies the desired state.",
        )
    if observed == "absent" and desired in {"present", "managed"}:
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="blocked",
            reason="Required resource is absent; no adapter is authorized to install it.",
        )
    return MaintenanceAction(
        profile=profile,
        resource=resource,
        desired=desired,
        observed=observed,
        status="blocked",
        reason="State differs and no reviewed adapter is admitted.",
    )


def build_plan(
    policy: MaintenancePolicy,
    inventory: InventorySnapshot,
    *,
    profile: str | None = None,
) -> MaintenancePlan:
    if profile is not None:
        if profile not in policy.profiles:
            raise ValueError(
                f"Profile is not configured in maintenance policy: {profile}"
            )
        if not policy.profiles[profile].get("enabled", True):
            raise ValueError(f"Maintenance profile is disabled: {profile}")
        selected = tuple(
            record for record in inventory.records if record.profile == profile
        )
        inventory = replace(
            inventory, records=selected, fingerprint=inventory_fingerprint(selected)
        )
    records: dict[str, InventoryRecord] = {}
    for record in inventory.records:
        if not record.profile.strip():
            raise ValueError("Inventory records require a non-empty profile name.")
        if record.profile in records:
            raise ValueError(f"Inventory contains duplicate profile: {record.profile}")
        records[record.profile] = record
    actions: list[MaintenanceAction] = []
    for profile_name in policy.profile_names() if profile is None else (profile,):
        profile_policy = policy.profiles[profile_name]
        if not profile_policy.get("enabled", True):
            continue
        resources = profile_policy.get("resources", {})
        for resource, desired in sorted(resources.items()):
            actions.append(
                _action_for(
                    profile_name,
                    resource,
                    str(desired),
                    records.get(profile_name),
                    policy.pins,
                )
            )
    statuses = {action.status for action in actions}
    status: PlanStatus
    if "blocked" in statuses:
        status = "blocked"
    elif "planned" in statuses:
        status = "planned"
    else:
        status = "ready"
    plan_key = {
        "policy_fingerprint": policy.fingerprint,
        "inventory_fingerprint": inventory.fingerprint,
        "actions": [action.to_dict() for action in actions],
    }
    plan_id = "plan-" + fingerprint(plan_key).split(":", 1)[1][:16]
    return MaintenancePlan(
        plan_id=plan_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        policy_fingerprint=policy.fingerprint,
        inventory_fingerprint=inventory.fingerprint,
        actions=tuple(actions),
        status=status,
    )


def updated_plan(
    plan: MaintenancePlan,
    *,
    action_index: int,
    status: ActionStatus,
    reason: str,
) -> MaintenancePlan:
    actions = list(plan.actions)
    actions[action_index] = replace(
        actions[action_index],
        status=status,
        reason=reason,
    )
    statuses = {action.status for action in actions}
    plan_status: PlanStatus
    if "applied" in statuses:
        plan_status = "applied"
    elif "unverified" in statuses:
        plan_status = "unverified"
    elif "rolled_back" in statuses:
        plan_status = "rolled_back"
    elif "verified" in statuses:
        plan_status = "verified"
    else:
        plan_status = plan.status
    return replace(plan, actions=tuple(actions), status=plan_status)

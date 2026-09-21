"""Deterministic policy-to-plan translation."""

from __future__ import annotations

from datetime import datetime, timezone

from .fingerprint import fingerprint
from .models import (
    InventoryRecord,
    InventorySnapshot,
    MaintenanceAction,
    MaintenancePlan,
    MaintenancePolicy,
)


def _observed(record: InventoryRecord, resource: str) -> str:
    return record.facts.get(resource, "unknown")


def _action_for(
    profile: str,
    resource: str,
    desired: str,
    record: InventoryRecord | None,
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
            status="noop",
            reason="Resource is absent; no adapter is authorized to install it.",
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
) -> MaintenancePlan:
    records = {record.profile: record for record in inventory.records}
    actions: list[MaintenanceAction] = []
    for profile in policy.profile_names():
        profile_policy = policy.profiles[profile]
        if not profile_policy.get("enabled", True):
            continue
        resources = profile_policy.get("resources", {})
        for resource, desired in sorted(resources.items()):
            actions.append(
                _action_for(
                    profile,
                    resource,
                    str(desired),
                    records.get(profile),
                )
            )
    statuses = {action.status for action in actions}
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

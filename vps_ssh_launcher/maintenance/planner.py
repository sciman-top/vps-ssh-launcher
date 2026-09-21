"""Deterministic policy-to-plan translation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
    if resource == "xray" and desired == "upgrade":
        observed = record.facts.get("xray_version", "unknown")
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
    if desired == "upgrade" and resource == "xray":
        pin = pins.get("xray")
        if not pin:
            return MaintenanceAction(
                profile=profile,
                resource=resource,
                desired=desired,
                observed=observed,
                status="blocked",
                reason="Xray upgrade requires an explicit version and SHA-256 pin.",
            )
        target = str(pin["version"])
        if observed in {target, f"v{target}"}:
            return MaintenanceAction(
                profile=profile,
                resource=resource,
                desired=desired,
                observed=observed,
                status="noop",
                reason="Observed Xray version already matches the pinned target.",
                target=target,
            )
        if observed in {"absent", "unknown", "present"}:
            return MaintenanceAction(
                profile=profile,
                resource=resource,
                desired=desired,
                observed=observed,
                status="blocked",
                reason="Fresh inventory must report a concrete Xray version before upgrade.",
                target=target,
            )
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="planned",
            reason="Pinned Xray version differs from the fresh inventory.",
            target=target,
        )
    if desired == "upgrade" and resource == "docker":
        pin = pins.get("docker")
        if observed != "present":
            return MaintenanceAction(
                profile=profile,
                resource=resource,
                desired=desired,
                observed=observed,
                status="blocked",
                reason="Docker must be present before a Compose reconciliation.",
            )
        if not pin:
            return MaintenanceAction(
                profile=profile,
                resource=resource,
                desired=desired,
                observed=observed,
                status="blocked",
                reason="Docker upgrade requires an absolute Compose path, service allowlist and digest pins.",
            )
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="planned",
            reason="Pinned non-CPA Compose services are ready for reconciliation.",
            target=str(pin["compose_file"]),
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
                    policy.pins,
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

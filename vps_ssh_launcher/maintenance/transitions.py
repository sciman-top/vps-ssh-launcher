"""Pure maintenance-plan state transitions.

This module contains no storage or transport code. It is the single place for
the monotonic action and plan status rules shared by planning and persistence.
"""

from __future__ import annotations

from dataclasses import replace

from .models import ActionStatus, MaintenancePlan, PlanStatus

_ACTION_RANK = {
    "noop": 0,
    "planned": 1,
    "deferred": 1,
    "blocked": 1,
    "applied": 2,
    "verified": 3,
    "unverified": 3,
    "rolled_back": 3,
    "refused_before_mutation": 3,
}

_PLAN_RANK = {
    "ready": 1,
    "planned": 1,
    "blocked": 1,
    "applied": 2,
    "verified": 3,
    "unverified": 3,
    "rolled_back": 3,
    "refused_before_mutation": 3,
}


def is_backward_transition(
    previous: MaintenancePlan, incoming: MaintenancePlan
) -> bool:
    """Return whether ``incoming`` would discard execution information.

    Per-action state is authoritative. Plan-level status is checked only when
    no action advanced, because one action may legitimately be retried while a
    different action is already verified.
    """
    if len(previous.actions) != len(incoming.actions):
        return True
    advanced = False
    for stored, candidate in zip(previous.actions, incoming.actions, strict=True):
        stored_rank = _ACTION_RANK.get(stored.status, 0)
        candidate_rank = _ACTION_RANK.get(candidate.status, 0)
        if candidate_rank < stored_rank:
            return True
        if candidate_rank > stored_rank:
            advanced = True
        if (
            stored_rank == 3
            and candidate_rank == 3
            and stored.status != candidate.status
        ):
            return True
    if advanced:
        return False
    previous_rank = _PLAN_RANK.get(previous.status, 0)
    incoming_rank = _PLAN_RANK.get(incoming.status, 0)
    if incoming_rank < previous_rank:
        return True
    return (
        previous_rank == 3 and incoming_rank == 3 and previous.status != incoming.status
    )


def updated_plan(
    plan: MaintenancePlan,
    *,
    action_index: int,
    status: ActionStatus,
    reason: str,
) -> MaintenancePlan:
    """Apply one action result and derive the aggregate plan status."""
    actions = list(plan.actions)
    actions[action_index] = replace(
        actions[action_index],
        status=status,
        reason=reason,
    )
    statuses = {action.status for action in actions}
    plan_status: PlanStatus
    if any(action.status == "applied" for action in actions):
        plan_status = "applied"
    elif "refused_before_mutation" in statuses:
        plan_status = "refused_before_mutation"
    elif "unverified" in statuses:
        plan_status = "unverified"
    elif "rolled_back" in statuses:
        plan_status = "rolled_back"
    elif "verified" in statuses:
        plan_status = "verified"
    elif any(action.status == "planned" for action in actions):
        plan_status = "planned"
    elif "deferred" in statuses or "noop" in statuses:
        plan_status = "planned"
    else:
        plan_status = plan.status
    return replace(plan, actions=tuple(actions), status=plan_status)

"""Typed models for the local maintenance control plane."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast, get_args

ActionStatus = Literal[
    "noop",
    "planned",
    "deferred",
    "blocked",
    "applied",
    "verified",
    "unverified",
    "rolled_back",
    "refused_before_mutation",
]

PlanStatus = Literal[
    "ready",
    "planned",
    "blocked",
    "applied",
    "verified",
    "unverified",
    "rolled_back",
    "refused_before_mutation",
]
ACTION_STATUSES = frozenset(get_args(ActionStatus))
PLAN_STATUSES = frozenset(get_args(PlanStatus))

AutomationMode = Literal["observe", "unattended_apply"]


@dataclass(frozen=True)
class AutomationPolicy:
    mode: AutomationMode
    acknowledgement: str | None
    profiles: tuple[str, ...]
    resources: tuple[str, ...]
    window_start: str
    window_end: str
    max_attempts_per_pin: int
    cooldown_minutes: int
    max_plan_age_minutes: int

    @property
    def unattended_apply(self) -> bool:
        return self.mode == "unattended_apply"


@dataclass(frozen=True)
class InventoryRecord:
    profile: str
    reachable: bool
    facts: dict[str, str] = field(default_factory=dict)
    error_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "reachable": self.reachable,
            "facts": dict(sorted(self.facts.items())),
            "error_class": self.error_class,
        }


@dataclass(frozen=True)
class InventorySnapshot:
    created_at: str
    records: tuple[InventoryRecord, ...]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "records": [record.to_dict() for record in self.records],
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class MaintenancePolicy:
    config_path: str
    strict_host_key_checking: bool
    command_timeout: int
    state_path: str | None
    receipt_dir: str | None
    profiles: dict[str, dict[str, Any]]
    pins: dict[str, dict[str, Any]]
    automation: AutomationPolicy
    fingerprint: str

    def profile_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.profiles))


@dataclass(frozen=True)
class MaintenanceAction:
    profile: str
    resource: str
    desired: str
    observed: str
    status: ActionStatus
    reason: str
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "profile": self.profile,
            "resource": self.resource,
            "desired": self.desired,
            "observed": self.observed,
            "status": self.status,
            "reason": self.reason,
        }
        if self.target is not None:
            value["target"] = self.target
        return value


@dataclass(frozen=True)
class MaintenancePlan:
    plan_id: str
    created_at: str
    policy_fingerprint: str
    inventory_fingerprint: str
    actions: tuple[MaintenanceAction, ...]
    status: PlanStatus
    # True only for a newly created, pristine plan. An old or ambiguous stored
    # plan cannot safely refund an automation attempt after a busy response.
    may_refund_unattended_attempt: bool = field(default=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "policy_fingerprint": self.policy_fingerprint,
            "inventory_fingerprint": self.inventory_fingerprint,
            "actions": [action.to_dict() for action in self.actions],
            "status": self.status,
            "may_refund_unattended_attempt": self.may_refund_unattended_attempt,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MaintenancePlan:
        """Rehydrate a serialized plan without depending on SQLite storage."""
        plan_id = value.get("plan_id")
        plan_status = value.get("status")
        action_values = value.get("actions")
        if (
            not isinstance(plan_id, str)
            or not isinstance(plan_status, str)
            or plan_status not in PLAN_STATUSES
            or not isinstance(action_values, list)
        ):
            raise ValueError("Stored maintenance plan is invalid.")
        actions: list[MaintenanceAction] = []
        for item in action_values:
            if not isinstance(item, dict):
                raise ValueError("Stored maintenance action is invalid.")
            action_status = item.get("status")
            if (
                not isinstance(action_status, str)
                or action_status not in ACTION_STATUSES
            ):
                raise ValueError("Stored maintenance action status is invalid.")
            actions.append(
                MaintenanceAction(
                    profile=str(item["profile"]),
                    resource=str(item["resource"]),
                    desired=str(item["desired"]),
                    observed=str(item["observed"]),
                    status=cast(ActionStatus, action_status),
                    reason=str(item["reason"]),
                    target=(
                        str(item["target"]) if item.get("target") is not None else None
                    ),
                )
            )
        return cls(
            plan_id=plan_id,
            created_at=str(value["created_at"]),
            policy_fingerprint=str(value["policy_fingerprint"]),
            inventory_fingerprint=str(value["inventory_fingerprint"]),
            actions=tuple(actions),
            status=cast(PlanStatus, plan_status),
            may_refund_unattended_attempt=(
                value.get("may_refund_unattended_attempt", True) is True
            ),
        )

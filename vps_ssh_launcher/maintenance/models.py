"""Typed models for the local maintenance control plane."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ActionStatus = Literal[
    "noop",
    "planned",
    "deferred",
    "blocked",
    "applied",
    "verified",
    "unverified",
    "rolled_back",
]


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

    def to_dict(self) -> dict[str, str]:
        return {
            "profile": self.profile,
            "resource": self.resource,
            "desired": self.desired,
            "observed": self.observed,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MaintenancePlan:
    plan_id: str
    created_at: str
    policy_fingerprint: str
    inventory_fingerprint: str
    actions: tuple[MaintenanceAction, ...]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "policy_fingerprint": self.policy_fingerprint,
            "inventory_fingerprint": self.inventory_fingerprint,
            "actions": [action.to_dict() for action in self.actions],
            "status": self.status,
        }

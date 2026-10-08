"""Registry of maintainable resources: the control plane's single authority
for which resource names exist.

Adding a resource touches, in order:
1. a resource module with pin validation, upgrade planning and command building;
2. one ``ResourceSpec`` here linking those functions;
3. its probe facts in ``maintenance/inventory.py`` (``INVENTORY_COMMAND``
   and ``_ALLOWED_FACT_KEYS``);
4. planner/admission/rollback tests in ``tests/test_maintenance.py``.

Planner observation, automation eligibility and policy defaults all derive
from this registry, so those modules need no per-resource edits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import docker, xray
from .models import MaintenanceAction


@dataclass(frozen=True)
class ResourceSpec:
    name: str
    normalize_pin: Callable[[Any], dict[str, Any]]
    build_command: Callable[[dict[str, Any]], str]
    plan_upgrade: Callable[[str, str, str, dict[str, Any] | None], MaintenanceAction]
    missing_pin_error: str
    # Inventory fact consulted as the observed state for upgrade planning;
    # None keeps the generic ``facts[<resource>]`` key.
    observed_fact_key: str | None = None


RESOURCES: dict[str, ResourceSpec] = {
    spec.name: spec
    for spec in (
        ResourceSpec(
            "xray",
            xray.normalize_pin,
            xray.build_from_pin,
            xray.plan_upgrade,
            "Xray action has no version/SHA-256 pin.",
            observed_fact_key="xray_version",
        ),
        ResourceSpec(
            "docker",
            docker.normalize_pin,
            docker.build_from_pin,
            docker.plan_upgrade,
            "Docker action has no Compose/digest pin.",
        ),
    )
}


def resource_names() -> frozenset[str]:
    return frozenset(RESOURCES)

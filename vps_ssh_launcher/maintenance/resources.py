"""Registry of maintainable resources: the control plane's single authority
for which resource names exist.

Adding a resource touches, in order:
1. one ``ResourceSpec`` here;
2. its pin schema branch in ``maintenance/config.py`` ``_pins_table``;
3. its remote command builder and ``_PIN_ADAPTERS`` entry in
   ``maintenance/adapters.py``;
4. its probe facts in ``maintenance/inventory.py`` (``INVENTORY_COMMAND``
   and ``_ALLOWED_FACT_KEYS``);
5. planner/admission tests in ``tests/test_maintenance.py``.

Planner observation, automation eligibility and policy defaults all derive
from this registry, so those modules need no per-resource edits.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceSpec:
    name: str
    # Inventory fact consulted as the observed state for upgrade planning;
    # None keeps the generic ``facts[<resource>]`` key.
    observed_fact_key: str | None = None


RESOURCES: dict[str, ResourceSpec] = {
    spec.name: spec
    for spec in (
        ResourceSpec("xray", observed_fact_key="xray_version"),
        ResourceSpec("docker"),
    )
}


def resource_names() -> frozenset[str]:
    return frozenset(RESOURCES)

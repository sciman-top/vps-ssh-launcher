"""Read-only SSH inventory for the maintenance control plane."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, cast

from .. import config as target_config_module, connection, execution
from .fingerprint import inventory_fingerprint as inventory_fingerprint
from .models import InventoryRecord, InventorySnapshot, MaintenancePolicy
from ..safe_files import atomic_write_json

from .inventory_protocol import (
    INVENTORY_COMMAND,
    _ALLOWED_FACT_KEYS,
    _FACT_MAX_CHARS as _FACT_MAX_CHARS,
    _bound_fact as _bound_fact,
    parse_probe_output,
)


def _target_profiles(target_config: Path) -> dict[str, Any]:
    config = target_config_module.load_config(target_config)
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("Target config must contain a non-empty 'profiles' object.")
    return cast(dict[str, Any], profiles)


def profile_args(
    name: str,
    entry: dict[str, Any],
    *,
    target_config: Path,
    policy: MaintenancePolicy,
) -> argparse.Namespace:
    # Maintenance inventory never reuses CLI auth overrides; it resolves each
    # profile's own credentials against the target config directory.
    base_args = argparse.Namespace(
        password=None,
        key=None,
        allow_agent=False,
    )
    return target_config_module.build_connect_namespace(
        entry,
        profile_name=name,
        base_args=base_args,
        config_dir=target_config.parent,
        strict_host_key_checking=policy.strict_host_key_checking,
    )


Connector = Callable[[str, dict[str, Any]], tuple[int, str, str]]


def _default_connector(
    name: str,
    entry: dict[str, Any],
    *,
    target_config: Path,
    policy: MaintenancePolicy,
) -> tuple[int, str, str]:
    args = profile_args(
        name,
        entry,
        target_config=target_config,
        policy=policy,
    )
    client = connection.connect_with_retry(args)
    try:
        return execution.exec_remote(
            client,
            INVENTORY_COMMAND,
            command_timeout=policy.command_timeout,
            command_hard_timeout=policy.command_timeout * 2,
        )
    finally:
        client.close()


def collect_inventory(
    policy: MaintenancePolicy,
    target_config: Path,
    *,
    profile: str | None = None,
    connector: Connector | None = None,
) -> InventorySnapshot:
    profiles = _target_profiles(target_config)
    selected = [profile] if profile else list(policy.profile_names())
    if not selected:
        raise ValueError("No maintenance profiles are configured.")
    missing = [name for name in selected if name not in profiles]
    if missing:
        raise ValueError(
            "Profiles missing from target config: " + ", ".join(sorted(missing))
        )

    records: list[InventoryRecord] = []
    for name in sorted(selected):
        entry = profiles[name]
        policy_profile = policy.profiles.get(name, {})
        if not policy_profile.get("enabled", True):
            # Admission first: a policy-disabled profile is recorded as such even
            # when it has no credentials. Validating auth first aborted the whole
            # inventory run instead of reporting the documented error_class.
            records.append(
                InventoryRecord(
                    profile=name,
                    reachable=False,
                    error_class="disabled_by_policy",
                )
            )
            continue
        target_config_module.validate_profile(entry, name, require_auth=True)
        try:
            if connector is None:
                result = _default_connector(
                    name,
                    cast(dict[str, Any], entry),
                    target_config=target_config,
                    policy=policy,
                )
            else:
                result = connector(name, cast(dict[str, Any], entry))
            code, stdout, _stderr = result
            records.append(parse_probe_output(name, stdout, return_code=code))
        except Exception as exc:
            records.append(
                InventoryRecord(
                    profile=name,
                    reachable=False,
                    error_class=type(exc).__name__.lower(),
                )
            )

    created_at = datetime.now(timezone.utc).isoformat()
    return InventorySnapshot(
        created_at=created_at,
        records=tuple(records),
        fingerprint=inventory_fingerprint(records),
    )


def write_inventory(path: Path, snapshot: InventorySnapshot) -> None:
    # Atomic 0600 write, mirroring the state DB and receipt storage promise:
    # inventory records hostname/listener/docker topology, which is as
    # sensitive as the rest of the maintenance state, and a torn write must
    # not leave a half-readable JSON for the next plan run to choke on.
    atomic_write_json(path, snapshot.to_dict(), replace_existing=False)


def load_inventory(path: Path) -> InventorySnapshot:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read inventory file: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("Inventory file root must be an object.")
    records_raw = value.get("records")
    if not isinstance(records_raw, list):
        raise ValueError("Inventory file records must be an array.")
    records: list[InventoryRecord] = []
    for item in records_raw:
        if not isinstance(item, dict):
            raise ValueError("Inventory records must be objects.")
        profile = item.get("profile")
        reachable = item.get("reachable")
        facts = item.get("facts", {})
        error_class = item.get("error_class")
        if (
            not isinstance(profile, str)
            or not profile.strip()
            or not isinstance(reachable, bool)
            or not isinstance(facts, dict)
            or (error_class is not None and not isinstance(error_class, str))
        ):
            raise ValueError("Inventory record has invalid fields.")
        if any(existing.profile == profile for existing in records):
            raise ValueError(f"Inventory contains duplicate profile: {profile}")
        records.append(
            InventoryRecord(
                profile=profile,
                reachable=reachable,
                facts={
                    str(key): str(value)
                    for key, value in facts.items()
                    if str(key) in _ALLOWED_FACT_KEYS
                },
                error_class=error_class,
            )
        )
    created_at = value.get("created_at")
    supplied_fingerprint = value.get("fingerprint")
    if not isinstance(created_at, str) or not isinstance(supplied_fingerprint, str):
        raise ValueError("Inventory file requires created_at and fingerprint.")
    calculated = inventory_fingerprint(records)
    if supplied_fingerprint != calculated:
        raise ValueError("Inventory fingerprint does not match its records.")
    return InventorySnapshot(
        created_at=created_at,
        records=tuple(records),
        fingerprint=supplied_fingerprint,
    )

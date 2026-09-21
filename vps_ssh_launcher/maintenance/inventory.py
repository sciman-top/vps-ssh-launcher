"""Read-only SSH inventory for the maintenance control plane."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, cast

from .. import cli
from .fingerprint import fingerprint
from .models import InventoryRecord, InventorySnapshot, MaintenancePolicy

INVENTORY_COMMAND = """set -eu
printf 'hostname=%s\n' "$(hostname 2>/dev/null || printf unknown)"
printf 'os=%s\n' "$(uname -s 2>/dev/null || printf unknown)"
if command -v docker >/dev/null 2>&1; then printf 'docker=present\n'; else printf 'docker=absent\n'; fi
xray_state=absent
if command -v xray >/dev/null 2>&1; then
  xray_state=present
elif command -v sing-box >/dev/null 2>&1; then
  xray_state=present
elif command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet xray 2>/dev/null; then
  xray_state=present
elif command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet sing-box 2>/dev/null; then
  xray_state=present
fi
printf 'xray=%s\n' "$xray_state"
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet cpa 2>/dev/null; then
  printf 'cpa=active\n'
else
  printf 'cpa=unknown\n'
fi
"""

_ALLOWED_FACT_KEYS = frozenset({"hostname", "os", "docker", "xray", "cpa"})


def parse_probe_output(
    profile: str,
    output: str,
    *,
    return_code: int = 0,
) -> InventoryRecord:
    facts: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key in _ALLOWED_FACT_KEYS and value:
            facts[key] = value[:256]
    if return_code != 0:
        return InventoryRecord(
            profile=profile,
            reachable=False,
            facts={},
            error_class="remote_command_error",
        )
    return InventoryRecord(profile=profile, reachable=True, facts=facts)


def _target_profiles(target_config: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    config = cli.load_config(target_config)
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("Target config must contain a non-empty 'profiles' object.")
    return config, cast(dict[str, Any], profiles)


def _profile_args(
    name: str,
    entry: dict[str, Any],
    *,
    target_config: Path,
    policy: MaintenancePolicy,
) -> argparse.Namespace:
    base_args = argparse.Namespace(
        password=None,
        key=None,
        allow_agent=False,
    )
    password, key = cli._resolve_auth_for_entry(
        entry,
        base_args,
        config_dir=target_config.parent,
    )
    return argparse.Namespace(
        host=cast(str, entry["host"]).strip(),
        port=cli._coerce_port(entry.get("port", 22), context=f"Profile '{name}'"),
        user=cast(str, entry["user"]).strip(),
        password=password,
        key=key,
        allow_agent=False,
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
    args = _profile_args(
        name,
        entry,
        target_config=target_config,
        policy=policy,
    )
    client = cli.connect_with_retry(args)
    try:
        return cli.exec_remote(
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
    _config, profiles = _target_profiles(target_config)
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
        cli.validate_profile(entry, name, require_auth=True)
        policy_profile = policy.profiles.get(name, {})
        if not policy_profile.get("enabled", True):
            records.append(
                InventoryRecord(
                    profile=name,
                    reachable=False,
                    error_class="disabled_by_policy",
                )
            )
            continue
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
    serialized = [record.to_dict() for record in records]
    return InventorySnapshot(
        created_at=created_at,
        records=tuple(records),
        fingerprint=fingerprint(serialized),
    )


def write_inventory(path: Path, snapshot: InventorySnapshot) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


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
            or not isinstance(reachable, bool)
            or not isinstance(facts, dict)
            or (error_class is not None and not isinstance(error_class, str))
        ):
            raise ValueError("Inventory record has invalid fields.")
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
    calculated = fingerprint([record.to_dict() for record in records])
    if supplied_fingerprint != calculated:
        raise ValueError("Inventory fingerprint does not match its records.")
    return InventorySnapshot(
        created_at=created_at,
        records=tuple(records),
        fingerprint=supplied_fingerprint,
    )

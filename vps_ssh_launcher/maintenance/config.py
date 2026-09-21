"""TOML policy loading and local path resolution."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, cast

from .fingerprint import fingerprint_without_keys
from .models import MaintenancePolicy

APP_CONFIG_DIR = "vps-ssh-launcher"
MAINTENANCE_CONFIG_FILE = "maintenance.toml"
MAINTENANCE_DB_FILE = "maintenance.db"
MAINTENANCE_RECEIPT_DIR = "maintenance-receipts"


def app_config_dir() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_CONFIG_DIR
    return Path.home() / ".config" / APP_CONFIG_DIR


def default_policy_path() -> Path:
    return app_config_dir() / MAINTENANCE_CONFIG_FILE


def default_state_path() -> Path:
    return app_config_dir() / MAINTENANCE_DB_FILE


def default_receipt_dir() -> Path:
    return app_config_dir() / MAINTENANCE_RECEIPT_DIR


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except OSError as exc:
        raise ValueError(f"Unable to read maintenance policy: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML maintenance policy: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("Maintenance policy root must be a TOML table.")
    return cast(dict[str, Any], value)


def _positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")
    return cast(int, value)


def _profile_table(value: Any, profile: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"profiles.{profile} must be a TOML table.")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"profiles.{profile}.enabled must be boolean.")
    resources = value.get("resources", {})
    if not isinstance(resources, dict):
        raise ValueError(f"profiles.{profile}.resources must be a TOML table.")
    normalized: dict[str, Any] = {"enabled": enabled, "resources": {}}
    for resource, desired in resources.items():
        if not isinstance(resource, str) or not resource.strip():
            raise ValueError("Resource names must be non-empty strings.")
        if not isinstance(desired, str) or not desired.strip():
            raise ValueError(
                f"profiles.{profile}.resources.{resource} must be a string."
            )
        normalized["resources"][resource] = desired.strip()
    return normalized


def load_policy(path: Path | None = None) -> MaintenancePolicy:
    policy_path = (path or default_policy_path()).expanduser().resolve()
    raw = _read_toml(policy_path)
    settings = raw.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("settings must be a TOML table.")
    strict = settings.get("strict_host_key_checking", True)
    if not isinstance(strict, bool):
        raise ValueError("settings.strict_host_key_checking must be boolean.")
    if not strict:
        raise ValueError(
            "settings.strict_host_key_checking must remain true for maintenance."
        )
    timeout = _positive_int(
        settings.get("command_timeout", 30),
        field_name="settings.command_timeout",
    )
    state_path = settings.get("state_path")
    receipt_dir = settings.get("receipt_dir")
    for field_name, field_value in (
        ("settings.state_path", state_path),
        ("settings.receipt_dir", receipt_dir),
    ):
        if field_value is not None and (
            not isinstance(field_value, str) or not field_value.strip()
        ):
            raise ValueError(f"{field_name} must be a non-empty string when set.")

    profiles = raw.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("profiles must be a non-empty TOML table.")
    normalized_profiles = {
        profile: _profile_table(value, profile)
        for profile, value in profiles.items()
        if isinstance(profile, str) and profile.strip()
    }
    if len(normalized_profiles) != len(profiles):
        raise ValueError("Profile names must be non-empty strings.")

    normalized = {
        "settings": {
            "strict_host_key_checking": strict,
            "command_timeout": timeout,
            "state_path": state_path,
            "receipt_dir": receipt_dir,
        },
        "profiles": normalized_profiles,
    }
    return MaintenancePolicy(
        config_path=str(policy_path),
        strict_host_key_checking=strict,
        command_timeout=timeout,
        state_path=state_path,
        receipt_dir=receipt_dir,
        profiles=normalized_profiles,
        fingerprint=fingerprint_without_keys(normalized, excluded=set()),
    )


def resolve_local_path(value: str | None, *, default: Path) -> Path:
    if value is None:
        return default
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = default.parent / path
    return path


def policy_state_path(policy: MaintenancePolicy) -> Path:
    default = Path(policy.config_path).parent / MAINTENANCE_DB_FILE
    return resolve_local_path(policy.state_path, default=default)


def policy_receipt_dir(policy: MaintenancePolicy) -> Path:
    default = Path(policy.config_path).parent / MAINTENANCE_RECEIPT_DIR
    return resolve_local_path(policy.receipt_dir, default=default)

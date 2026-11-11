"""Policy and target-config preparation shared by maintenance commands."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .. import config as target_config
from .config import load_policy
from .models import MaintenancePolicy

RUN_INTEGRATION_ENV = "VPS_SSH_LAUNCHER_RUN_INTEGRATION"

# vps-maint's own exit-code semantics (documented in maintenance_cli's module
# docstring): EXIT_OK is shared with the launcher contracts, while the not-OK
# buckets stay local to the maintenance control plane so call sites never
# repeat bare literals.
EXIT_NOT_OK = 1
EXIT_LOCAL_ERROR = 2


def policy_from_args(args: argparse.Namespace) -> MaintenancePolicy:
    path = Path(args.config).expanduser() if args.config else None
    return load_policy(path)


def require_policy_profile(policy: MaintenancePolicy, profile: str | None) -> None:
    """Reject a --profile that the maintenance policy does not cover or enable.

    Shared gate for every command that narrows itself to one policy profile:
    a probe, plan, or apply must not reach a host outside the policy.
    """
    if profile is None:
        return
    if profile not in policy.profiles:
        raise ValueError(f"Profile is not configured in maintenance policy: {profile}")
    if not policy.profiles[profile].get("enabled", True):
        raise ValueError(f"Maintenance profile is disabled: {profile}")


def target_config_from_args(args: argparse.Namespace) -> Path:
    configured = getattr(args, "target_config", None)
    resolved = target_config.resolve_entry_config_path(configured)
    if resolved is None:
        raise ValueError(
            "Target config not found. Pass --target-config or create the "
            "user-local target.json."
        )
    return resolved.resolve()


def require_integration_opt_in(args: argparse.Namespace) -> None:
    if not getattr(args, "run_integration", False):
        raise ValueError("Real SSH inventory requires explicit --run-integration.")
    if os.environ.get(RUN_INTEGRATION_ENV) != "1":
        raise ValueError(f"Real SSH inventory also requires {RUN_INTEGRATION_ENV}=1.")

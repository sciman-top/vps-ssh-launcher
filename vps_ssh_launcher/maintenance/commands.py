"""Policy and target-config preparation shared by maintenance commands."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .. import config as target_config
from .config import load_policy
from .models import MaintenancePolicy

RUN_INTEGRATION_ENV = "VPS_SSH_LAUNCHER_RUN_INTEGRATION"


def policy_from_args(args: argparse.Namespace) -> MaintenancePolicy:
    path = Path(args.config).expanduser() if args.config else None
    return load_policy(path)


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

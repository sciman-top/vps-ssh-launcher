"""CLI for the local, policy-driven VPS maintenance control plane."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import cli
from .maintenance.config import (
    load_policy,
    policy_receipt_dir,
    policy_state_path,
)
from .maintenance.inventory import (
    collect_inventory,
    load_inventory,
    write_inventory,
)
from .maintenance.models import InventorySnapshot, MaintenancePlan, MaintenancePolicy
from .maintenance.planner import build_plan
from .maintenance.receipt import write_receipt
from .maintenance.state import list_plans, load_plan, save_plan

RUN_INTEGRATION_ENV = "VPS_SSH_LAUNCHER_RUN_INTEGRATION"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vps-maint",
        description="Policy-driven, redaction-first VPS maintenance control plane",
    )
    parser.add_argument(
        "--config",
        help="TOML maintenance policy (default: user-local maintenance.toml)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON where supported",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    inventory = sub.add_parser(
        "inventory", help="Collect a fixed read-only SSH inventory"
    )
    inventory.add_argument("--target-config", help="JSON target config path")
    inventory.add_argument("--profile", help="Only inspect one policy profile")
    inventory.add_argument("--output", help="Write the redacted inventory JSON")
    inventory.add_argument(
        "--run-integration",
        action="store_true",
        help="Allow a real SSH read-only probe (also requires the opt-in env var)",
    )

    plan = sub.add_parser("plan", help="Build a deterministic plan from inventory")
    plan.add_argument("--inventory-file", help="Previously saved inventory JSON")
    plan.add_argument(
        "--live-inventory",
        action="store_true",
        help="Collect fresh SSH inventory before planning",
    )
    plan.add_argument("--target-config", help="JSON target config path")
    plan.add_argument("--profile", help="Only inspect one policy profile")
    plan.add_argument(
        "--run-integration",
        action="store_true",
        help="Allow live inventory when --live-inventory is selected",
    )
    plan.add_argument("--output", help="Write the plan JSON")

    apply = sub.add_parser(
        "apply", help="Review an existing plan at the high-risk boundary"
    )
    apply.add_argument("--plan-id", help="Stored plan id (default: newest plan)")
    apply.add_argument(
        "--yes", action="store_true", help="Acknowledge the apply boundary"
    )

    history = sub.add_parser("history", help="List local plan history")
    history.add_argument("--limit", type=int, default=20)
    return parser


def _policy_from_args(args: argparse.Namespace) -> MaintenancePolicy:
    path = Path(args.config).expanduser() if args.config else None
    return load_policy(path)


def _target_config(args: argparse.Namespace) -> Path:
    configured = getattr(args, "target_config", None)
    if configured:
        return Path(configured).expanduser().resolve()
    resolved = cli.resolve_default_config_path(cli.SOURCE_ROOT)
    if resolved is None:
        raise ValueError(
            "Target config not found. Pass --target-config or create the "
            "user-local target.json."
        )
    return resolved


def _require_integration_opt_in(args: argparse.Namespace) -> None:
    if not getattr(args, "run_integration", False):
        raise ValueError("Real SSH inventory requires explicit --run-integration.")
    if os.environ.get(RUN_INTEGRATION_ENV) != "1":
        raise ValueError(f"Real SSH inventory also requires {RUN_INTEGRATION_ENV}=1.")


def _print(value: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")
    else:
        print(value)


def _inventory_command(args: argparse.Namespace) -> int:
    _require_integration_opt_in(args)
    policy = _policy_from_args(args)
    target = _target_config(args)
    snapshot = collect_inventory(
        policy,
        target,
        profile=args.profile,
    )
    if args.output:
        write_inventory(Path(args.output), snapshot)
    result = {
        "kind": "inventory",
        "created_at": snapshot.created_at,
        "fingerprint": snapshot.fingerprint,
        "profiles": [
            {
                "profile": record.profile,
                "reachable": record.reachable,
                "error_class": record.error_class,
                "fact_keys": sorted(record.facts),
            }
            for record in snapshot.records
        ],
        "output": str(Path(args.output).expanduser()) if args.output else None,
    }
    _print(result, as_json=args.json)
    return 0 if all(record.reachable for record in snapshot.records) else 1


def _load_plan_input(args: argparse.Namespace) -> tuple[Any, InventorySnapshot]:
    policy = _policy_from_args(args)
    if args.inventory_file:
        inventory = load_inventory(Path(args.inventory_file))
    elif args.live_inventory:
        _require_integration_opt_in(args)
        inventory = collect_inventory(
            policy,
            _target_config(args),
            profile=args.profile,
        )
    else:
        raise ValueError(
            "plan requires --inventory-file, or explicit --live-inventory "
            "with integration opt-in."
        )
    return policy, inventory


def _plan_command(args: argparse.Namespace) -> int:
    policy, inventory = _load_plan_input(args)
    plan = build_plan(policy, inventory)
    save_plan(policy_state_path(policy), plan)
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
    result = plan.to_dict()
    result["state_path"] = str(policy_state_path(policy))
    result["output"] = str(Path(args.output).expanduser()) if args.output else None
    _print(result, as_json=args.json)
    return 0 if plan.status != "blocked" else 1


def _apply_command(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ValueError("apply requires explicit --yes.")
    policy = _policy_from_args(args)
    plan: MaintenancePlan = load_plan(policy_state_path(policy), args.plan_id)
    blocked = any(action.status in {"blocked", "planned"} for action in plan.actions)
    if blocked:
        outcome = "refused"
        reason = (
            "No reviewed remote adapter is admitted; apply produced no remote "
            "side effect."
        )
        code = 1
    elif any(action.status == "deferred" for action in plan.actions):
        outcome = "deferred"
        reason = "All changes remain on guarded/manual maintenance paths."
        code = 0
    else:
        outcome = "noop"
        reason = "The plan contains no remote changes."
        code = 0
    receipt = write_receipt(
        policy_receipt_dir(policy),
        plan,
        outcome=outcome,
        reason=reason,
    )
    save_plan(policy_state_path(policy), plan, receipt_path=receipt)
    result = {
        "plan_id": plan.plan_id,
        "outcome": outcome,
        "reason": reason,
        "receipt": str(receipt),
        "remote_write": False,
    }
    _print(result, as_json=args.json)
    return code


def _history_command(args: argparse.Namespace) -> int:
    policy = _policy_from_args(args)
    result = {
        "state_path": str(policy_state_path(policy)),
        "plans": list_plans(policy_state_path(policy), limit=args.limit),
    }
    _print(result, as_json=args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "inventory":
            return _inventory_command(args)
        if args.action == "plan":
            return _plan_command(args)
        if args.action == "apply":
            return _apply_command(args)
        if args.action == "history":
            return _history_command(args)
        raise ValueError(f"Unsupported action: {args.action}")
    except (OSError, ValueError) as exc:
        print(f"vps-maint error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

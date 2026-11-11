"""CLI for the local, policy-driven VPS maintenance control plane.

Exit codes: 0 success (EXIT_OK, shared with the launcher contracts),
1 structured not-OK outcome (inventory unreachable, blocked plan, a pre-write
remote apply refusal, or an unattended guard refusal), 2 unexpected local
error. These are vps-maint's own semantics and are documented here, not in
vps_ssh_launcher.contracts; the constants live in
vps_ssh_launcher.maintenance.commands next to the shared admission gate, and
the high-risk apply orchestration lives in vps_ssh_launcher.maintenance.apply.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from . import config as target_config
from .contracts import EXIT_OK
from .maintenance.apply import run_apply_locked
from .maintenance.automation import unattended_lock
from .maintenance.commands import (
    EXIT_LOCAL_ERROR,
    EXIT_NOT_OK,
    policy_from_args as _policy_from_args,
    require_integration_opt_in as _require_integration_opt_in,
    require_policy_profile as _require_policy_profile,
    target_config_from_args as _target_config,
)
from .maintenance.config import policy_lock_path, policy_state_path
from .maintenance.inventory import (
    collect_inventory,
    load_inventory,
    write_inventory,
)
from .maintenance.models import InventorySnapshot, MaintenancePolicy
from .maintenance.security import redact_evidence
from .maintenance.planner import build_plan
from .maintenance.state import (
    delete_plan_if_pristine,
    list_plans,
    save_plan_if_absent,
)
from .safe_files import atomic_write_json


def _build_inventory_parser(sub: argparse._SubParsersAction[Any]) -> None:
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


def _build_plan_parser(sub: argparse._SubParsersAction[Any]) -> None:
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


def _build_apply_parser(sub: argparse._SubParsersAction[Any]) -> None:
    apply = sub.add_parser(
        "apply", help="Review an existing plan at the high-risk boundary"
    )
    apply.add_argument("--plan-id", help="Stored plan id (default: newest plan)")
    apply.add_argument(
        "--yes", action="store_true", help="Acknowledge the apply boundary"
    )
    apply.add_argument(
        "--remote-write",
        action="store_true",
        help="Permit a reviewed adapter to write one fresh single-host target",
    )
    apply.add_argument(
        "--unattended",
        action="store_true",
        help="Use the explicit policy-gated unattended BWG authorization",
    )
    apply.add_argument(
        "--run-integration",
        action="store_true",
        help="Allow the fresh pre-apply SSH inventory",
    )
    apply.add_argument("--target-config", help="JSON target config path")
    apply.add_argument("--profile", help="Limit apply to one profile")


def _build_history_parser(sub: argparse._SubParsersAction[Any]) -> None:
    history = sub.add_parser("history", help="List local plan history")
    history.add_argument("--limit", type=int, default=20)


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
    _build_inventory_parser(sub)
    _build_plan_parser(sub)
    _build_apply_parser(sub)
    _build_history_parser(sub)
    return parser


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
    if args.profile is not None:
        # Same policy gate as the plan path: an inventory probe must not
        # reach a host that the maintenance policy does not cover.
        _require_policy_profile(policy, args.profile)
    target_config.assert_private_target_config(target)
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
    return EXIT_OK if all(r.reachable for r in snapshot.records) else EXIT_NOT_OK


def _load_plan_input(
    args: argparse.Namespace,
) -> tuple[MaintenancePolicy, InventorySnapshot, InventorySnapshot | None]:
    policy = _policy_from_args(args)
    _require_policy_profile(policy, args.profile)
    if args.inventory_file:
        inventory = load_inventory(Path(args.inventory_file))
        review_inventory = None
    elif args.live_inventory:
        _require_integration_opt_in(args)
        target = _target_config(args)
        target_config.assert_private_target_config(target)
        inventory = collect_inventory(
            policy,
            target,
            profile=args.profile,
        )
        review_inventory = inventory
    else:
        raise ValueError(
            "plan requires --inventory-file, or explicit --live-inventory "
            "with integration opt-in."
        )
    return policy, inventory, review_inventory


def _plan_command(args: argparse.Namespace) -> int:
    policy, inventory, review_inventory = _load_plan_input(args)
    plan = build_plan(policy, inventory, profile=args.profile)
    output: Path | None = None
    inventory_output: Path | None = None
    if args.output:
        output = Path(args.output).expanduser()
        _validate_plan_output_pair(output, review_inventory)
        inventory_output = output.with_name(output.stem + "-inventory.json")
    state_path = policy_state_path(policy)
    plan = save_plan_if_absent(state_path, plan)
    if output is not None:
        try:
            if inventory_output is not None:
                if review_inventory is None:
                    raise ValueError(
                        "Review inventory output requires a reviewed inventory."
                    )
                _atomic_local_json(inventory_output, review_inventory.to_dict())
            _atomic_local_json(output, plan.to_dict())
        except Exception:
            # Do not leave a failed CLI invocation's plan as the newest stored
            # entry. Never compensate a plan that acquired execution receipts.
            delete_plan_if_pristine(
                state_path,
                plan_id=plan.plan_id,
                receipt_path=None,
            )
            raise
    result = plan.to_dict()
    result["state_path"] = str(state_path)
    result["output"] = str(output) if output is not None else None
    _print(result, as_json=args.json)
    return EXIT_OK if plan.status != "blocked" else EXIT_NOT_OK


def _atomic_local_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value, replace_existing=False)


def _validate_plan_output_pair(
    output: Path, review_inventory: InventorySnapshot | None
) -> None:
    if output.exists():
        raise ValueError("Plan output already exists; choose a new plan output path.")
    if review_inventory is not None:
        inventory_output = output.with_name(output.stem + "-inventory.json")
        if inventory_output.exists():
            raise ValueError(
                "Review inventory output already exists; choose a new plan output path."
            )


def _apply_command(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ValueError("apply requires explicit --yes.")
    policy = _policy_from_args(args)
    if args.unattended and not args.remote_write:
        raise ValueError("--unattended requires --remote-write.")
    # The unattended acknowledgement guard lives in
    # authorize_unattended_apply: a disabled ack is a structured not-OK
    # refusal (exit 1 + refused receipt), never an unexpected local error.
    # Every remote write needs the same local mutex. Unattended mode adds
    # policy authorization, but a manually confirmed apply must not race an
    # unattended run (or another manually confirmed run) either.
    lock = (
        unattended_lock(policy_lock_path(policy))
        if args.remote_write
        else nullcontext()
    )
    with lock:
        result, code = run_apply_locked(args, policy)
        if result is not None:
            _print(result, as_json=args.json)
        return code


def _history_command(args: argparse.Namespace) -> int:
    policy = _policy_from_args(args)
    result = {
        "state_path": str(policy_state_path(policy)),
        "plans": list_plans(policy_state_path(policy), limit=args.limit),
    }
    _print(result, as_json=args.json)
    return EXIT_OK


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
    except Exception as exc:
        # Anything outside the typed contract (state-layer RuntimeError,
        # paramiko transport errors) still maps to the documented error exit
        # code instead of leaking an arbitrary traceback exit status.
        print(f"vps-maint error: {redact_evidence(exc)}", file=sys.stderr)
        return EXIT_LOCAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

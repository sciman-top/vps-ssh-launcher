"""CLI for the local, policy-driven VPS maintenance control plane.

Exit codes: 0 success (EXIT_OK, shared with the launcher contracts),
1 structured not-OK outcome (inventory unreachable or blocked plan),
2 unexpected local error. These are vps-maint's own semantics and are
documented here, not in vps_ssh_launcher.contracts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from . import config as target_config, connection, execution
from .contracts import EXIT_OK
from .maintenance.config import (
    load_policy,
    policy_lock_path,
    policy_receipt_dir,
    policy_state_path,
)
from .maintenance.automation import (
    authorize_unattended_apply,
    unattended_lock,
)
from .maintenance.inventory import (
    profile_args,
    collect_inventory,
    load_inventory,
    write_inventory,
)
from .maintenance.adapters import execute_action
from .maintenance.models import (
    ActionStatus,
    InventorySnapshot,
    MaintenancePlan,
    MaintenancePolicy,
)
from .maintenance.planner import build_plan
from .maintenance.receipt import write_receipt
from .maintenance.state import (
    list_plans,
    load_plan,
    record_automation_attempt,
    record_automation_outcome,
    save_plan,
    save_plan_if_absent,
)

RUN_INTEGRATION_ENV = "VPS_SSH_LAUNCHER_RUN_INTEGRATION"

# Remote adapters download an artifact, restart a service and read the result
# back, with long silent stretches (a `curl --silent` download, a container
# pull). The interactive `settings.command_timeout` (default 30s) is the wrong
# budget for that: its idle timer can fire mid-transaction and leave the local
# receipt reporting "unverified" while the remote side keeps going. Adapters
# get their own floor; a larger policy value still wins.
ADAPTER_IDLE_TIMEOUT_SECONDS = 300
ADAPTER_HARD_TIMEOUT_SECONDS = 900


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

    history = sub.add_parser("history", help="List local plan history")
    history.add_argument("--limit", type=int, default=20)
    return parser


def _policy_from_args(args: argparse.Namespace) -> MaintenancePolicy:
    path = Path(args.config).expanduser() if args.config else None
    return load_policy(path)


def _target_config(args: argparse.Namespace) -> Path:
    configured = getattr(args, "target_config", None)
    resolved = target_config.resolve_entry_config_path(configured)
    if resolved is None:
        raise ValueError(
            "Target config not found. Pass --target-config or create the "
            "user-local target.json."
        )
    return resolved.resolve()


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
    return EXIT_OK if all(record.reachable for record in snapshot.records) else 1


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
    plan = save_plan_if_absent(policy_state_path(policy), plan)
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
    return EXIT_OK if plan.status != "blocked" else 1


def _apply_command(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ValueError("apply requires explicit --yes.")
    policy = _policy_from_args(args)
    if args.unattended and not args.remote_write:
        raise ValueError("--unattended requires --remote-write.")
    if args.unattended and not policy.automation.unattended_apply:
        raise ValueError(
            "Unattended apply is disabled; set the explicit automation acknowledgement in policy."
        )
    # Every remote write needs the same local mutex. Unattended mode adds
    # policy authorization, but a manually confirmed apply must not race an
    # unattended run (or another manually confirmed run) either.
    lock = (
        unattended_lock(policy_lock_path(policy))
        if args.remote_write
        else nullcontext()
    )
    with lock:
        return _apply_command_locked(args, policy)


def _apply_command_locked(
    args: argparse.Namespace,
    policy: MaintenancePolicy,
) -> int:
    state_path = policy_state_path(policy)
    receipt_dir = policy_receipt_dir(policy)
    plan: MaintenancePlan = load_plan(state_path, args.plan_id)
    if plan.policy_fingerprint != policy.fingerprint:
        raise ValueError(
            "Stored plan policy fingerprint does not match the current policy; rebuild the plan."
        )
    blocked = any(action.status == "blocked" for action in plan.actions)
    applied = [action for action in plan.actions if action.status == "applied"]
    terminal_failure = [
        action
        for action in plan.actions
        if action.status in {"unverified", "rolled_back"}
    ]
    planned = [action for action in plan.actions if action.status == "planned"]
    if blocked:
        outcome = "refused"
        reason = (
            "The plan contains a blocked action; apply produced no remote side effect."
        )
        code = 1
    elif applied:
        outcome = "refused"
        reason = (
            "The plan contains an applied action whose remote outcome is unresolved; "
            "fresh inventory and manual review are required before retrying."
        )
        code = 1
    elif terminal_failure:
        outcome = "refused"
        reason = (
            "The plan contains an unverified or rolled-back action; fresh inventory "
            "and a new reviewed plan are required before retrying."
        )
        code = 1
    elif planned and not args.remote_write:
        outcome = "dry_run"
        reason = (
            "Planned actions were reviewed locally; pass --remote-write plus the "
            "fresh integration guards to permit a remote adapter."
        )
        code = 0
    elif planned:
        authorization = None
        attempt_recorded = False

        def _record_remote_start() -> None:
            # The pin attempt quota counts real remote attempts: local guard
            # refusals (integration opt-in, fingerprint drift, missing target)
            # raise before any SSH connection and must not burn an attempt.
            nonlocal attempt_recorded
            if authorization is None:
                return
            record_automation_attempt(
                state_path,
                profile=authorization.profile,
                resource=authorization.resource,
                pin_fingerprint=authorization.pin_fingerprint,
                plan_id=plan.plan_id,
            )
            attempt_recorded = True

        if args.unattended:
            authorization = authorize_unattended_apply(
                policy,
                plan,
                state_path,
            )
        try:
            plan, outcome, reason, code = _execute_remote_plan(
                args,
                policy,
                plan,
                state_path=state_path,
                on_remote_start=_record_remote_start,
            )
        except Exception:
            if attempt_recorded and authorization is not None:
                record_automation_outcome(
                    state_path,
                    profile=authorization.profile,
                    resource=authorization.resource,
                    pin_fingerprint=authorization.pin_fingerprint,
                    outcome="unverified",
                    plan_id=plan.plan_id,
                )
            raise
        if attempt_recorded and authorization is not None:
            record_automation_outcome(
                state_path,
                profile=authorization.profile,
                resource=authorization.resource,
                pin_fingerprint=authorization.pin_fingerprint,
                outcome=outcome,
                plan_id=plan.plan_id,
            )
    elif any(action.status == "deferred" for action in plan.actions):
        outcome = "deferred"
        reason = "All changes remain on guarded/manual maintenance paths."
        code = 0
    else:
        outcome = "noop"
        reason = "The plan contains no remote changes."
        code = 0
    receipt = write_receipt(
        receipt_dir,
        plan,
        outcome=outcome,
        reason=reason,
    )
    save_plan(state_path, plan, receipt_path=receipt)
    result = {
        "plan_id": plan.plan_id,
        "outcome": outcome,
        "reason": reason,
        "receipt": str(receipt),
        "remote_write": bool(outcome in {"verified", "rolled_back", "unverified"}),
        "actions": [
            {
                "resource": action.resource,
                "status": action.status,
                "reason": action.reason,
            }
            for action in plan.actions
        ],
    }
    _print(result, as_json=args.json)
    return code


def _apply_profile(plan: MaintenancePlan, requested: str | None) -> str:
    profiles = sorted({action.profile for action in plan.actions})
    if len(profiles) != 1:
        raise ValueError("Remote apply requires a plan containing exactly one profile.")
    profile = profiles[0]
    if requested is not None and requested != profile:
        raise ValueError("--profile does not match the stored plan profile.")
    return profile


def _updated_plan(
    plan: MaintenancePlan,
    *,
    action_index: int,
    status: ActionStatus,
    reason: str,
) -> MaintenancePlan:
    actions = list(plan.actions)
    actions[action_index] = replace(
        actions[action_index],
        status=status,
        reason=reason,
    )
    statuses = {action.status for action in actions}
    if "applied" in statuses:
        plan_status = "applied"
    elif "unverified" in statuses:
        plan_status = "unverified"
    elif "rolled_back" in statuses:
        plan_status = "rolled_back"
    elif "verified" in statuses:
        plan_status = "verified"
    else:
        plan_status = plan.status
    return replace(plan, actions=tuple(actions), status=plan_status)


def _execute_remote_plan(
    args: argparse.Namespace,
    policy: MaintenancePolicy,
    plan: MaintenancePlan,
    *,
    state_path: Path,
    on_remote_start: Callable[[], None] | None = None,
) -> tuple[MaintenancePlan, str, str, int]:
    _require_integration_opt_in(args)
    profile = _apply_profile(plan, args.profile)
    target = _target_config(args)
    fresh = collect_inventory(policy, target, profile=profile)
    if fresh.fingerprint != plan.inventory_fingerprint:
        raise ValueError(
            "Fresh inventory fingerprint does not match the stored plan; rebuild the plan."
        )
    record = fresh.records[0] if fresh.records else None
    if record is None or not record.reachable:
        raise ValueError("Fresh pre-apply inventory is not reachable.")

    config = target_config.load_config(target)
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or profile not in profiles:
        raise ValueError("Target config profile is missing for remote apply.")
    entry = profiles[profile]
    if not isinstance(entry, dict):
        raise ValueError("Target config profile must be an object.")
    target_config.validate_profile(entry, profile, require_auth=True)
    connection_args = profile_args(
        profile,
        entry,
        target_config=target,
        policy=policy,
    )
    client = connection.connect_with_retry(connection_args)
    updated = plan
    try:
        # Count an unattended attempt only after the SSH session is actually
        # established.  A failed connection is a local/network refusal and
        # must not consume the bounded quota for real remote attempts.  Keep
        # the callback inside the close boundary so a state write failure
        # cannot leak the newly opened client.
        if on_remote_start is not None:
            on_remote_start()
        for index, action in enumerate(plan.actions):
            if action.status != "planned":
                continue
            updated = _updated_plan(
                updated,
                action_index=index,
                status="applied",
                reason=(
                    "Remote adapter started; outcome remains unresolved until its "
                    "success or rollback marker is verified."
                ),
            )
            # Persist the in-flight marker before the adapter can mutate the
            # host. A process crash after the remote side effect must not leave
            # a seemingly planned action that the next invocation retries.
            save_plan(state_path, updated)
            adapter_result = execute_action(
                action,
                pins=policy.pins,
                executor=lambda command: execution.exec_remote(
                    client,
                    command,
                    command_timeout=max(
                        policy.command_timeout,
                        ADAPTER_IDLE_TIMEOUT_SECONDS,
                    ),
                    command_hard_timeout=max(
                        policy.command_timeout * 2,
                        ADAPTER_HARD_TIMEOUT_SECONDS,
                    ),
                ),
            )
            updated = _updated_plan(
                updated,
                action_index=index,
                status=adapter_result.status,
                reason=adapter_result.reason,
            )
            # Persist each adapter result immediately; the final receipt is a
            # summary, not the only durable record of the remote transition.
            save_plan(state_path, updated)
            if adapter_result.status != "verified":
                break
    finally:
        client.close()

    if any(action.status == "unverified" for action in updated.actions):
        return (
            updated,
            "unverified",
            "Remote execution did not establish a verified apply or rollback.",
            1,
        )
    if any(action.status == "rolled_back" for action in updated.actions):
        return (
            updated,
            "rolled_back",
            "Remote execution failed and the adapter verified its rollback.",
            1,
        )
    return (
        updated,
        "verified",
        "Remote execution completed with adapter readback verification.",
        0,
    )


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
        print(f"vps-maint error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

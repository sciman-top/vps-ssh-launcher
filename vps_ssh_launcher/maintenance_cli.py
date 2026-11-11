"""CLI for the local, policy-driven VPS maintenance control plane.

Exit codes: 0 success (EXIT_OK, shared with the launcher contracts),
1 structured not-OK outcome (inventory unreachable, blocked plan, a pre-write
remote apply refusal, or an unattended guard refusal), 2 unexpected local
error. These are vps-maint's own semantics and are documented here, not in
vps_ssh_launcher.contracts.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from . import config as target_config
from .contracts import EXIT_OK
from .maintenance.adapters import MaintenanceBusy
from .maintenance.config import (
    policy_lock_path,
    policy_receipt_dir,
    policy_state_path,
)
from .maintenance.automation import (
    AutomationAuthorization,
    authorize_unattended_apply,
    unattended_lock,
)
from .maintenance.commands import (
    policy_from_args as _policy_from_args,
    require_integration_opt_in as _require_integration_opt_in,
    target_config_from_args as _target_config,
)
from .maintenance.inventory import (
    collect_inventory,
    load_inventory,
    write_inventory,
)
from .maintenance.models import (
    InventorySnapshot,
    MaintenanceAction,
    MaintenancePlan,
    MaintenancePolicy,
)
from .maintenance.security import redact_evidence
from .maintenance.planner import build_plan
from .maintenance.receipt import write_receipt
from .maintenance.state import (
    delete_plan_if_pristine,
    list_plans,
    load_plan,
    record_automation_outcome,
    record_remote_start_and_mark_applied,
    refund_automation_attempt,
    restore_unattended_busy_plan,
    save_plan,
    save_plan_if_absent,
)
from .maintenance.workflow import RemotePlanRefusal, execute_remote_plan
from .safe_files import atomic_write_json


@dataclass
class ApplyExecution:
    plan: MaintenancePlan
    outcome: str
    reason: str
    code: int
    authorization: AutomationAuthorization | None = None
    attempt_recorded: bool = False

    def _record_start(self, state_path: Path) -> None:
        if self.authorization is None:
            return
        record_remote_start_and_mark_applied(
            state_path,
            profile=self.authorization.profile,
            resource=self.authorization.resource,
            pin_digest=self.authorization.pin_fingerprint,
            plan_id=self.plan.plan_id,
        )
        self.attempt_recorded = True

    @property
    def planned(self) -> list[MaintenanceAction]:
        return [action for action in self.plan.actions if action.status == "planned"]

    def record_start(self, state_path: Path) -> None:
        self._record_start(state_path)

    def record_interrupted(
        self,
        state_path: Path,
        receipt_dir: Path,
        error: BaseException,
    ) -> None:
        _record_interrupted_apply(self, state_path, receipt_dir, error)

    def record_outcome(self, state_path: Path) -> None:
        _record_apply_outcome(self, state_path)


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
        if args.profile not in policy.profiles:
            raise ValueError(
                f"Profile is not configured in maintenance policy: {args.profile}"
            )
        if not policy.profiles[args.profile].get("enabled", True):
            raise ValueError(f"Maintenance profile is disabled: {args.profile}")
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
    return EXIT_OK if all(record.reachable for record in snapshot.records) else 1


def _load_plan_input(
    args: argparse.Namespace,
) -> tuple[MaintenancePolicy, InventorySnapshot, InventorySnapshot | None]:
    policy = _policy_from_args(args)
    if args.profile is not None:
        if args.profile not in policy.profiles:
            raise ValueError(
                f"Profile is not configured in maintenance policy: {args.profile}"
            )
        if not policy.profiles[args.profile].get("enabled", True):
            raise ValueError(f"Maintenance profile is disabled: {args.profile}")
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
    return EXIT_OK if plan.status != "blocked" else 1


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
        return _apply_command_locked(args, policy)


def _apply_command_locked(
    args: argparse.Namespace,
    policy: MaintenancePolicy,
) -> int:
    state_path = policy_state_path(policy)
    receipt_dir = policy_receipt_dir(policy)
    plan: MaintenancePlan = load_plan(state_path, args.plan_id)
    try:
        if plan.policy_fingerprint != policy.fingerprint:
            raise ValueError(
                "Stored plan policy fingerprint does not match the current policy; rebuild the plan."
            )
        profile = _apply_profile(plan, args.profile)
    except ValueError as exc:
        # A stored-plan mismatch (stale policy fingerprint, wrong --profile)
        # is a structured refusal, not an unexpected local error: the plan
        # was reviewed and stored, so the refusal deserves a receipt in the
        # same exit-1 bucket as the other not-OK outcomes (12b5292 contract).
        receipt = write_receipt(receipt_dir, plan, outcome="refused", reason=str(exc))
        save_plan(
            state_path,
            plan,
            receipt_path=receipt,
            allow_invalid_root_update=True,
        )
        print(f"vps-maint refused: {exc}", file=sys.stderr)
        return 1
    outcome, reason, code = _apply_outcome(
        args, policy, plan, profile, state_path, receipt_dir
    )
    receipt = write_receipt(
        receipt_dir,
        plan,
        outcome=outcome,
        reason=redact_evidence(reason),
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


def _apply_outcome(
    args: argparse.Namespace,
    policy: MaintenancePolicy,
    plan: MaintenancePlan,
    profile: str,
    state_path: Path,
    receipt_dir: Path,
) -> tuple[str, str, int]:
    blocked = any(action.status == "blocked" for action in plan.actions)
    applied = [action for action in plan.actions if action.status == "applied"]
    terminal_failure = [
        action
        for action in plan.actions
        if action.status in {"unverified", "rolled_back"}
    ]
    planned = [action for action in plan.actions if action.status == "planned"]
    if blocked:
        return (
            "refused",
            "The plan contains a blocked action; apply produced no remote side effect.",
            1,
        )
    elif applied:
        return (
            "refused",
            "The plan contains an applied action whose remote outcome is unresolved; "
            "fresh inventory and manual review are required before retrying.",
            1,
        )
    elif terminal_failure:
        return (
            "refused",
            "The plan contains an unverified or rolled-back action; fresh inventory "
            "and a new reviewed plan are required before retrying.",
            1,
        )
    elif planned and not args.remote_write:
        return (
            "dry_run",
            "Planned actions were reviewed locally; pass --remote-write plus the "
            "fresh integration guards to permit a remote adapter.",
            0,
        )
    elif planned:
        guard_refusal: str | None = None
        authorization = None
        if args.unattended:
            try:
                authorization = authorize_unattended_apply(
                    policy,
                    plan,
                    state_path,
                )
            except ValueError as exc:
                # A guard refusal (window, plan age, cooldown, attempt quota)
                # is a structured not-OK outcome, not an unexpected local
                # error: report it like a blocked plan (exit 1 + reason).
                guard_refusal = str(exc)
        if guard_refusal is None:
            return _execute_apply(
                args,
                policy,
                plan,
                profile,
                state_path,
                receipt_dir,
                authorization,
            )
        return "refused", guard_refusal, 1
    elif any(action.status == "deferred" for action in plan.actions):
        return "deferred", "All changes remain on guarded/manual maintenance paths.", 0
    return "noop", "The plan contains no remote changes.", 0


def _execute_apply(
    args: argparse.Namespace,
    policy: MaintenancePolicy,
    plan: MaintenancePlan,
    profile: str,
    state_path: Path,
    receipt_dir: Path,
    authorization: AutomationAuthorization | None,
) -> tuple[str, str, int]:
    execution = ApplyExecution(
        plan=plan, outcome="", reason="", code=0, authorization=authorization
    )

    def record_remote_start() -> None:
        # The pin attempt quota counts real remote attempts: local guard
        # refusals raise before SSH and must not burn an attempt.
        execution.record_start(state_path)

    try:
        (
            execution.plan,
            execution.outcome,
            execution.reason,
            execution.code,
        ) = _execute_remote_plan(
            args,
            policy,
            execution.plan,
            state_path=state_path,
            profile=profile,
            on_remote_start=record_remote_start,
        )
    except RemotePlanRefusal as exc:
        execution.outcome, execution.reason, execution.code = "refused", str(exc), 1
    except MaintenanceBusy as exc:
        _reconcile_busy_apply(execution, state_path)
        execution.outcome, execution.reason, execution.code = "deferred", str(exc), 0
    except Exception as exc:
        execution.record_interrupted(state_path, receipt_dir, exc)
        raise
    execution.record_outcome(state_path)
    return execution.outcome, execution.reason, execution.code


def _reconcile_busy_apply(execution: ApplyExecution, state_path: Path) -> None:
    # Never infer safety from the pre-execution plan. Restore only the exact
    # action that received MAINTENANCE_BUSY, then refund its unattended attempt.
    planned = execution.planned
    authorization = execution.authorization
    busy_profile = planned[0].profile
    busy_resource = planned[0].resource
    if authorization is not None:
        busy_profile = authorization.profile
        busy_resource = authorization.resource
    execution.plan = _restore_busy_plan(
        state_path,
        execution.plan.plan_id,
        busy_profile,
        busy_resource,
        preserve_refund=execution.attempt_recorded,
    )
    if (
        execution.attempt_recorded
        and authorization is not None
        and execution.plan.may_refund_unattended_attempt
    ):
        _refund_unattended_attempt(state_path, authorization, execution.plan.plan_id)
        execution.plan = replace(execution.plan, may_refund_unattended_attempt=False)
        execution.attempt_recorded = False


def _restore_busy_plan(
    state_path: Path,
    plan_id: str,
    profile: str,
    resource: str,
    *,
    preserve_refund: bool,
) -> MaintenancePlan:
    return restore_unattended_busy_plan(
        state_path,
        plan_id=plan_id,
        action_profile=profile,
        action_resource=resource,
        preserve_refund=preserve_refund,
    )


def _refund_unattended_attempt(
    state_path: Path, authorization: AutomationAuthorization, plan_id: str
) -> None:
    refund_automation_attempt(
        state_path,
        profile=authorization.profile,
        resource=authorization.resource,
        pin_fingerprint=authorization.pin_fingerprint,
        plan_id=plan_id,
    )


def _record_interrupted_apply(
    execution: ApplyExecution,
    state_path: Path,
    receipt_dir: Path,
    error: BaseException,
) -> None:
    authorization = execution.authorization
    if execution.attempt_recorded and authorization is not None:
        _record_unverified_attempt(state_path, authorization, execution.plan.plan_id)
    _record_unverified_receipt(execution.plan, state_path, receipt_dir, error)


def _record_unverified_attempt(
    state_path: Path,
    authorization: AutomationAuthorization,
    plan_id: str,
) -> None:
    record_automation_outcome(
        state_path,
        profile=authorization.profile,
        resource=authorization.resource,
        pin_fingerprint=authorization.pin_fingerprint,
        outcome="unverified",
        plan_id=plan_id,
    )


def _record_unverified_receipt(
    plan: MaintenancePlan,
    state_path: Path,
    receipt_dir: Path,
    error: BaseException,
) -> None:
    try:
        receipt = write_receipt(
            receipt_dir,
            plan,
            outcome="unverified",
            reason=f"apply interrupted: {type(error).__name__}",
        )
        save_plan(state_path, plan, receipt_path=receipt)
    except Exception as receipt_exc:
        print(
            "vps-maint warning: unverified receipt write failed: "
            f"{type(receipt_exc).__name__}",
            file=sys.stderr,
        )


def _record_apply_outcome(execution: ApplyExecution, state_path: Path) -> None:
    authorization = execution.authorization
    if not execution.attempt_recorded or authorization is None:
        return
    record_automation_outcome(
        state_path,
        profile=authorization.profile,
        resource=authorization.resource,
        pin_fingerprint=authorization.pin_fingerprint,
        outcome=execution.outcome,
        plan_id=execution.plan.plan_id,
    )


def _apply_command_outcome(
    args: argparse.Namespace,
    policy: MaintenancePolicy,
    plan: MaintenancePlan,
    profile: str,
    state_path: Path,
) -> tuple[str, str, int]:
    outcome, reason, code = _apply_outcome(
        args,
        policy,
        plan,
        profile,
        state_path,
        policy_receipt_dir(policy),
    )
    return outcome, reason, code


def _write_unverified_receipt(
    plan: MaintenancePlan,
    state_path: Path,
    receipt_dir: Path,
    error: BaseException,
) -> None:
    _record_unverified_receipt(plan, state_path, receipt_dir, error)


def _apply_profile(plan: MaintenancePlan, requested: str | None) -> str:
    profiles = sorted({action.profile for action in plan.actions})
    if len(profiles) != 1:
        raise ValueError("Remote apply requires a plan containing exactly one profile.")
    profile = profiles[0]
    if requested is not None and requested != profile:
        raise ValueError("--profile does not match the stored plan profile.")
    return profile


def _execute_remote_plan(
    args: argparse.Namespace,
    policy: MaintenancePolicy,
    plan: MaintenancePlan,
    *,
    state_path: Path,
    profile: str,
    on_remote_start: Callable[[], None] | None = None,
) -> tuple[MaintenancePlan, str, str, int]:
    try:
        _require_integration_opt_in(args)
        target = _target_config(args)
    except ValueError as exc:
        raise RemotePlanRefusal(str(exc)) from exc
    return execute_remote_plan(
        policy,
        plan,
        target=target,
        state_path=state_path,
        profile=profile,
        on_remote_start=on_remote_start,
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
        print(f"vps-maint error: {redact_evidence(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

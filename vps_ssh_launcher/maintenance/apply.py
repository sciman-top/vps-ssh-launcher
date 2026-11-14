"""Apply orchestration for the vps-maint maintenance control plane.

Extracted from maintenance_cli along the same boundary as 77b265d: the CLI
shell and the high-risk apply state machine evolve on separate axes. The
apply contract is preserved verbatim — structured refusals land in the same
exit-1 bucket, busy outcomes reconcile only the exact busy action, and an
interrupted apply always leaves an unverified receipt behind.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from ..contracts import EXIT_OK
from .adapters import MaintenanceBusy
from .automation import AutomationAuthorization, authorize_unattended_apply
from .commands import (
    EXIT_NOT_OK,
    require_integration_opt_in,
    target_config_from_args,
)
from .config import policy_receipt_dir, policy_state_path
from .models import MaintenanceAction, MaintenancePlan, MaintenancePolicy
from .receipt import write_receipt
from .security import redact_evidence
from .state import (
    load_plan,
    record_automation_outcome,
    record_remote_start_and_mark_applied,
    refund_automation_attempt,
    restore_unattended_busy_plan,
    save_plan,
)
from .workflow import RemotePlanRefusal, execute_remote_plan


@dataclass
class ApplyExecution:
    plan: MaintenancePlan
    outcome: str
    reason: str
    code: int
    authorization: AutomationAuthorization | None = None
    attempt_recorded: bool = False

    @property
    def planned(self) -> list[MaintenanceAction]:
        return [action for action in self.plan.actions if action.status == "planned"]

    def record_start(self, state_path: Path) -> None:
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

    def record_outcome(self, state_path: Path) -> None:
        _record_apply_outcome(self, state_path)


def run_apply_locked(
    args: argparse.Namespace,
    policy: MaintenancePolicy,
) -> tuple[dict[str, Any] | None, int]:
    """Run one stored plan through the apply state machine under the caller's lock.

    Returns the machine-readable result dict and the process exit code; the
    CLI shell owns presentation (``_print``). A structured early refusal
    returns ``None`` as the result: the caller prints the refused line to
    stderr instead of a result payload.
    """
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
        return None, EXIT_NOT_OK
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
    return result, code


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
        if action.status in {"unverified", "rolled_back", "refused_before_mutation"}
    ]
    planned = [action for action in plan.actions if action.status == "planned"]
    if blocked:
        return (
            "refused",
            "The plan contains a blocked action; apply produced no remote side effect.",
            EXIT_NOT_OK,
        )
    elif applied:
        return (
            "refused",
            "The plan contains an applied action whose remote outcome is unresolved; "
            "fresh inventory and manual review are required before retrying.",
            EXIT_NOT_OK,
        )
    elif terminal_failure:
        return (
            "refused",
            "The plan contains an unverified, rolled-back, or refused-before-mutation "
            "action; fresh inventory and a new reviewed plan are required before retrying.",
            EXIT_NOT_OK,
        )
    elif planned and not args.remote_write:
        return (
            "dry_run",
            "Planned actions were reviewed locally; pass --remote-write plus the "
            "fresh integration guards to permit a remote adapter.",
            EXIT_OK,
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
        return "refused", guard_refusal, EXIT_NOT_OK
    elif any(action.status == "deferred" for action in plan.actions):
        return (
            "deferred",
            "All changes remain on guarded/manual maintenance paths.",
            EXIT_OK,
        )
    return "noop", "The plan contains no remote changes.", EXIT_OK


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
        execution.outcome, execution.reason, execution.code = (
            "refused",
            str(exc),
            EXIT_NOT_OK,
        )
    except MaintenanceBusy as exc:
        _reconcile_busy_apply(execution, state_path)
        execution.outcome, execution.reason, execution.code = (
            "deferred",
            str(exc),
            EXIT_OK,
        )
    except Exception as exc:
        _record_interrupted_apply(execution, state_path, receipt_dir, exc)
        raise
    execution.record_outcome(state_path)
    return execution.outcome, execution.reason, execution.code


def _reconcile_busy_apply(execution: ApplyExecution, state_path: Path) -> None:
    # Never infer safety from the pre-execution plan. Restore only the exact
    # action that received MAINTENANCE_BUSY, then refund its unattended attempt.
    authorization = execution.authorization
    # An unattended run authorizes exactly one action; a manually confirmed run
    # falls back to the planned action the apply already required.
    source = authorization or next(iter(execution.planned), None)
    if source is None:
        raise ValueError("Busy apply has no action to reconcile.")
    busy_profile, busy_resource = source.profile, source.resource
    execution.plan = restore_unattended_busy_plan(
        state_path,
        plan_id=execution.plan.plan_id,
        action_profile=busy_profile,
        action_resource=busy_resource,
        preserve_refund=execution.attempt_recorded,
    )
    if (
        execution.attempt_recorded
        and authorization is not None
        and execution.plan.may_refund_unattended_attempt
    ):
        refund_automation_attempt(
            state_path,
            profile=authorization.profile,
            resource=authorization.resource,
            pin_fingerprint=authorization.pin_fingerprint,
            plan_id=execution.plan.plan_id,
        )
        execution.plan = replace(execution.plan, may_refund_unattended_attempt=False)
        execution.attempt_recorded = False


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
        require_integration_opt_in(args)
        target = target_config_from_args(args)
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

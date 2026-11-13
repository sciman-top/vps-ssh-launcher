"""Contract tests for the WorkBuddy 6008 classification.

2026-11-14 revision: 6008 ("Credits exhausted") is the burnout signature of
the UTC+8 daily free-credit allowance -- a window-class signal that self-heals
at the day boundary (the 2026-11-11 census showed CN balances intact; a
CN-credential single-shot 200 landed minutes after the boundary with zero
account actions). It therefore rides the capacity ladder beside the 6004
family instead of latching the durable `account_credits_exhausted` hold, whose
operator-review gate manufactured the false "all 14 credentials dead, purchase
add-on" verdict. The extraction shapes stay pinned so the code cannot fall
back to the generic missing-deadline classification invisibly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ADMISSION_PATH = (
    Path(__file__).resolve().parent.parent / "scripts/remote/cpa-admission.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cpa_admission_6008", _ADMISSION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("admission module spec unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def admission() -> ModuleType:
    return _load()


CREDITS_WRAPPED = (
    b'upstream 429: {"code":6008,"msg":"Credits exhausted. Please visit the link"}'
)
CREDITS_BARE = b'{"code":6008,"msg":"Credits exhausted. Please visit the link"}'
CREDITS_ENVELOPE = b'{"error":{"code":6008,"message":"Credits exhausted"}}'


def test_6008_is_extracted_from_the_cpa_prose_wrapper(admission: ModuleType) -> None:
    """CPA carries the provider code inside `upstream 429: {json}` prose."""
    errors = admission.protocol_errors(CREDITS_WRAPPED, "workbuddy-oauth")
    assert errors, "the wrapped 6008 must be surfaced at all"
    assert errors[0]["code"] == 6008


@pytest.mark.parametrize("body", [CREDITS_WRAPPED, CREDITS_BARE, CREDITS_ENVELOPE])
def test_6008_rides_the_capacity_ladder_without_a_hold(
    admission: ModuleType, body: bytes
) -> None:
    hold, delay = admission.oauth_risk_signal("workbuddy-oauth", 429, body)
    assert hold is None
    assert delay is None


@pytest.mark.parametrize("known_retry", [False, True])
def test_6008_stays_window_class_with_or_without_a_deadline(
    admission: ModuleType, known_retry: bool
) -> None:
    """A daily-allowance window is transient with or without an advertised
    deadline; neither shape may resurrect the durable hold."""
    hold, _ = admission.oauth_risk_signal(
        "workbuddy-oauth", 429, CREDITS_WRAPPED, known_retry=known_retry
    )
    assert hold is None


def test_6008_does_not_leak_to_other_lanes(admission: ModuleType) -> None:
    """Only the WorkBuddy lane owns this provider code."""
    for lane in ("chatgpt-oauth", "claude-oauth", "antigravity-oauth"):
        hold, _ = admission.oauth_risk_signal(lane, 429, CREDITS_WRAPPED)
        assert hold is None, lane


@pytest.mark.parametrize(
    "body",
    [
        b'upstream 429: {"code":6004,"msg":"model rate limit"}',
        b'upstream 429: {"code":14003,"msg":"Model busy"}',
        b'upstream 429: {"code":6003,"msg":"rate limited"}',
    ],
)
def test_neighbouring_codes_keep_their_existing_meaning(
    admission: ModuleType, body: bytes
) -> None:
    """6004/14003 must not be reclassified as billing; they stay retryable."""
    hold, _ = admission.oauth_risk_signal("workbuddy-oauth", 429, body)
    assert hold != "account_credits_exhausted"


def test_zero_cost_models_are_declared_in_lane_config(admission: ModuleType) -> None:
    """hy4free is served at x0.00 credits, so billing cannot gate it.

    The list is lane configuration, not a code constant: which models are free
    is provider catalog data that moves without a code change.
    """
    loaded = admission.load_config(
        Path(__file__).resolve().parent.parent / "scripts/remote/cpa-admission.json"
    )
    lane = next(item for item in loaded["lanes"] if item["name"] == "workbuddy-oauth")
    assert "hy4free" in lane["zero_cost_models"]


def test_zero_cost_models_default_to_empty_when_absent(admission: ModuleType) -> None:
    """An older projected JSON keeps the pre-existing lane-wide behavior."""
    lane = admission.LaneState(
        {
            "name": "workbuddy-oauth",
            "max_inflight": 1,
            "max_pending": 1,
            "queue_timeout_seconds": 1,
            "cooldown_schedule_seconds": [60, 120],
            "retry_after_max_seconds": 86400,
            "heartbeat_interval_seconds": 15,
        }
    )
    assert lane._zero_cost_models == frozenset()


def test_credits_hold_is_an_accepted_persisted_reason(
    admission: ModuleType, tmp_path: Path
) -> None:
    """The new reason must load from disk, or the service refuses to start."""
    state = tmp_path / "workbuddy-risk.json"
    state.write_text(
        '{"version": 1, "not_before": 0, "hold_reason": "account_credits_exhausted"}',
        encoding="utf-8",
    )
    lane = admission.LaneState(
        {
            "name": "workbuddy-oauth",
            "max_inflight": 1,
            "max_pending": 1,
            "queue_timeout_seconds": 1,
            "cooldown_schedule_seconds": [60, 120],
            "retry_after_max_seconds": 86400,
            "heartbeat_interval_seconds": 15,
        },
        state_path=state,
    )
    assert lane._manual_hold == "account_credits_exhausted"

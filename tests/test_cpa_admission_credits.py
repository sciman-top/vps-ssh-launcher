"""Contract tests for the WorkBuddy 6008 credits-exhausted classification.

6008 is a billing state on one credential, not a rate window. Before this
change it was invisible to `protocol_errors` (which only unwrapped 11140 and
6003/6004), fell through to the generic `retry_window_review_required`
fallback, and then gated every model on the lane -- including the zero-cost
`hy4free`. These tests pin the classification and the narrowed scope.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ADMISSION_PATH = (
    Path(__file__).resolve().parent.parent / "scripts/remote/cpa-admission.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("cpa_admission_6008", _ADMISSION_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def admission():
    return _load()


CREDITS_WRAPPED = (
    b'upstream 429: {"code":6008,"msg":"Credits exhausted. Please visit the link"}'
)
CREDITS_BARE = b'{"code":6008,"msg":"Credits exhausted. Please visit the link"}'
CREDITS_ENVELOPE = b'{"error":{"code":6008,"message":"Credits exhausted"}}'


def test_6008_is_extracted_from_the_cpa_prose_wrapper(admission):
    """CPA carries the provider code inside `upstream 429: {json}` prose."""
    errors = admission.protocol_errors(CREDITS_WRAPPED, "workbuddy-oauth")
    assert errors, "the wrapped 6008 must be surfaced at all"
    assert errors[0]["code"] == 6008


@pytest.mark.parametrize("body", [CREDITS_WRAPPED, CREDITS_BARE, CREDITS_ENVELOPE])
def test_6008_classifies_as_credits_exhausted(admission, body):
    hold, delay = admission.oauth_risk_signal("workbuddy-oauth", 429, body)
    assert hold == "account_credits_exhausted"
    assert delay is None


@pytest.mark.parametrize("known_retry", [False, True])
def test_6008_classification_ignores_known_retry(admission, known_retry):
    """A billing denial is not made recoverable by an advertised deadline."""
    hold, _ = admission.oauth_risk_signal(
        "workbuddy-oauth", 429, CREDITS_WRAPPED, known_retry=known_retry
    )
    assert hold == "account_credits_exhausted"


def test_6008_does_not_leak_to_other_lanes(admission):
    """Only the WorkBuddy lane owns this provider code."""
    for lane in ("chatgpt-oauth", "claude-oauth", "antigravity-oauth"):
        hold, _ = admission.oauth_risk_signal(lane, 429, CREDITS_WRAPPED)
        assert hold != "account_credits_exhausted", lane


@pytest.mark.parametrize(
    "body",
    [
        b'upstream 429: {"code":6004,"msg":"model rate limit"}',
        b'upstream 429: {"code":14003,"msg":"Model busy"}',
        b'upstream 429: {"code":6003,"msg":"rate limited"}',
    ],
)
def test_neighbouring_codes_keep_their_existing_meaning(admission, body):
    """6004/14003 must not be reclassified as billing; they stay retryable."""
    hold, _ = admission.oauth_risk_signal("workbuddy-oauth", 429, body)
    assert hold != "account_credits_exhausted"


def test_zero_cost_models_are_declared(admission):
    """hy4free is served at x0.00 credits, so billing cannot gate it."""
    assert "hy4free" in admission.ZERO_COST_MODELS


def test_credits_hold_is_an_accepted_persisted_reason(admission, tmp_path):
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

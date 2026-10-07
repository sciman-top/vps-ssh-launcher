from __future__ import annotations

import json
import http.client
import hashlib
import logging
import runpy
import socket
import socketserver
import struct
import threading
import time
import pytest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from cpa_catalog_expectations import ADMISSION_MODEL_LANES


MODULE = runpy.run_path(
    str(Path(__file__).parent / "scripts" / "remote" / "cpa-admission.py")
)
LaneState = cast(Any, MODULE["LaneState"])
AdmissionProxy = cast(Any, MODULE["AdmissionProxy"])
AdmissionServer = cast(Any, MODULE["Server"])
is_capacity_response = cast(Any, MODULE["is_capacity_response"])
load_config = cast(Any, MODULE["load_config"])
parse_retry_after = cast(Any, MODULE["parse_retry_after"])
requested_lane = cast(Any, MODULE["requested_lane"])
requested_model = cast(Any, MODULE["requested_model"])


def config() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        load_config(
            Path(__file__).parent / "scripts" / "remote" / "cpa-admission.json"
        ),
    )


def lane(loaded: dict[str, Any], name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        next(item for item in loaded["lanes"] if item["name"] == name),
    )


def test_config_freezes_three_shared_official_account_lanes() -> None:
    loaded = config()
    assert loaded["listen_host"] == "127.0.0.1"
    assert loaded["listen_port"] == 8318
    assert loaded["upstream_port"] == 8317
    assert loaded["retry_after_max_seconds"] == 86400
    assert set(loaded["model_lanes"]) == ADMISSION_MODEL_LANES
    assert [item["name"] for item in loaded["lanes"]] == [
        "chatgpt-oauth",
        "zhipu-coding-plan",
        "deepseek-official",
    ]
    expected_max_inflight = {
        "chatgpt-oauth": 2,
        "zhipu-coding-plan": 3,
        "deepseek-official": 3,
    }
    for loaded_lane in loaded["lanes"]:
        # The OAuth lane is one shared subscription account, so its bound is
        # deliberately small -- but not one: a turn runs 39-216s upstream, and
        # single-flight made every concurrent request wait out the whole turn
        # and then fail at the queue budget. API-key lanes have independent
        # consumption and keep their own bound.
        assert loaded_lane["max_inflight"] == expected_max_inflight[loaded_lane["name"]]
        assert loaded_lane["max_pending"] == 4
        # The queue budget must outlast a typical upstream turn, not just its
        # first few seconds.
        assert loaded_lane["queue_timeout_seconds"] == 120
        assert loaded_lane["cooldown_schedule_seconds"] == (
            60,
            120,
            240,
            480,
            900,
        )
        assert loaded_lane["early_probe_interval_seconds"] == 10
    health = AdmissionProxy(loaded).health()
    assert health["retry_after_max_seconds"] == 86400
    assert health["retired_readers"] == 0
    assert health["retired_readers_total"] == 0
    assert health["lanes"]["chatgpt-oauth"]["max_inflight"] == 2
    assert health["lanes"]["zhipu-coding-plan"]["max_inflight"] == 3


def test_oauth_lane_queues_a_concurrent_turn_instead_of_opening_parallel_upstream_calls() -> (
    None
):
    """The bound, not one, is what must queue: a turn takes 39-216s upstream.

    Single-flight starved every concurrent request for a whole turn and then
    rejected it at the 120s queue budget, which is why the bound is now the
    lane's declared `max_inflight` rather than a hardcoded 1.
    """

    loaded = config()
    state = LaneState(lane(loaded, "chatgpt-oauth"))
    bound = lane(loaded, "chatgpt-oauth")["max_inflight"]
    assert bound == 2

    held = [state.acquire() for _ in range(bound)]
    assert all(lease.admitted for lease in held)
    assert state.inflight == bound

    result: list[Any] = []

    def wait_for_slot() -> None:
        result.append(state.acquire())

    waiter = threading.Thread(target=wait_for_slot)
    waiter.start()
    deadline = time.monotonic() + 2
    while state.pending != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.pending == 1
    assert state.inflight == bound
    assert not result

    state.release(held.pop(), capacity_error=False, retry_after=None)
    waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert len(result) == 1
    assert result[0].admitted
    assert state.inflight == bound
    for lease in held + result:
        state.release(lease, capacity_error=False, retry_after=None)


def test_policy_accepts_admission_contract_and_rejects_lane_drift() -> None:
    config_path = Path(__file__).parent / "scripts" / "remote" / "cpa-admission.json"
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    manifest = json.loads(
        (
            Path(__file__).parent / "scripts" / "remote" / "cpa_provider_routes.json"
        ).read_text(encoding="utf-8")
    )
    policy = runpy.run_path(
        str(Path(__file__).parent / "scripts" / "remote" / "cpa_policy.py")
    )
    assert policy["_admission_config_issues"](manifest, raw_config) == []

    drifted = json.loads(json.dumps(raw_config))
    drifted["lanes"][0]["models"].append("gpt-6-astra")
    issues = policy["_admission_config_issues"](manifest, drifted)
    assert any("admission lane 'chatgpt-oauth' models=" in issue for issue in issues)

    drifted_interval = json.loads(json.dumps(raw_config))
    drifted_interval["early_probe_interval_seconds"] = 0
    issues = policy["_admission_config_issues"](manifest, drifted_interval)
    assert any("early_probe_interval_seconds" in issue for issue in issues)

    drifted_interval["early_probe_interval_seconds"] = "60"
    issues = policy["_admission_config_issues"](manifest, drifted_interval)
    assert any("early_probe_interval_seconds" in issue for issue in issues)


def test_requested_lane_only_admits_shared_generation_routes() -> None:
    loaded = config()
    assert requested_lane(
        "/v1/responses",
        b'{"model":"gpt-6-luna","input":"hello"}',
        loaded,
    ) == ("chatgpt-oauth", "gpt-6-luna")
    assert requested_lane(
        "/v1/chat/completions",
        b'{"model":"GLM-5.3-FLASH","messages":[]}',
        loaded,
    ) == ("zhipu-coding-plan", "glm-5.3-flash")
    assert requested_lane(
        "/v1/responses",
        b'{"model":"deepseek-flash","input":"hello"}',
        loaded,
    ) == ("deepseek-official", "deepseek-flash")
    assert requested_lane("/v1/models", b'{"model":"gpt-6-luna"}', loaded) is None
    assert (
        requested_lane(
            "/v1/responses",
            b'{"model":"gpt-6.1-sol-91","input":"hello"}',
            loaded,
        )
        is None
    )
    assert requested_lane("/v1/responses", b"not-json", loaded) is None


def test_requested_model_names_pass_through_models_for_attribution() -> None:
    """A model outside every lane must still be attributable in the journal.

    `requested_lane` deliberately returns None for pass-through traffic, so the
    log lines used to collapse every such request into `model=other`. That hid
    which model a burst of pass-through 5xx belonged to, which is the only
    question such a burst raises.
    """

    assert (
        requested_model("/v1/responses", b'{"model":"gpt-6-sol-91","input":"hello"}')
        == "gpt-6-sol-91"
    )
    # Normalisation is shared with lane matching, so casing cannot fork the two.
    assert (
        requested_model(
            "/v1/chat/completions", b'{"model":"GLM-5.3-FLASH","messages":[]}'
        )
        == "glm-5.3-flash"
    )
    # Unparseable and unnameable bodies stay unattributable rather than guessed.
    assert requested_model("/v1/responses", b"not-json") is None
    assert requested_model("/v1/responses", b'{"model":"   "}') is None
    assert requested_model("/v1/responses", b'{"model":42}') is None
    assert requested_model("/v1/models", b'{"model":"gpt-6-luna"}') is None
    assert requested_model("/v1/responses", b"") is None


def test_capacity_classifier_uses_status_and_markers_without_rewriting() -> None:
    loaded = config()
    loaded_lane = lane(loaded, "deepseek-official")
    assert is_capacity_response(503, b"", None, loaded_lane)
    chatgpt_lane = lane(loaded, "chatgpt-oauth")
    assert is_capacity_response(
        200, b'{"error":"Selected model is at capacity"}', None, chatgpt_lane
    )
    assert is_capacity_response(429, b"upstream", "7", loaded_lane)
    assert not is_capacity_response(
        500, b'{"error":"invalid request"}', None, loaded_lane
    )


def test_retry_after_accepts_seconds_and_http_date() -> None:
    assert parse_retry_after("12") == 12
    assert parse_retry_after("0") == 1
    now = datetime.now(timezone.utc).timestamp()
    future = datetime.now(timezone.utc) + timedelta(seconds=4)
    value = parse_retry_after(future.strftime("%a, %d %b %Y %H:%M:%S GMT"), now)
    assert value in {3, 4}
    assert parse_retry_after("not-a-date") is None


def test_lane_absorbs_a_lone_blip_and_opens_after_a_proven_streak() -> None:
    # A single upstream blip must not black out the lane. The upstream
    # advertises transient `server_is_overloaded` 503s that clear within
    # seconds, so the breaker waits for a consecutive streak before opening.
    loaded = config()
    # A cooldown shorter than the queue budget is now held rather than bounced,
    # so pin the budget low here to keep exercising the outright-refusal path.
    state = LaneState({**lane(loaded, "chatgpt-oauth"), "queue_timeout_seconds": 5})

    first = state.acquire()
    assert first.admitted
    state.release(first, capacity_error=True, retry_after=None)
    after_blip = state.acquire()
    assert after_blip.admitted
    assert state.snapshot()["cooldown_remaining"] == 0

    # The second consecutive failure is a proven outage: the breaker opens.
    state.release(after_blip, capacity_error=True, retry_after=None)
    blocked = state.acquire()
    assert not blocked.admitted
    assert blocked.reason == "cooldown"
    assert blocked.retry_after >= 1

    state.open_until = time.monotonic() - 1
    probe = state.acquire()
    assert probe.admitted and probe.probe
    queued: list[Any] = []
    waiter = threading.Thread(target=lambda: queued.append(state.acquire()))
    waiter.start()
    deadline = time.monotonic() + 2
    while state.pending != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.pending == 1
    assert not queued
    state.release(probe, capacity_error=False, retry_after=None)
    waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert len(queued) == 1
    recovered = queued[0]
    assert recovered.admitted and not recovered.probe
    state.release(recovered, capacity_error=False, retry_after=None)


def test_lane_resets_the_failure_streak_after_a_success() -> None:
    # A success between two failures means the outage never materialised, so
    # the streak restarts and the breaker stays shut.
    loaded = config()
    state = LaneState(lane(loaded, "chatgpt-oauth"))

    first = state.acquire()
    state.release(first, capacity_error=True, retry_after=None)
    second = state.acquire()
    assert second.admitted
    state.release(second, capacity_error=False, retry_after=None)
    assert state.snapshot()["failure_streak"] == 0

    third = state.acquire()
    state.release(third, capacity_error=True, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 0


def test_upstream_retry_after_opens_the_breaker_on_first_failure(
    monkeypatch: Any,
) -> None:
    # An upstream that advertises Retry-After is explicitly telling us to back
    # off, so honour it immediately instead of waiting for the streak.
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded = config()
    state = LaneState(lane(loaded, "deepseek-official"))
    lease = state.acquire()
    assert lease.admitted
    state.release(lease, capacity_error=True, retry_after=45)
    snapshot = state.snapshot()
    assert snapshot["failure_streak"] == 1
    assert 1 <= snapshot["cooldown_remaining"] <= 45

    # A cooldown longer than the queue budget cannot be waited out, so the
    # arrival is refused outright with the advertised window.
    refused_state = LaneState(
        {**lane(loaded, "deepseek-official"), "queue_timeout_seconds": 1}
    )
    refused_state.open_until = clock[0] + 45
    refused_state.failure_streak = 1
    refused_state._next_probe_at = clock[0] + 1e9
    refused = refused_state.acquire()
    assert not refused.admitted
    assert refused.reason == "cooldown"
    assert refused.retry_after == 45

    # A cooldown that fits the budget is held instead of bounced: the arrival
    # waits the advertised backoff out and is then admitted as the recovery
    # probe, so the client never receives a 429 it cannot wait out.
    held = LaneState(lane(loaded, "deepseek-official"))
    held.open_until = clock[0] + 45
    held.failure_streak = 1
    held._next_probe_at = clock[0] + 1e9
    original_wait = held._condition.wait

    def advance(timeout: float | None = None) -> bool:
        clock[0] = 145.0  # the advertised window has passed
        held._next_probe_at = 0.0
        return True

    held._condition.wait = advance
    try:
        admitted = held.acquire()
    finally:
        held._condition.wait = original_wait
    assert admitted.admitted and admitted.probe
    assert admitted.waited_ms >= 45_000
    assert held.pending == 0


def test_cooldown_holds_a_fitting_arrival_instead_of_bouncing_it(
    monkeypatch: Any,
) -> None:
    """A cooldown shorter than the queue budget must not become a client 429.

    The observed client does not wait out a Retry-After -- it fails the whole
    turn -- so the gateway holds the request and sends it once the window
    opens. That protects the upstream exactly as much as the advertised
    backoff would, without spending the client's retry budget.
    """
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    state.open_until = clock[0] + 40
    state._next_probe_at = clock[0] + 1e9
    state.failure_streak = 1
    original_wait = state._condition.wait

    def advance(timeout: float | None = None) -> bool:
        clock[0] = 140.0  # the advertised window has now elapsed
        state._next_probe_at = 0.0
        return True

    state._condition.wait = advance
    try:
        lease = state.acquire()
    finally:
        state._condition.wait = original_wait
    assert lease.admitted and lease.probe
    assert lease.waited_ms >= 40_000
    assert state.pending == 0


def test_lane_probes_only_a_locally_chosen_cooldown_early() -> None:
    loaded = config()
    loaded_lane = dict(lane(loaded, "chatgpt-oauth"))
    loaded_lane["early_probe_interval_seconds"] = 0.1
    loaded_lane["queue_timeout_seconds"] = 0.5
    state = LaneState(loaded_lane)

    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=None)
    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] > 55

    # Inside the first interval the client still sees an honest cooldown
    # rejection carrying the advertised window.
    blocked = state.acquire()
    assert not blocked.admitted
    assert blocked.reason == "cooldown"
    assert blocked.retry_after >= 55

    # After one interval the lane verifies recovery with a single probe.
    time.sleep(0.5)
    probe = state.acquire()
    assert probe.admitted and probe.probe
    # Concurrent demand during the probe waits in the bounded queue instead of
    # becoming a fast half_open_probe 429. A failed probe keeps the breaker
    # open and the waiter eventually receives the normal queue-timeout result.
    during: list[Any] = []
    waiter = threading.Thread(target=lambda: during.append(state.acquire()))
    waiter.start()
    deadline = time.monotonic() + 2
    while state.pending != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.pending == 1
    state.release(probe, capacity_error=True, retry_after=None)
    waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert len(during) == 1
    assert not during[0].admitted
    assert during[0].reason == "queue_timeout"
    # The shed must describe the lane's next recheck, not a 1s "retry now"
    # that would only re-queue the client for another full budget.
    assert during[0].retry_after > 1
    assert state.snapshot()["cooldown_remaining"] > 0

    # A successful probe heals the lane immediately.
    time.sleep(0.5)
    healed = state.acquire()
    assert healed.admitted and healed.probe
    state.release(healed, capacity_error=False, retry_after=None)
    normal = state.acquire()
    assert normal.admitted and not normal.probe
    state.release(normal, capacity_error=False, retry_after=None)


def test_explicit_retry_after_is_capped_and_not_bypassed_by_early_probe(
    monkeypatch: Any,
) -> None:
    real_monotonic = time.monotonic
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded = config()
    state = LaneState(lane(loaded, "chatgpt-oauth"))
    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=100000)
    max_retry_after = lane(loaded, "chatgpt-oauth")["retry_after_max_seconds"]
    assert state.snapshot()["cooldown_remaining"] <= max_retry_after

    # A provider value inside the safety bound is still honoured in full; the
    # early probe must not bypass it.
    clock[0] = 86300.0
    refused = state.acquire()
    assert not refused.admitted
    assert refused.reason == "cooldown"

    # Once the bounded window opens, the next arrival becomes the recovery
    # probe.  The old implementation would have kept this blocked for the
    # unbounded 100000-second provider value.
    clock[0] = 86501.0
    held: list[Any] = []
    waiter = threading.Thread(target=lambda: held.append(state.acquire()))
    waiter.start()
    deadline = real_monotonic() + 2
    while not held and real_monotonic() < deadline:
        time.sleep(0.005)
    waiter.join(timeout=3)
    assert not waiter.is_alive()
    assert len(held) == 1
    assert held[0].admitted and held[0].probe


def test_quota_reset_probe_can_heal_before_old_server_deadline(
    monkeypatch: Any,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=3600)
    assert not state.acquire().admitted
    generation = state.snapshot()["failure_generation"]
    assert state.acquire_after_reset(generation - 1).reason == "stale_generation"
    probe = state.acquire_after_reset(generation)
    assert probe.admitted and probe.probe and probe.reset_probe
    assert state.acquire_after_reset(generation).reason == "busy"
    state.release(probe, capacity_error=False, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 0
    assert state.snapshot()["server_retry_after_remaining"] == 0
    normal = state.acquire()
    assert normal.admitted and not normal.probe
    state.release(normal, capacity_error=False, retry_after=None)


def test_failed_reset_probe_preserves_backoff_and_limits_repetition(
    monkeypatch: Any,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=3600)
    generation = state.snapshot()["failure_generation"]
    probe = state.acquire_after_reset(generation)
    state.release(probe, capacity_error=False, retry_after=None, successful=False)
    assert state.snapshot()["cooldown_remaining"] == 3600
    assert state.acquire_after_reset(generation).reason == "reset_probe_throttled"
    clock[0] = 401.0
    probe = state.acquire_after_reset(generation)
    state.release(probe, capacity_error=True, retry_after=7200)
    assert state.snapshot()["cooldown_remaining"] == 7200
    assert state.acquire_after_reset(generation).reason == "stale_generation"
    assert not state.acquire().admitted


def test_reset_probe_success_cannot_clear_a_newer_capacity_event(
    monkeypatch: Any,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))
    lease = state.acquire()
    state.release(lease, capacity_error=True, retry_after=3600)
    probe = state.acquire_after_reset(state.snapshot()["failure_generation"])
    newer = MODULE["Lease"](True, "admitted", 0)
    state.release(newer, capacity_error=True, retry_after=7200)
    state.release(probe, capacity_error=False, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 7200
    assert state.snapshot()["failure_streak"] == 2


def test_reset_probe_refuses_a_lane_with_outstanding_work() -> None:
    state = LaneState(lane(config(), "chatgpt-oauth"))
    first = state.acquire()
    held = state.acquire()
    state.release(first, capacity_error=True, retry_after=3600)
    assert (
        state.acquire_after_reset(state.snapshot()["failure_generation"]).reason
        == "busy"
    )
    state.release(held, capacity_error=False, retry_after=None)
    state.pending = 1
    assert (
        state.acquire_after_reset(state.snapshot()["failure_generation"]).reason
        == "busy"
    )
    state.pending = 0


def test_reset_probe_requires_completed_output_not_just_http_success() -> None:
    completed = MODULE["completed_reset_probe"]
    good = {
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "OK"}]}
        ],
    }
    assert completed(json.dumps(good).encode())
    for bad in (
        {**good, "status": "incomplete"},
        {**good, "error": {"type": "usage_limit_reached"}},
        {**good, "output": []},
        {"output_text": "OK"},
        {"status": "completed", "output": [None]},
        {"status": "completed", "output": None},
    ):
        assert not completed(json.dumps(bad).encode())
    assert not completed(b"data: " + json.dumps(good).encode())


def test_unsuccessful_probe_preserves_cooldown(monkeypatch: Any) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState({**lane(config(), "chatgpt-oauth"), "queue_timeout_seconds": 1})
    for _ in range(2):
        lease = state.acquire()
        state.release(lease, capacity_error=True, retry_after=None)
    clock[0] = 111.0
    probe = state.acquire()
    assert probe.probe
    state.release(probe, capacity_error=False, retry_after=None, successful=False)
    assert state.snapshot()["cooldown_remaining"] == 49
    assert state.snapshot()["failure_streak"] == 2
    # The remaining cooldown exceeds this lane's queue budget, so the arrival
    # is refused outright instead of being held.
    assert not state.acquire().admitted


def test_half_open_probe_respects_inflight_bound() -> None:
    loaded_lane = dict(lane(config(), "chatgpt-oauth"))
    loaded_lane["max_inflight"] = 1
    loaded_lane["queue_timeout_seconds"] = 0.02
    state = LaneState(loaded_lane)
    held = state.acquire()
    state.open_until = time.monotonic() - 1
    rejected = state.acquire()
    assert not rejected.admitted
    assert rejected.reason == "queue_timeout"
    assert state.inflight == 1
    assert not state.probe_inflight
    state.release(held, capacity_error=False, retry_after=None)


def test_successful_probe_does_not_clear_newer_retry_after(monkeypatch: Any) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    # A one-second budget keeps the final arrival on the outright-refusal path
    # instead of holding it for the whole cooldown.
    state = LaneState({**lane(config(), "chatgpt-oauth"), "queue_timeout_seconds": 1})
    first = state.acquire()
    concurrent = state.acquire()
    state.release(first, capacity_error=True, retry_after=None)
    second = state.acquire()
    state.release(second, capacity_error=True, retry_after=None)
    clock[0] = 111.0
    probe = state.acquire()
    assert probe.admitted and probe.probe
    state.release(concurrent, capacity_error=True, retry_after=60)
    state.release(probe, capacity_error=False, retry_after=None)
    assert state.snapshot()["cooldown_remaining"] == 60
    assert state.snapshot()["failure_streak"] == 3
    assert not state.acquire().admitted


def test_new_arrival_cannot_overtake_a_queued_request() -> None:
    loaded_lane = dict(lane(config(), "chatgpt-oauth"))
    loaded_lane["max_pending"] = 1
    loaded_lane["queue_timeout_seconds"] = 1
    state = LaneState(loaded_lane)
    held = [state.acquire() for _ in range(loaded_lane["max_inflight"])]
    results: list[Any] = []
    waiter = threading.Thread(target=lambda: results.append(state.acquire()))
    newcomer = None
    waiter.start()
    try:
        deadline = time.monotonic() + 2
        while state.pending != 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert state.pending == 1
        with state._condition:
            state.release(held.pop(), capacity_error=False, retry_after=None)
            newcomer = state.acquire()
        assert not newcomer.admitted
        assert newcomer.reason == "busy"
    finally:
        if newcomer is not None and newcomer.admitted:
            state.release(newcomer, capacity_error=False, retry_after=None)
        waiter.join(timeout=2)
        for acquired in held + results:
            if acquired.admitted:
                state.release(acquired, capacity_error=False, retry_after=None)
    assert not waiter.is_alive()
    assert len(results) == 1
    assert results[0].admitted
    assert state.pending == 0


def test_queued_request_is_not_admitted_after_its_deadline(monkeypatch: Any) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    loaded_lane = dict(lane(config(), "chatgpt-oauth"))
    loaded_lane["queue_timeout_seconds"] = 1
    state = LaneState(loaded_lane)
    held = [state.acquire() for _ in range(loaded_lane["max_inflight"])]

    def expire_and_release(timeout: float) -> None:
        clock[0] = 102.0
        state.release(held.pop(), capacity_error=False, retry_after=None)

    monkeypatch.setattr(state._condition, "wait", expire_and_release)
    rejected = state.acquire()
    assert not rejected.admitted
    assert rejected.reason == "queue_timeout"
    assert state.pending == 0
    for acquired in held:
        state.release(acquired, capacity_error=False, retry_after=None)


def test_shed_rejection_advertises_the_next_lane_recheck(monkeypatch: Any) -> None:
    """A shed must not advertise a 1s retry.

    A request that already burned its whole queue budget is only re-queued by
    an immediate retry, so the advertised interval has to describe when the
    lane will actually re-evaluate: the later of the cooldown expiry and the
    next probe slot, with the probe cadence as a floor.
    """
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = LaneState(lane(config(), "chatgpt-oauth"))

    # Healthy lane, nothing scheduled: the probe cadence is the floor.
    assert state._shed_retry_after(clock[0]) == 10

    # A cooldown longer than the cadence is the honest answer.
    state.open_until = clock[0] + 45
    assert state._shed_retry_after(clock[0]) == 45

    # A pending probe slot beyond the cooldown also delays the next recheck.
    state.open_until = clock[0] + 5
    state._next_probe_at = clock[0] + 30
    assert state._shed_retry_after(clock[0]) == 30


def test_capacity_classifier_ignores_generated_text_and_routing_errors() -> None:
    loaded_lane = lane(config(), "chatgpt-oauth")
    for body in (
        b'{"output_text":"Explain rate limit and server_is_overloaded"}',
        b'data: {"type":"response.output_text.delta","delta":"rate limit"}\n\n',
    ):
        assert not is_capacity_response(200, body, None, loaded_lane)
    assert not is_capacity_response(
        503, b'{"error":{"code":"model_not_found"}}', None, loaded_lane
    )
    assert is_capacity_response(
        200,
        b'data: {"type":"response.failed","response":{"error":{"code":"server_is_overloaded"}}}\n\n',
        None,
        loaded_lane,
    )


def test_config_rejects_non_positive_early_probe_interval(tmp_path: Any) -> None:
    raw = json.loads(
        (Path(__file__).parent / "scripts" / "remote" / "cpa-admission.json").read_text(
            encoding="utf-8"
        )
    )
    raw["early_probe_interval_seconds"] = 0
    config_path = tmp_path / "cpa-admission.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    try:
        load_config(config_path)
    except ValueError as exc:
        assert "early_probe_interval_seconds" in str(exc)
    else:
        raise AssertionError(
            "non-positive early_probe_interval_seconds must be rejected"
        )


def test_lane_respects_retry_after_beyond_local_backoff_schedule() -> None:
    loaded = config()
    state = LaneState(lane(loaded, "deepseek-official"))
    lease = state.acquire()
    assert lease.admitted
    state.release(lease, capacity_error=True, retry_after=7200)
    snapshot = state.snapshot()
    assert snapshot["failure_streak"] == 1
    assert snapshot["cooldown_remaining"] >= 7199
    assert snapshot["cooldown_remaining"] <= 7200


def test_lane_does_not_shorten_an_existing_longer_cooldown() -> None:
    loaded = config()
    state = LaneState(lane(loaded, "deepseek-official"))
    first = state.acquire()
    concurrent = state.acquire()
    state.release(first, capacity_error=True, retry_after=60)

    # Model the next demand-driven early probe without waiting for the
    # production interval. A shorter concurrent Retry-After must not replace
    # the already advertised longer window.
    assert concurrent.admitted
    state.release(concurrent, capacity_error=True, retry_after=1)
    assert state.snapshot()["cooldown_remaining"] >= 55


def test_health_reaps_finished_retired_readers_but_keeps_total() -> None:
    loaded = config()
    proxy = AdmissionProxy(loaded)
    finished = threading.Thread(target=lambda: None)
    finished.start()
    finished.join(timeout=2)
    assert not finished.is_alive()
    assert proxy.note_retired_reader(finished) == 0

    release = threading.Event()
    resident = threading.Thread(target=release.wait)
    resident.start()
    deadline = time.monotonic() + 2
    while not resident.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert resident.is_alive()
    assert proxy.note_retired_reader(resident) == 1
    health = proxy.health()
    assert health["retired_readers"] == 1
    assert health["retired_readers_total"] == 2

    release.set()
    resident.join(timeout=2)
    assert not resident.is_alive()
    health = proxy.health()
    assert health["retired_readers"] == 0
    assert health["retired_readers_total"] == 2


def test_lane_pending_slots_bound_concurrency_and_reject_the_rest() -> None:
    # The bound is the contract: at most `max_inflight` upstream calls run at
    # once, at most `max_pending` waiters queue, and anything beyond that is
    # rejected immediately as busy rather than piling up.
    # Every assertion below runs against a one-second queue budget clone, so a
    # regression in the bound shows up as a wrong reason code rather than as a
    # test that blocks for the production queue timeout.
    loaded = config()
    loaded_lane = lane(loaded, "zhipu-coding-plan")
    inflight = loaded_lane["max_inflight"]
    max_pending = loaded_lane["max_pending"]

    short_lane = dict(loaded_lane)
    short_lane["queue_timeout_seconds"] = 1
    state = LaneState(short_lane)

    held = [state.acquire() for _ in range(inflight)]
    assert all(lease.admitted for lease in held)
    assert state.inflight == inflight

    results: list[Any] = []
    lock = threading.Lock()

    def wait_for_slot() -> None:
        lease = state.acquire()
        with lock:
            results.append(lease)

    waiters = [threading.Thread(target=wait_for_slot) for _ in range(max_pending)]
    for waiter in waiters:
        waiter.start()
    deadline = time.monotonic() + 3
    while state.pending != max_pending and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.pending == max_pending
    assert state.inflight == inflight

    # One more than the bound is rejected immediately rather than queued.
    rejected = state.acquire()
    assert not rejected.admitted
    assert rejected.reason == "busy"
    assert state.pending == max_pending

    # A waiter served after a release is admitted without ever exceeding the
    # concurrency bound.
    state.release(held.pop(), capacity_error=False, retry_after=None)
    served_deadline = time.monotonic() + 3
    while not results and time.monotonic() < served_deadline:
        time.sleep(0.005)
    assert len(results) == 1
    assert results[0].admitted
    assert state.inflight == inflight
    assert state.pending == max_pending - 1

    # The waiters that never got a slot time out rather than blocking forever.
    for waiter in waiters:
        waiter.join(timeout=5)
    assert not any(waiter.is_alive() for waiter in waiters)
    assert len(results) == max_pending
    assert all(
        not lease.admitted and lease.reason == "queue_timeout" for lease in results[1:]
    )
    assert state.inflight <= inflight


def test_queued_request_hands_its_slot_back_when_the_client_is_gone() -> None:
    """An abandoned request must not hold a pending slot for the whole budget.

    A queued request that nobody is listening for would otherwise occupy one
    of four pending slots for the 120s budget, long after its client left.
    """

    loaded = config()
    state = LaneState(lane(loaded, "chatgpt-oauth"))
    held = [
        state.acquire() for _ in range(lane(loaded, "chatgpt-oauth")["max_inflight"])
    ]
    assert all(lease.admitted for lease in held)
    # An immediate admit never queued, so its wait is stamped zero.
    assert all(lease.waited_ms == 0 for lease in held)

    gone = state.acquire(alive=lambda: False)
    assert not gone.admitted
    assert gone.reason == "downstream_gone"
    # The slot is returned, not leaked, and the request gave up after a poll
    # slice rather than sleeping out the 120s budget.
    assert state.pending == 0
    assert 0 <= gone.waited_ms < 30000

    # A live waiter still takes the lane the moment a holder releases it, and
    # its own queue wait is visible in the lease.
    results: list[Any] = []

    def wait_for_slot() -> None:
        results.append(state.acquire(alive=lambda: True))

    waiter = threading.Thread(target=wait_for_slot)
    waiter.start()
    deadline = time.monotonic() + 3
    while state.pending != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state.pending == 1

    state.release(held.pop(), capacity_error=False, retry_after=None)
    waiter.join(timeout=5)
    assert not waiter.is_alive()
    assert len(results) == 1
    assert results[0].admitted
    assert state.pending == 0
    for lease in held + results:
        state.release(lease, capacity_error=False, retry_after=None)


def test_lease_waited_ms_reports_a_timeout_against_the_queue_budget() -> None:
    """A queue timeout must be attributable to the wait it actually served."""

    loaded = config()
    lane_config = dict(lane(loaded, "chatgpt-oauth"))
    lane_config["queue_timeout_seconds"] = 1
    state = LaneState(lane_config)
    held = [state.acquire() for _ in range(lane_config["max_inflight"])]
    assert all(lease.admitted for lease in held)

    started = time.monotonic()
    timed_out = state.acquire()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    assert not timed_out.admitted
    assert timed_out.reason == "queue_timeout"
    assert elapsed_ms - 200 <= timed_out.waited_ms <= elapsed_ms + 500
    for lease in held:
        state.release(lease, capacity_error=False, retry_after=None)


def test_downstream_alive_tracks_a_client_that_leaves_while_queued() -> None:
    """The queue-reaping probe must read a closed socket as gone.

    Semantics under test, and why they are asymmetric: reaping only happens when
    the socket buffer is EMPTY and the peer has closed. A pipelined next request
    looks identical to a body followed by a FIN at the socket layer, so a
    non-empty buffer is always reported alive. That is the fail-safe direction --
    a missed reap falls back to the pre-existing full-budget wait, while a false
    reap would reject a live request.
    """

    handler_type = cast(Any, MODULE["Handler"])

    def handler_for(connection: socket.socket) -> Any:
        handler = object.__new__(handler_type)
        handler.connection = connection
        return handler

    def wait_gone(handler: Any) -> bool:
        deadline = time.monotonic() + 2
        while handler._downstream_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        return handler._downstream_alive() is False

    # An idle but connected client is alive.
    server_side, client_side = socket.socketpair()
    handler = handler_for(server_side)
    try:
        assert handler._downstream_alive() is True
        # A clean FIN with nothing buffered is a client that gave up.
        client_side.close()
        assert wait_gone(handler) is True
    finally:
        server_side.close()

    # A hard RST with nothing buffered is the desktop-abort shape.
    server_side, client_side = socket.socketpair()
    handler = handler_for(server_side)
    try:
        client_side.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        client_side.close()
        assert wait_gone(handler) is True
    finally:
        server_side.close()

    # Buffered bytes keep the client "alive" on purpose, so a pipelined request
    # is never mistaken for an abandoned one.
    server_side, client_side = socket.socketpair()
    handler = handler_for(server_side)
    try:
        client_side.sendall(b"GET /healthz HTTP/1.1\r\n")
        client_side.close()
        assert handler._downstream_alive() is True
    finally:
        server_side.close()


def test_proxy_preserves_chunked_stream_framing() -> None:
    class UpstreamHandler(BaseHTTPRequestHandler):
        # Deliberately HTTP/1.0 close-delimited, matching the CPA upstream
        # the admission talks to.
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            self.connection.sendall(b"data: one\n\n")
            self.connection.sendall(b"data: two\n\n")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever)
    upstream_thread.start()
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(loaded))
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=3
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"unregistered-model","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        assert response.getheader("Transfer-Encoding") == "chunked"
        assert response.read() == b"data: one\n\ndata: two\n\n"
        client.close()
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def _read_raw_response(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, ConnectionError):
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def test_rejects_truncated_fixed_length_request_body() -> None:
    loaded = config()
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(loaded))
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    sock = socket.create_connection(admission.server_address, timeout=3)
    sock.settimeout(3)
    try:
        sock.sendall(
            b"POST /v1/responses HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Length: 5\r\n\r\n"
            b"abc"
        )
        sock.shutdown(socket.SHUT_WR)
        response = _read_raw_response(sock)
        assert b"400 Bad Request" in response
        assert b"request body ended before" in response
    finally:
        sock.close()
        admission.shutdown()
        admission.server_close()
        admission_thread.join(timeout=2)


def test_rejects_truncated_chunked_request_body() -> None:
    loaded = config()
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(loaded))
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    sock = socket.create_connection(admission.server_address, timeout=3)
    sock.settimeout(3)
    try:
        sock.sendall(
            b"POST /v1/responses HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
            b"5\r\n"
            b"abc"
        )
        sock.shutdown(socket.SHUT_WR)
        response = _read_raw_response(sock)
        assert b"400 Bad Request" in response
        assert b"request body ended before" in response
    finally:
        sock.close()
        admission.shutdown()
        admission.server_close()
        admission_thread.join(timeout=2)


@pytest.mark.parametrize(
    "framing,body",
    [
        (b"Content-Length: 999999999\r\n", b""),
        (b"Content-Length: 0\r\nContent-Length: 1\r\n", b""),
        (b"Content-Length: 0\r\nTransfer-Encoding: chunked\r\n", b"0\r\n\r\n"),
        (b"Transfer-Encoding: chunked\r\n", b"+1\r\nx\r\n0\r\n\r\n"),
        (b"Transfer-Encoding: chunked\r\n", b"0\r\n" + b"X-Trailer: x\r\n" * 6000),
    ],
    ids=[
        "oversized-length",
        "duplicate-length",
        "conflicting-framing",
        "invalid-chunk-size",
        "oversized-trailers",
    ],
)
def test_malformed_body_closes_connection_before_pipelined_request(
    framing: bytes,
    body: bytes,
) -> None:
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(config()))
    thread = threading.Thread(target=admission.serve_forever)
    thread.start()
    sock = socket.create_connection(admission.server_address, timeout=3)
    try:
        sock.sendall(
            b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n"
            + framing
            + b"\r\n"
            + body
            + b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        response = _read_raw_response(sock)
        assert b"400 Bad Request" in response
        assert b"Connection: close" in response
        assert b"200 OK" not in response
    finally:
        sock.close()
        admission.shutdown()
        admission.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "framing,body",
    [
        (b"Content-Length: 1 \t\r\n", b"x"),
        (b"Transfer-Encoding: chunked\r\n", b"1 \t;name=value\r\nx\r\n0\r\n\r\n"),
    ],
    ids=["length-ows", "chunk-extension-bws"],
)
def test_valid_body_framing_whitespace_remains_compatible(
    framing: bytes,
    body: bytes,
) -> None:
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(config()))
    thread = threading.Thread(target=admission.serve_forever)
    thread.start()
    try:
        with socket.create_connection(admission.server_address, timeout=3) as sock:
            sock.sendall(
                b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
                + framing
                + b"\r\n"
                + body
            )
            response = _read_raw_response(sock)
            assert b"200 OK" in response
            assert b"400 Bad Request" not in response
    finally:
        admission.shutdown()
        admission.server_close()
        thread.join(timeout=2)


def test_zero_chunk_without_trailer_terminator_is_rejected() -> None:
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(config()))
    thread = threading.Thread(target=admission.serve_forever)
    thread.start()
    sock = socket.create_connection(admission.server_address, timeout=3)
    try:
        sock.sendall(
            b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n0\r\n"
        )
        sock.shutdown(socket.SHUT_WR)
        response = _read_raw_response(sock)
        assert b"400 Bad Request" in response
        assert b"invalid chunked trailer" in response
    finally:
        sock.close()
        admission.shutdown()
        admission.server_close()
        thread.join(timeout=2)


def _serve_local(handler: Any) -> tuple[Any, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread


def test_admin_reset_probe_checks_access_completion_and_repeat_budget() -> None:
    good = {
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "OK"}]}
        ],
    }
    for upstream_payload, should_heal in (
        (good, True),
        ({**good, "status": "incomplete"}, False),
        ({"error": {"type": "usage_limit_reached"}}, False),
    ):
        received: list[dict[str, Any]] = []

        class RecoveryUpstream(BaseHTTPRequestHandler):
            def do_POST(
                self,
                received_requests: list[dict[str, Any]] = received,
                response_payload: dict[str, Any] = upstream_payload,
            ) -> None:
                received_requests.append(
                    {
                        "path": self.path,
                        "body": json.loads(
                            self.rfile.read(int(self.headers["Content-Length"]))
                        ),
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                encoded = json.dumps(response_payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        upstream, upstream_thread = _serve_local(RecoveryUpstream)
        loaded = config()
        loaded["upstream_port"] = upstream.server_address[1]
        proxy = AdmissionProxy(loaded)
        state = proxy.lanes["chatgpt-oauth"]
        failed = state.acquire()
        state.release(failed, capacity_error=True, retry_after=3600)
        payload = json.dumps(
            {
                "model": "gpt-6.1-sol",
                "reason": "quota_reset",
                "expected_generation": state.snapshot()["failure_generation"],
            }
        ).encode()
        admission = AdmissionServer(("127.0.0.1", 0), proxy)
        admission_thread = threading.Thread(target=admission.serve_forever)
        admission_thread.start()
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=5
        )
        try:
            for method, body, headers, expected_status in (
                ("GET", b"", {}, 404),
                ("POST", payload, {"X-Forwarded-For": "192.0.2.1"}, 404),
                ("POST", b'{"model":"gpt-6.1-sol"}', {}, 400),
                (
                    "POST",
                    payload.replace(b'"gpt-6.1-sol"', b'"glm-5.3"'),
                    {},
                    400,
                ),
                (
                    "POST",
                    payload.replace(b'"quota_reset"', b'"automatic_timer"'),
                    {},
                    400,
                ),
            ):
                client.request(
                    method, "/admin/recover-after-reset", body=body, headers=headers
                )
                response = client.getresponse()
                assert response.status == expected_status
                response.read()
            assert not received
            client.request(
                "POST",
                "/v1/responses",
                body=b'{"model":"gpt-6.1-sol","input":"normal"}',
            )
            response = client.getresponse()
            assert response.status == 429
            assert response.getheader("X-CPA-Admission-Reason") == "cooldown"
            response.read()
            assert not received
            client.request(
                "POST",
                "/admin/recover-after-reset",
                body=payload,
                headers={"Authorization": "Bearer test-client-key"},
            )
            response = client.getresponse()
            assert response.status == 200
            response.read()
            deadline = time.monotonic() + 2
            while state.snapshot()["inflight"] and time.monotonic() < deadline:
                time.sleep(0.005)
            assert not state.snapshot()["inflight"]
            assert (state.snapshot()["cooldown_remaining"] == 0) == should_heal
            assert len(received) == 1
            assert received[0]["path"] == "/v1/responses"
            assert received[0]["body"]["model"] == "gpt-6.1-sol"
            assert received[0]["body"]["input"] == "Reply with exactly: OK"
            assert received[0]["body"]["stream"] is False
            assert received[0]["authorization"] == "Bearer test-client-key"
            client.request("POST", "/admin/recover-after-reset", body=payload)
            response = client.getresponse()
            assert response.status == 409
            response.read()
            assert len(received) == 1
        finally:
            client.close()
            admission.shutdown()
            upstream.shutdown()
            admission.server_close()
            upstream.server_close()
            admission_thread.join(timeout=2)
            upstream_thread.join(timeout=2)


def test_request_trace_distinguishes_upstream_and_local_rejections(caplog: Any) -> None:
    caplog.set_level(logging.INFO)
    received: list[dict[str, str]] = []
    incoming_request_id = "private-client-request-identifier"
    incoming_instance_id = "private-cockpit-instance-identifier"
    payload = b'{"model":"gpt-6-luna","input":"private-request-content"}'

    class TraceUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            received.append(dict(self.headers.items()))
            status = 200 if len(received) == 1 else 429
            response_body = (
                b'{"output_text":"ok"}'
                if status == 200
                else b'{"error":{"code":"rate_limit_exceeded"}}'
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("X-CPA-Request-Id", "spoofed-upstream-identifier")
            self.send_header("X-CPA-Admission-Reason", "spoofed-upstream-reason")
            if status == 429:
                self.send_header("Retry-After", "30")
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(TraceUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    # Keep the upstream's advertised cooldown longer than the queue budget so
    # this test still exercises the outright-refusal headers; a cooldown that
    # fits the budget is now held in the queue instead of being refused.
    for lane_config in loaded["lanes"]:
        lane_config["queue_timeout_seconds"] = 1
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    client = http.client.HTTPConnection(
        "127.0.0.1", admission.server_address[1], timeout=5
    )
    request_ids: list[str] = []
    try:
        for route, expected_status in (
            ("/v1/responses?private-route-content=1", 200),
            ("/v1/chat/completions", 429),
        ):
            client.request(
                "POST",
                route,
                body=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Client-Request-Id": incoming_request_id,
                    "X-Cockpit-Instance-Id": incoming_instance_id,
                    "X-CPA-Request-Id": "spoofed-client-identifier",
                },
            )
            response = client.getresponse()
            assert response.status == expected_status
            request_id = response.getheader("X-CPA-Request-Id")
            assert request_id is not None and len(request_id) == 32
            assert int(request_id, 16) >= 0
            assert response.getheader("X-CPA-Admission-Reason") is None
            if expected_status == 429:
                assert response.getheader("Retry-After") == "30"
            assert response.read()
            request_ids.append(request_id)
            deadline = time.monotonic() + 2
            while proxy.lanes["chatgpt-oauth"].snapshot()["inflight"]:
                assert time.monotonic() < deadline
                time.sleep(0.005)
        assert len(received) == 2
        assert [headers.get("X-CPA-Request-Id") for headers in received] == request_ids
        client.request(
            "POST",
            "/v1/responses",
            body=payload,
            headers={
                "Content-Type": "application/json",
                "X-Client-Request-Id": incoming_request_id,
                "X-Cockpit-Instance-Id": incoming_instance_id,
            },
        )
        rejected = client.getresponse()
        assert rejected.status == 429
        assert rejected.getheader("X-CPA-Admission-Reason") == "cooldown"
        local_request_id = rejected.getheader("X-CPA-Request-Id")
        assert local_request_id is not None and len(local_request_id) == 32
        request_ids.append(local_request_id)
        retry_after_header = rejected.getheader("Retry-After")
        assert retry_after_header is not None
        assert 28 <= int(retry_after_header) <= 30
        assert len(rejected.read()) == 203
        assert len(received) == 2
        assert len(set(request_ids)) == 3
    finally:
        client.close()
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)
    log_text = caplog.text
    for request_id in request_ids:
        assert f"request_id={request_id}" in log_text
    assert "route=responses" in log_text
    assert "route=chat" in log_text
    assert (
        "client_request_hash="
        + hashlib.sha256(incoming_request_id.encode()).hexdigest()[:12]
    ) in log_text
    assert (
        "instance_hash="
        + hashlib.sha256(incoming_instance_id.encode()).hexdigest()[:12]
    ) in log_text
    for sensitive_value in (
        incoming_request_id,
        incoming_instance_id,
        "private-request-content",
        "private-route-content",
    ):
        assert sensitive_value not in log_text


def test_sse_stream_emits_heartbeat_comments_during_upstream_silence() -> None:
    class SlowSSEUpstream(BaseHTTPRequestHandler):
        # Deliberately HTTP/1.0 close-delimited, matching the CPA upstream
        # the admission talks to.
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            # Frames go out on the raw socket on purpose: a buffered wfile
            # would batch the deliberate 1.5s silence away.
            self.connection.sendall(b"data: one\n\n")
            time.sleep(1.5)
            self.connection.sendall(b"data: two\n\n")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(SlowSSEUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    admission = AdmissionServer(
        ("127.0.0.1", 0), AdmissionProxy(loaded, heartbeat_interval=0.3)
    )
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=10
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"unregistered-model","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        payload = response.read()
        client.close()
        assert b"data: one\n\n" in payload
        assert b"data: two\n\n" in payload
        assert payload.count(b": keepalive\n\n") >= 1
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_client_disconnect_releases_lane_lease_during_upstream_silence() -> None:
    class SilentSSEUpstream(BaseHTTPRequestHandler):
        # Deliberately HTTP/1.0 close-delimited, matching the CPA upstream
        # the admission talks to.
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            self.connection.sendall(b"data: one\n\n")
            time.sleep(30)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(SilentSSEUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    proxy = AdmissionProxy(loaded, heartbeat_interval=0.3)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=5
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        assert response.read(10)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 1
        client.sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        client.close()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0:
                break
            time.sleep(0.1)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0
        assert proxy.lanes["chatgpt-oauth"].snapshot()["failure_streak"] == 0
        assert proxy.lanes["chatgpt-oauth"].snapshot()["cooldown_remaining"] == 0
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_lane_sse_lease_released_when_client_leaves_before_upstream_speaks() -> None:
    # The disconnect must be honoured structurally, not by luck of timing: a
    # silent upstream must never let the forwarding thread park inside a read
    # while the client is gone. The reader thread owns every blocking read,
    # so the lane lease is released on the very next heartbeat slice no matter
    # how long the upstream stays quiet (production heartbeat is 15s, so one
    # slice is the worst case a client can pin a lane).
    class MutedSSEUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            self.connection.sendall(b"data: one\n\n")
            time.sleep(30)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(MutedSSEUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    proxy = AdmissionProxy(loaded, heartbeat_interval=0.3)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=5
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        assert response.read(1)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 1
        client.sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        client.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0:
                break
            time.sleep(0.05)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["inflight"] == 0
        assert proxy.lanes["chatgpt-oauth"].snapshot()["failure_streak"] == 0
        assert proxy.lanes["chatgpt-oauth"].snapshot()["cooldown_remaining"] == 0
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_lane_sse_stream_completes_when_upstream_closes_connection() -> None:
    # http.client detaches the socket (conn.sock becomes None) as soon as the
    # upstream signals close. A lane SSE stream must still be forwarded in
    # full: any readiness check that treats the detached socket as "nothing to
    # read" leaves the downstream waiting for data that is already buffered,
    # and check-then-read races on the same HTTPResponse cause IncompleteRead.
    # The client must receive both frames and the terminating chunk.
    class ClosingSSEUpstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            self.connection.sendall(
                b"B\r\ndata: one\n\n\r\nB\r\ndata: two\n\n\r\n0\r\n\r\n"
            )
            self.close_connection = True

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(ClosingSSEUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    admission = AdmissionServer(("127.0.0.1", 0), AdmissionProxy(loaded))
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=5
        )
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 200
        assert response.getheader("Transfer-Encoding") == "chunked"
        assert response.read() == b"data: one\n\ndata: two\n\n"
        client.close()
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_lane_reject_survives_a_client_that_left_before_the_429_landed() -> None:
    # A retry-storming client closes the socket the moment it has decided to
    # retry; the admission then writes the 429 into a dead pipe. That race
    # must be absorbed quietly -- it is not a handler crash and must not reach
    # the journal as a traceback -- while the lane bookkeeping stays intact.
    class OverloadedUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = b'{"error":{"code":"server_is_overloaded"}}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    upstream, upstream_thread = _serve_local(OverloadedUpstream)
    loaded = config()
    loaded["upstream_port"] = upstream.server_address[1]
    loaded_lane = next(
        item for item in loaded["lanes"] if item["name"] == "chatgpt-oauth"
    )
    # A short queue timeout keeps the two failures that open the breaker from
    # stalling the test; the production budget is untouched.
    loaded_lane["queue_timeout_seconds"] = 1
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()

    payload = b'{"model":"gpt-6-luna","input":"hello"}'

    def raw_call(read_response: bool = True) -> None:
        # A full request/response cycle so the upstream 503 is classified as a
        # capacity blip (the client leaving early would be a disconnect, which
        # is deliberately not capacity evidence).
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect(("127.0.0.1", admission.server_address[1]))
            sock.sendall(
                b"POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            if read_response:
                while sock.recv(4096):
                    pass
        finally:
            sock.close()

    try:
        # Two consecutive capacity failures open the breaker (threshold 2).
        for _ in range(2):
            raw_call()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if proxy.lanes["chatgpt-oauth"].snapshot()["failure_streak"] >= 2:
                break
            time.sleep(0.05)
        assert proxy.lanes["chatgpt-oauth"].snapshot()["failure_streak"] >= 2

        # The next request is rejected with a 429; the socket is torn down with
        # SO_LINGER 0 so the server sees an RST and its write fails. Swallowing
        # that failure is the behaviour under test: the request thread survives
        # and the lane keeps its own bookkeeping.
        victim = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        victim.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        try:
            victim.connect(("127.0.0.1", admission.server_address[1]))
            victim.sendall(
                b"POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
        finally:
            victim.close()
        time.sleep(0.5)

        assert proxy.lanes["chatgpt-oauth"].snapshot()["cooldown_remaining"] > 0
        assert admission_thread.is_alive()
    finally:
        admission.shutdown()
        upstream.shutdown()
        admission.server_close()
        upstream.server_close()
        admission_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_upstream_transport_failure_does_not_open_the_breaker() -> None:
    # A connection refused / reset / read timeout proves the network hiccupped,
    # not that the shared account is out of capacity. Two such failures in a
    # row must leave the lane open for business: the request is still served a
    # bounded 503, but no cooldown opens and a later request is still admitted
    # rather than being cooldown-rejected.
    loaded = config()
    # A closed port makes every upstream call fail at the transport layer
    # without any provider ever answering.
    loaded["upstream_port"] = 1
    loaded_lane = next(
        item for item in loaded["lanes"] if item["name"] == "chatgpt-oauth"
    )
    loaded_lane["queue_timeout_seconds"] = 1
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()
    try:
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=10
        )
        for _ in range(2):
            client.request(
                "POST",
                "/v1/responses",
                body=b'{"model":"gpt-6-luna","input":"hello"}',
                headers={"Content-Type": "application/json"},
            )
            response = client.getresponse()
            assert response.status == 503
            assert response.read()
        state = proxy.lanes["chatgpt-oauth"].snapshot()
        assert state["failure_streak"] == 0, state
        assert state["cooldown_remaining"] == 0, state
        # Still admitted, not cooldown-rejected.
        client.request(
            "POST",
            "/v1/responses",
            body=b'{"model":"gpt-6-luna","input":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        assert client.getresponse().status == 503
        client.close()
    finally:
        admission.shutdown()
        admission.server_close()
        admission_thread.join(timeout=2)


def test_keep_alive_connection_reset_after_a_response_is_absorbed() -> None:
    # BaseHTTPRequestHandler.handle() re-enters handle_one_request() while the
    # connection is kept alive, and http.server guards only TimeoutError around
    # the next request-line read. A client that RSTs after a completed response
    # therefore raises ConnectionResetError out of the stdlib loop and
    # socketserver logs a full handler traceback -- indistinguishable in the
    # journal from a real crash. The handler must absorb it and close quietly.
    loaded = config()
    # A dead upstream keeps this test about the reader loop, not about proxying.
    loaded["upstream_port"] = 1
    proxy = AdmissionProxy(loaded)
    admission = AdmissionServer(("127.0.0.1", 0), proxy)
    admission_thread = threading.Thread(target=admission.serve_forever)
    admission_thread.start()

    # socketserver calls _handle_request_noblock; intercept the traceback that
    # its process_request_thread prints for an uncaught handler exception.
    logged: list[str] = []
    original_print_exc = socketserver.BaseServer.handle_error

    def capture(
        self: Any,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: Any,
    ) -> None:
        logged.append("handle_error")

    socketserver.BaseServer.handle_error = capture  # type: ignore[method-assign]
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(("127.0.0.1", admission.server_address[1]))
        # A completed request first, then an RST before the next request line.
        # The connection stays open after the response (that is the point), so
        # read exactly one framed response rather than draining to EOF.
        sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            head += sock.recv(4096)
        length = int(
            next(
                line.split(b":", 1)[1]
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
        )
        body = head.split(b"\r\n\r\n", 1)[1]
        while len(body) < length:
            body += sock.recv(4096)
        assert head.startswith(b"HTTP/1.1 200")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()
        time.sleep(0.5)

        assert logged == [], f"handler logged a traceback: {logged}"
        assert admission_thread.is_alive()
        # The server is still healthy for the next request.
        client = http.client.HTTPConnection(
            "127.0.0.1", admission.server_address[1], timeout=10
        )
        client.request("GET", "/healthz")
        assert client.getresponse().status == 200
        client.close()
    finally:
        socketserver.BaseServer.handle_error = original_print_exc  # type: ignore[method-assign]
        admission.shutdown()
        admission.server_close()
        admission_thread.join(timeout=2)

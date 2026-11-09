"""Contracts for the admission cache/TTFT telemetry.

The rolling aggregate in ``ModelTelemetry`` answers two questions that used to
require a post-hoc ``journalctl`` pass: is the gateway really reaching the
provider prompt cache, and how slow is the first visible token. It is
observation only -- no admission decision may read it -- so the tests pin its
shape, its honesty (unknown is ``None``, not a fabricated 0), and its bounds.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, cast
from unittest import mock

MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "remote" / "cpa-admission.py")
)
ModelTelemetry = cast(Any, MODULE["ModelTelemetry"])

SERIES_LIMIT = int(MODULE["_TELEMETRY_SERIES_LIMIT"])


def _record(
    telemetry: Any,
    model: str = "gpt-6.1-terra",
    usage_in: int | None = 100,
    usage_cached: int | None = 900,
    usage_out: int | None = 10,
    first_text_ms: int | None = 1500,
    result: str | None = "completed",
) -> None:
    telemetry.record(model, usage_in, usage_cached, usage_out, first_text_ms, result)


def test_hit_ratio_uses_provider_cached_and_uncached_prompt_tokens() -> None:
    telemetry = ModelTelemetry()

    _record(telemetry, usage_in=100, usage_cached=900, usage_out=10)
    _record(telemetry, usage_in=300, usage_cached=700, usage_out=20)

    entry = telemetry.snapshot()["models"]["gpt-6.1-terra"]

    assert entry["usage_samples"] == 2
    assert entry["cached_tokens"] == 1600
    assert entry["uncached_input_tokens"] == 400
    # 1600 / (1600 + 400): every prompt token is the denominator.
    assert entry["cache_hit_ratio"] == 0.8


def test_only_completed_generations_feed_the_cache_ratio() -> None:
    telemetry = ModelTelemetry()

    _record(telemetry, usage_in=0, usage_cached=1000)
    # A capacity refusal must not move the ratio, however much usage CPA
    # happened to relay for it. It carries no first-text sample either, so it
    # is not stored at all: a refusal is not a generation.
    _record(
        telemetry,
        usage_in=1000,
        usage_cached=0,
        first_text_ms=None,
        result="capacity",
    )

    entry = telemetry.snapshot()["models"]["gpt-6.1-terra"]

    assert entry["usage_samples"] == 1
    assert entry["cache_hit_ratio"] == 1.0
    assert entry["samples"] == 1


def test_provider_without_cache_numbers_reports_none_not_zero() -> None:
    telemetry = ModelTelemetry()

    _record(
        telemetry,
        usage_in=None,
        usage_cached=None,
        usage_out=42,
        first_text_ms=None,
    )

    entry = telemetry.snapshot()["models"]["gpt-6.1-terra"]

    assert "cache_hit_ratio" not in entry
    assert "usage_samples" not in entry
    assert entry["samples"] == 1


def test_interrupted_first_token_still_feeds_the_first_text_distribution() -> None:
    telemetry = ModelTelemetry()

    # No usage, but a visible failure after a slow first token is exactly the
    # "吐字缓慢" symptom the percentile exists to expose.
    _record(
        telemetry,
        usage_in=None,
        usage_cached=None,
        usage_out=None,
        first_text_ms=9000,
        result="client_disconnect",
    )

    entry = telemetry.snapshot()["models"]["gpt-6.1-terra"]

    assert entry["first_text_ms"] == {"n": 1, "p50": 9000, "p90": 9000, "max": 9000}
    assert "cache_hit_ratio" not in entry


def test_samples_outside_the_window_are_dropped_and_pruned() -> None:
    telemetry = ModelTelemetry(window_seconds=0.0)

    _record(telemetry)
    stamped_at = telemetry._samples["gpt-6.1-terra"][0][0]
    # Whether the clock advances between record() and snapshot() is machine
    # tick resolution, not semantics: on a coarse monotonic clock both calls
    # land on the same value and ``ts >= now - 0`` would keep the sample. Pin
    # the clock one tick past the stamp so "older than the window" holds on
    # every host, and snapshot() also prunes the series to [].
    with mock.patch.object(MODULE["time"], "monotonic", return_value=stamped_at + 1.0):
        snapshot = telemetry.snapshot()

    assert snapshot["models"]["gpt-6.1-terra"]["samples"] == 0
    assert "cache_hit_ratio" not in snapshot["models"]["gpt-6.1-terra"]
    # The series itself is pruned, so a long-lived process cannot accumulate
    # stale rows.
    assert telemetry._samples["gpt-6.1-terra"] == []


def test_series_count_is_bounded_and_never_evicts_a_live_model() -> None:
    telemetry = ModelTelemetry()

    for index in range(SERIES_LIMIT):
        _record(telemetry, model=f"model-{index}")
    _record(telemetry, model="overflow-model")

    snapshot = telemetry.snapshot()

    assert len(snapshot["models"]) == SERIES_LIMIT
    assert "overflow-model" not in snapshot["models"]
    assert snapshot["dropped_series_total"] == 1
    # An admitted model keeps its series; growth stops at the cap only.
    assert snapshot["models"]["model-0"]["samples"] == 1


def test_sample_carrying_nothing_is_not_recorded() -> None:
    telemetry = ModelTelemetry()

    _record(
        telemetry,
        usage_in=None,
        usage_cached=None,
        usage_out=None,
        first_text_ms=None,
    )

    assert telemetry.snapshot()["models"] == {}


def test_listen_backlog_is_deeper_than_the_stdlib_default() -> None:
    """A 5-deep accept queue throttles concurrency before any lane decision.

    socketserver defaults ``request_queue_size`` to 5. Measured 2026-11-08:
    447 req/s single-threaded but 57 req/s at 8-way concurrency, with the
    listener reporting ``LISTEN 0 5``. The backlog must not be the thing that
    serialises a burst; lane inflight/pending still decides admission.
    """
    server = cast(Any, MODULE["Server"])

    assert server.request_queue_size > 5
    # Bounded, not unbounded: it only absorbs the pre-lane burst.
    assert server.request_queue_size <= 256


def test_total_input_shape_reports_provider_ratio_not_a_halved_one() -> None:
    """OpenAI/Antigravity/GLM report TOTAL input plus a cached subset.

    The pre-2026-11-08 snapshot summed input and cached for every provider, so
    the total-input shape read as cached / (cached + total). Measured on bwg
    over 48h that turned a real 96.8% OpenClaw cache hit into 49.2%, i.e. the
    operator-facing number was wrong by roughly 2x and made a healthy prompt
    cache look like a mid-range one.
    """

    telemetry = ModelTelemetry()

    _record(telemetry, usage_in=1000, usage_cached=900, usage_out=10)
    _record(telemetry, usage_in=1000, usage_cached=960, usage_out=10)

    entry = telemetry.snapshot()["models"]["gpt-6.1-terra"]

    assert entry["cached_tokens"] == 1860
    assert entry["uncached_input_tokens"] == 140
    assert entry["cache_hit_ratio"] == 0.93


def test_uncached_input_shape_still_reads_as_cached_plus_input() -> None:
    """WorkBuddy's DeepSeek shape reports UNCACHED input apart from cache reads.

    The discriminator is cached > input, which the provider only produces when
    its input count excludes cache reads. Both shapes must survive the same
    aggregation, including a workspace that serves both.
    """

    telemetry = ModelTelemetry()

    _record(telemetry, usage_in=140, usage_cached=1860, usage_out=10)

    entry = telemetry.snapshot()["models"]["gpt-6.1-terra"]

    assert entry["cached_tokens"] == 1860
    assert entry["uncached_input_tokens"] == 140
    assert entry["cache_hit_ratio"] == 0.93

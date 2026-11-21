"""Tests for the cache-reset attribution probe.

Tests cover cache usage accounting and what correlated journal rows support.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_MODULE_PATH = ROOT / "scripts" / "cpa_cache_reset_probe.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("cpa_cache_reset_probe", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules, so the module has
    # to be registered before exec_module runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def _line(
    stamp: str = "2026-11-14 07:13:34",
    lane: str = "chatgpt-oauth",
    model: str = "gpt-6-luna",
    conversation: str = "abc123",
    instance: str = "-",
    usage_in: int = 1000,
    usage_cached: int | str = 900,
    status: str = "200",
    successful: str = "true",
) -> str:
    return (
        "{0} INFO upstream_result lane={1} model={2} status={3} capacity=false "
        "retry_after=absent waited_ms=1 successful={8} upstream_header_ms=10 "
        "upstream_first_byte_ms=11 first_text_ms=12 usage_in={4} usage_cached={5} "
        "usage_out=8 request_id=deadbeef route=responses "
        "client_request_hash={6} instance_hash={7}".format(
            stamp,
            lane,
            model,
            status,
            usage_in,
            usage_cached,
            conversation,
            instance,
            successful,
        )
    )


def test_probe_traffic_is_not_a_prefix() -> None:
    """A 53-token probe with no cache credit is not a cold restart."""

    turns = probe.parse_journal([_line(usage_in=53, usage_cached=0)])
    assert probe.attribute(turns) == []


def test_large_uncached_turn_is_a_restart() -> None:
    turns = probe.parse_journal([_line(usage_in=240_767, usage_cached=0)])
    findings = probe.attribute(turns)
    assert len(findings) == 1
    assert findings[0].turn.usage_in == 240_767


def test_provider_variant_is_not_a_model_switch() -> None:
    """flash-n is the provider echo of flash-high; both normalise to one name."""

    assert probe.normalise_model("gemini-3.8-flash-n") == "gemini-3.8-flash-high"
    assert probe.normalise_model("gemini-3.8-flash-control") == "gemini-3.8-flash-high"
    assert probe.normalise_model("gemini-3.8-flash") == "gemini-3.8-flash-high"
    assert probe.normalise_model("gemini-4-hydrogen") == "gemini-4-hydrogen-xhigh"
    assert probe.normalise_model("gemini-4-carbon") == "gemini-4-carbon-high"
    assert probe.normalise_model("gemini-4-argon") == "gemini-4-argon-high"
    assert probe.normalise_model("gemini-4-argon-trial") == "gemini-4-argon-high"
    assert probe.normalise_model("gemini-4-neon") == "gemini-4-neon-xhigh"
    # 2026-11-17: the luode cutover serves hy4flash-free upstream (echoed
    # bare); the retired hy4free upstream echoed hy4-free and keeps its
    # historical label so old journal windows stay stable.
    assert probe.normalise_model("hy4-flash-free") == "hy4flash-free"
    assert probe.normalise_model("hy4-free") == "hy4free"
    assert probe.normalise_model("gpt-6-luna") == "gpt-6-luna"

    lines = [
        _line(
            stamp="2026-11-14 08:00:00",
            model="gemini-3.8-flash-high",
            usage_in=90_000,
            usage_cached=88_000,
        ),
        _line(
            stamp="2026-11-14 08:01:00",
            model="gemini-3.8-flash-n",
            usage_in=91_000,
            usage_cached=0,
        ),
    ]
    findings = probe.attribute(probe.parse_journal(lines))
    assert [f.mechanism for f in findings] == ["warm_to_cold"]


def test_model_switch_is_named_when_the_client_changed_model() -> None:
    lines = [
        _line(model="gpt-6.1-terra", usage_in=95_495, usage_cached=3_712),
        _line(model="gpt-6-luna", usage_in=240_767, usage_cached=0),
    ]
    findings = probe.attribute(probe.parse_journal(lines))
    assert [f.mechanism for f in findings] == ["model_switch"]
    assert "gpt-6.1-terra" in findings[0].detail


def test_lane_switch_is_a_model_switch_in_practice() -> None:
    """A lane is a function of the model name, so the model check wins first.

    The lane assertion exists only for a cross-lane pooled alias. Pinning the
    current precedence keeps a future refactor from silently reordering the two
    checks and relabelling a client model change as a gateway lane change.
    """

    lines = [
        _line(
            stamp="2026-11-14 09:00:00",
            lane="workbuddy-oauth",
            model="deepseek-v4.1-flash",
            usage_in=80_000,
            usage_cached=79_000,
        ),
        _line(
            stamp="2026-11-14 09:01:00",
            lane="zhipu-coding-plan",
            model="glm-5.3-flash",
            usage_in=50_000,
            usage_cached=0,
        ),
    ]
    findings = probe.attribute(probe.parse_journal(lines))
    assert [f.mechanism for f in findings] == ["model_switch"]

    # Same model name, different lane: only a pooled alias can produce this,
    # and then the lane really is the distinguishing fact.
    lines = [
        _line(
            stamp="2026-11-14 09:00:00",
            lane="workbuddy-oauth",
            model="deepseek-v4.1-flash",
            usage_in=80_000,
            usage_cached=79_000,
        ),
        _line(
            stamp="2026-11-14 09:01:00",
            lane="passthrough",
            model="deepseek-v4.1-flash",
            usage_in=50_000,
            usage_cached=0,
        ),
    ]
    findings = probe.attribute(probe.parse_journal(lines))
    assert [f.mechanism for f in findings] == ["lane_switch"]


def test_client_instance_switch_outranks_model_switch() -> None:
    """The observed instance change comes from the client header."""

    lines = [
        _line(
            instance="aaaa", model="gpt-6-luna", usage_in=100_000, usage_cached=99_000
        ),
        _line(instance="bbbb", model="gpt-6-luna", usage_in=101_000, usage_cached=0),
    ]
    findings = probe.attribute(probe.parse_journal(lines))
    assert [f.mechanism for f in findings] == ["client_instance_switch"]


def test_consecutive_cold_turns_are_unattributed() -> None:

    lines = [
        _line(usage_in=213_752, usage_cached=0),
        _line(usage_in=232_012, usage_cached=0),
    ]
    findings = probe.attribute(probe.parse_journal(lines))
    assert [f.mechanism for f in findings] == ["unattributed", "unattributed"]


def test_summary_counts_cold_input_tokens_and_ratio() -> None:
    lines = [
        _line(usage_in=100_000, usage_cached=100_000),
        _line(usage_in=240_000, usage_cached=0),
    ]
    turns = probe.parse_journal(lines)
    summary = probe.summarise(turns, probe.attribute(turns))
    assert summary["restarts"] == 1
    assert summary["cold_input_tokens"] == 240_000
    assert summary["restart_lost_tokens"] == 240_000
    assert summary["cache_hit_ratio"] == pytest.approx(100_000 / 340_000, abs=1e-4)
    assert summary["mechanisms"] == {"warm_to_cold": 1}


def test_failed_and_unreported_cache_usage_do_not_inflate_misses() -> None:
    lines = [
        _line(usage_in=240_000, usage_cached=0, status="400"),
        _line(usage_in=240_000, usage_cached=0, successful="false"),
        _line(usage_in=240_000, usage_cached="na"),
        _line(usage_in=100_000, usage_cached=90_000),
    ]
    turns = probe.parse_journal(lines)
    summary = probe.summarise(turns, probe.attribute(turns))
    assert summary["turns"] == 2
    assert summary["turns_with_usage"] == 2
    assert summary["turns_with_cache_usage"] == 1
    assert summary["restarts"] == 0
    assert summary["cache_hit_ratio"] == 0.9


def test_additive_provider_usage_stays_bounded_and_eligible() -> None:
    """Providers that report uncached input in usage_in must not exceed 1.0
    or drop warm hits below min_prefix_tokens from cache_eligible_turns."""

    lines = [
        _line(
            stamp="2026-11-18 01:00:00",
            lane="workbuddy-oauth",
            model="deepseek-v4.1-flash",
            usage_in=1_500,
            usage_cached=200_000,
        ),
        _line(
            stamp="2026-11-18 01:01:00",
            lane="workbuddy-oauth",
            model="deepseek-v4.1-flash",
            usage_in=205_000,
            usage_cached=0,
        ),
    ]
    turns = probe.parse_journal(lines)
    summary = probe.summarise(turns, probe.attribute(turns))
    expected_ratio = 200_000 / (200_000 + 1_500 + 205_000)
    assert summary["cache_eligible_turns"] == 2
    assert summary["cache_hit_ratio"] == pytest.approx(expected_ratio, abs=1e-4)
    assert summary["eligible_cache_hit_ratio"] == pytest.approx(
        expected_ratio, abs=1e-4
    )


def test_filter_hours_anchors_on_newest_turn(tmp_path: Path, capsys: Any) -> None:
    journal = tmp_path / "adm.log"
    journal.write_text(
        "\n".join(
            [
                _line(
                    stamp="2026-11-15 01:00:00",
                    conversation="old",
                    usage_in=210_000,
                    usage_cached=0,
                ),
                _line(
                    stamp="2026-11-18 01:00:00",
                    conversation="recent",
                    usage_in=100_000,
                    usage_cached=98_000,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert probe.main(["--file", str(journal), "--hours", "24", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"turns": 1' in out
    assert '"restarts": 0' in out


def test_render_emits_no_credential_or_prompt_shapes() -> None:
    lines = [_line(usage_in=240_000, usage_cached=0)]
    turns = probe.parse_journal(lines)
    text = probe.render(
        probe.summarise(turns, probe.attribute(turns)), probe.attribute(turns)
    )
    assert "==cache-reset-probe==" in text
    for forbidden in ("request_id=", "sk-", "Bearer", "access_token", "prompt"):
        assert forbidden not in text


def test_cli_requires_a_source() -> None:
    with pytest.raises(SystemExit):
        probe.main([])
    with pytest.raises(SystemExit):
        probe.main(["--file", "a.log", "--ssh-profile", "bwg"])
    with pytest.raises(SystemExit):
        probe.main(["--file", "a.log", "--hours", "0"])


def test_cli_ssh_profile_stages_remote_probe(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    import subprocess as sp

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> sp.CompletedProcess[str]:
        captured["argv"] = argv
        return sp.CompletedProcess(
            argv,
            0,
            stdout='{"summary": {"restarts": 0}, "restarts": []}\n',
            stderr="",
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    rc = probe.main(["--ssh-profile", "bwg", "--hours", "24", "--json"])
    assert rc == 0
    joined = " ".join(captured["argv"])
    assert "--profile bwg" in joined
    assert "--strict-host-key-checking" in joined
    assert "24 hours ago" in joined
    assert "--file -" in joined
    assert '"restarts": 0' in capsys.readouterr().out


def test_cli_reads_a_file_and_emits_json(tmp_path: Path) -> None:
    journal = tmp_path / "adm.log"
    journal.write_text(
        _line(usage_in=100_000, usage_cached=99_000)
        + "\n"
        + _line(usage_in=240_000, usage_cached=0)
        + "\n",
        encoding="utf-8",
    )
    assert probe.main(["--file", str(journal), "--json"]) == 0


def test_cli_reports_unreadable_input(tmp_path: Path, capsys: Any) -> None:
    assert probe.main(["--file", str(tmp_path / "missing.log")]) == 2
    assert "input-error" in capsys.readouterr().err


def test_cli_reads_stdin_when_file_is_dash(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    import io

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            _line(usage_in=100_000, usage_cached=99_000)
            + "\n"
            + _line(usage_in=240_000, usage_cached=0)
            + "\n"
        ),
    )
    assert probe.main(["--file", "-", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"restarts": 1' in out

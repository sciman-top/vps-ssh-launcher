"""Probes in the projected relay checker must distinguish failure from zero.

``grep -c ... || true`` collapsed "the tool failed" and "the count is zero"
into one value, and ``== 1`` rejected a legitimate overlapping reconnect. Both
now have explicit semantics, pinned here because the script only runs against a
live pair of hosts.
"""

from __future__ import annotations

from typing import Any


from scripts.remote import frp_bwg_relay as relay


class _Run:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self.mapping = mapping

    def __call__(self, command: str) -> str:
        for prefix, value in self.mapping.items():
            if command.startswith(prefix):
                if isinstance(value, BaseException):
                    raise value
                return str(value)
        return ""


LISTENING = """LISTEN 0 4096 *:443 *:*
LISTEN 0 4096 127.0.0.1:8317 0.0.0.0:*
"""


def test_a_listening_port_is_counted_from_the_listing() -> None:
    run = _Run({"ss -Hltn": LISTENING})
    assert relay._count_listening(run, "443") == 1
    assert relay._count_listening(run, "9999") == 0


def test_two_overlapping_established_sessions_are_not_a_failure() -> None:
    listing = (
        "ESTAB 0 0 10.0.0.1:5555 8.163.41.198:27000\n"
        "ESTAB 0 0 10.0.0.1:5556 8.163.41.198:27000\n"
    )
    assert (
        relay._count_established(
            _Run({"ss -Htn state established": listing}), "8.163.41.198:27000"
        )
        == 2
    )


def test_no_established_session_counts_as_zero_not_an_error() -> None:
    assert relay._count_established(_Run({}), "8.163.41.198:27000") == 0


def test_a_failed_journal_read_is_not_reported_as_zero_lines() -> None:
    run = _Run({"journalctl": relay.RelayCheckError("CHECK=FAILED_EXIT_1")})
    summary = relay._recent_log_summary(run)
    assert summary.startswith("UNAVAILABLE:"), summary


def test_a_readable_journal_reports_its_line_count() -> None:
    run = _Run({"journalctl": "login ok\nproxy start\n"})
    assert relay._recent_log_summary(run) == "LINES=2"

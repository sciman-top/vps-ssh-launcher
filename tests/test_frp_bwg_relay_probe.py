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


# A real `ss -Htn state established` line, verbatim from the hosts: Recv-Q and
# Send-Q come first, then the local and peer addresses.
SERVER_SIDE = (
    "0      0                     172.28.242.82:27000        144.34.229.116:2940"
)
CLIENT_SIDE = (
    "0      0                     144.34.229.116:2940        8.163.41.198:27000"
)


def test_control_connection_counts_from_either_end() -> None:
    # The aliyun branch reported 0 forever: the listening port is in the LOCAL
    # column there, while the original check only looked at the line tail (the
    # client ephemeral port). That is "failure looks like zero" one level down.
    assert relay._count_established(_Run({"ss -Htn": SERVER_SIDE}), ":27000") == 1
    assert (
        relay._count_established(_Run({"ss -Htn": CLIENT_SIDE}), "8.163.41.198:27000")
        == 1
    )


def test_control_count_ignores_the_queue_columns() -> None:
    # Recv-Q/Send-Q are the first two columns and are "0" here; scanning them
    # instead of the address columns silently matched nothing and turned a
    # healthy relay into a zero.
    listing = "0      0   127.0.0.1:45987   127.0.0.1:11242"
    assert relay._count_established(_Run({"ss -Htn": listing}), ":27000") == 0
    assert relay._count_established(_Run({"ss -Htn": listing}), ":45987") == 1
    assert relay._count_established(_Run({"ss -Htn": listing}), ":11242") == 1


def test_control_count_is_zero_for_an_empty_listing() -> None:
    assert relay._count_established(_Run({"ss -Htn": ""}), ":27000") == 0


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

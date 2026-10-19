"""Tests for scripts/cockpit_non_oauth_replay.py and gate-wait-cap checks.

The replay tool sends the local bearer token in an Authorization header, so
its endpoint must never leave loopback. The wait-cap acceptance tool must
refuse a missing control exe before touching the live sidecar config and
must reap the scratch sidecar process before deleting its scratch dir.
"""

from __future__ import annotations

import contextlib
import io
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest import mock

REPLAY = cast(
    dict[str, Any],
    runpy.run_path(
        str(Path(__file__).parents[1] / "scripts" / "cockpit_non_oauth_replay.py")
    ),
)
WAIT_CAP = cast(
    dict[str, Any],
    runpy.run_path(
        str(Path(__file__).parents[1] / "scripts" / "cockpit_gate_wait_cap_check.py")
    ),
)
# run_path returns a copy of the module globals, but the loaded functions
# still resolve names through the original namespace, so patches must land
# there for the functions to see them.
replay_main = cast(Any, REPLAY["main"])
wait_cap_main = cast(Any, WAIT_CAP["main"])
terminate_process = cast(Any, WAIT_CAP["terminate_process"])
REPLAY_GLOBALS = cast(dict[str, Any], replay_main.__globals__)
WAIT_CAP_GLOBALS = cast(dict[str, Any], wait_cap_main.__globals__)


class NonOAuthReplayEndpointTests(unittest.TestCase):
    """--endpoint carries the bearer token; it must stay on loopback."""

    @staticmethod
    def _replay_argv(endpoint: str) -> list[str]:
        return [
            "cockpit_non_oauth_replay.py",
            "--config",
            "unused-config.toml",
            "--endpoint",
            endpoint,
        ]

    def test_loopback_endpoints_are_accepted(self) -> None:
        for endpoint in (
            "http://127.0.0.1:10909/v1/responses",
            "http://localhost:10909/v1/responses",
            "http://[::1]:10909/v1/responses",
        ):
            run_mock = mock.Mock(return_value=0)
            with (
                self.subTest(endpoint=endpoint),
                mock.patch.dict(
                    REPLAY_GLOBALS,
                    {
                        "read_bearer": mock.Mock(return_value="token"),
                        "run": run_mock,
                    },
                ),
                mock.patch("sys.argv", self._replay_argv(endpoint)),
            ):
                self.assertEqual(replay_main(), 0)
            run_mock.assert_called_once()

    def test_non_loopback_endpoints_are_rejected(self) -> None:
        for endpoint in (
            "http://gateway.example.com/v1/responses",
            "http://203.0.113.7:10909/v1/responses",
            "http://[2001:db8::1]:10909/v1/responses",
        ):
            with (
                self.subTest(endpoint=endpoint),
                mock.patch.dict(
                    REPLAY_GLOBALS,
                    {"read_bearer": mock.Mock(side_effect=AssertionError("unread"))},
                ),
                mock.patch("sys.argv", self._replay_argv(endpoint)),
                contextlib.redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit) as ctx,
            ):
                replay_main()
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("loopback", stderr.getvalue())


class GateWaitCapControlTests(unittest.TestCase):
    """The control exe must be validated like the candidate exe, up front."""

    @staticmethod
    def _write_stub(tmp: Path, name: str) -> Path:
        stub = tmp / name
        stub.write_bytes(b"stub exe")
        return stub

    def _wait_cap_argv(self, candidate: Path, control: Path | None) -> list[str]:
        argv = [
            "cockpit_gate_wait_cap_check.py",
            "--candidate",
            str(candidate),
        ]
        if control is not None:
            argv += ["--control", str(control)]
        return argv

    def test_missing_control_exe_fails_before_touching_the_live_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidate = self._write_stub(tmp_path, "candidate.exe")
            unreachable = mock.Mock(
                side_effect=AssertionError("must not pass the control gate")
            )
            with (
                mock.patch.dict(WAIT_CAP_GLOBALS, {"find_live_dir": unreachable}),
                mock.patch(
                    "sys.argv", self._wait_cap_argv(candidate, tmp_path / "absent.exe")
                ),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
            ):
                self.assertEqual(wait_cap_main(), 2)
            self.assertIn("FAIL missing control exe", stdout.getvalue())

    def test_present_control_exe_passes_the_existence_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidate = self._write_stub(tmp_path, "candidate.exe")
            control = self._write_stub(tmp_path, "control.exe")
            sentinel = SystemExit("SENTINEL_LIVE_DIR_PROBE")
            probe = mock.Mock(side_effect=sentinel)
            with (
                mock.patch.dict(WAIT_CAP_GLOBALS, {"find_live_dir": probe}),
                mock.patch("sys.argv", self._wait_cap_argv(candidate, control)),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    wait_cap_main()
            self.assertIs(ctx.exception, sentinel)


class ScratchProcessCleanupTests(unittest.TestCase):
    """terminate_process must reap the sidecar before the scratch dir goes."""

    def test_kill_and_reap_once_when_the_process_cooperates(self) -> None:
        proc = mock.Mock()
        proc.wait.return_value = 0
        terminate_process(proc)
        proc.kill.assert_called_once_with()
        proc.wait.assert_called_once_with(timeout=10)

    def test_retries_kill_and_wait_when_the_first_reap_times_out(self) -> None:
        proc = mock.Mock()
        proc.wait.side_effect = [subprocess.TimeoutExpired("proc", 10), 0]
        terminate_process(proc)
        self.assertEqual(proc.kill.call_count, 2)
        self.assertEqual(proc.wait.call_count, 2)

    def test_second_reap_timeout_is_swallowed(self) -> None:
        proc = mock.Mock()
        proc.wait.side_effect = [
            subprocess.TimeoutExpired("proc", 10),
            subprocess.TimeoutExpired("proc", 10),
        ]
        terminate_process(proc)  # must not raise out of the cleanup path
        self.assertEqual(proc.kill.call_count, 2)

    def test_already_dead_process_is_tolerated(self) -> None:
        proc = mock.Mock()
        proc.kill.side_effect = OSError("process already exited")
        terminate_process(proc)  # must not raise
        proc.wait.assert_called_once_with(timeout=10)

    # run_case's finally also warns when the scratch dir survives rmtree;
    # that branch needs the real live sidecar config and a running exe, so it
    # stays covered by the manual WaitCapSimulation runbook, not by a unit test.


if __name__ == "__main__":
    unittest.main()

"""Remote command submission, stream draining, timeouts and capture caps."""

from __future__ import annotations

import argparse
import io
import threading
import time
import unittest
from contextlib import redirect_stdout
from typing import Any, cast
from unittest import mock

from vps_ssh_launcher import cli as ssh_tool
from vps_ssh_launcher import contracts, execution
from launcher_fakes import (
    BlockingChannel,
    FakeChannel,
    FakeClient,
    FakeFile,
    FakeStdin,
    patch_attr,
)


class ExecutionTests(unittest.TestCase):
    def test_submission_timeout_closes_client_for_open_and_exec_waits(self) -> None:
        for stage in ("channel open", "exec acknowledgement"):
            with self.subTest(stage=stage):
                release = threading.Event()
                client = mock.Mock()

                # Bind the loop variables as defaults: the closure is invoked
                # within the same iteration today, but the pattern is a trap
                # (B023) if the call ever moves out of the loop body.
                def stalled(
                    command: str,
                    stage: str = stage,
                    release: threading.Event = release,
                ) -> Any:
                    if not release.wait(2):
                        raise AssertionError("watchdog failed to close client")
                    raise RuntimeError(stage + " closed")

                client.exec_command.side_effect = stalled
                client.close.side_effect = release.set
                started = time.monotonic()
                with self.assertRaisesRegex(TimeoutError, "submission timed out"):
                    execution._submit_remote_command(client, "true", timeout=0.03)
                self.assertLess(time.monotonic() - started, 1)
                client.close.assert_called_once()

    def test_submission_success_cancels_watchdog(self) -> None:
        client = FakeClient(FakeChannel())
        timer = mock.Mock()
        with mock.patch.object(
            execution.threading, "Timer", return_value=timer
        ) as factory:
            execution._submit_remote_command(client, "true", timeout=1)
            # Even a callback already scheduled at cancellation must do nothing.
            factory.call_args.args[1]()
        timer.cancel.assert_called_once()
        self.assertFalse(client.closed)

    def test_hard_deadline_includes_submission_and_closes_streams(self) -> None:
        channel = FakeChannel(stdout_chunks=[b"too late"])
        client = FakeClient(channel)
        now = [0.0]
        original = client.exec_command

        def delayed(command: str) -> Any:
            now[0] = 5.0
            return original(command)

        with (
            mock.patch.object(client, "exec_command", side_effect=delayed),
            mock.patch.object(execution.time, "monotonic", side_effect=lambda: now[0]),
        ):
            with self.assertRaisesRegex(TimeoutError, "hard timeout of 3s"):
                execution.exec_remote(client, "true", command_hard_timeout=3)
        self.assertTrue(channel.closed)

    def test_exec_remote_reads_both_streams(self) -> None:
        channel = FakeChannel(
            stdout_chunks=[b"hello ", b"world\n"],
            stderr_chunks=[b"warn\n"],
            exit_status=7,
        )
        client = FakeClient(channel)

        code, out, err = execution.exec_remote(client, "echo test")

        self.assertEqual(code, 7)
        self.assertEqual(out, "hello world\n")
        self.assertEqual(err, "warn\n")
        self.assertFalse(client.closed)

    def test_exec_remote_uses_custom_command_timeout(self) -> None:
        channel = FakeChannel(stdout_chunks=[b"ok\n"], exit_status=0)
        client = FakeClient(channel)

        code, out, err = execution.exec_remote(
            client,
            "echo ok",
            command_timeout=120,
        )

        self.assertEqual(code, 0)
        self.assertEqual(out, "ok\n")
        self.assertEqual(err, "")
        self.assertEqual(channel.timeout, 120)

    def test_exec_remote_allows_disabling_command_timeout(self) -> None:
        channel = FakeChannel(stdout_chunks=[b"ok\n"], exit_status=0)
        client = FakeClient(channel)

        code, out, err = execution.exec_remote(
            client,
            "echo ok",
            command_timeout=0,
        )

        self.assertEqual(code, 0)
        self.assertEqual(out, "ok\n")
        self.assertEqual(err, "")
        self.assertIsNone(channel.timeout)

    def test_exec_remote_enforces_explicit_hard_timeout(self) -> None:
        channel = BlockingChannel()
        client = FakeClient(channel)
        monotonic_values = iter([0.0, 0.0, 1.0, 2.0, 4.0])

        with patch_attr(
            execution.time,
            "monotonic",
            side_effect=lambda: next(monotonic_values),
        ):
            with patch_attr(execution.time, "sleep", return_value=None):
                with self.assertRaisesRegex(TimeoutError, "hard timeout of 3s"):
                    execution.exec_remote(
                        client,
                        "sleep 10",
                        command_timeout=0,
                        command_hard_timeout=3,
                    )

        self.assertTrue(channel.closed)

    def test_exec_remote_closes_channel_after_success(self) -> None:
        channel = FakeChannel(stdout_chunks=[b"ok\n"], exit_status=0)
        client = FakeClient(channel)

        execution.exec_remote(client, "echo ok")

        self.assertTrue(channel.closed)

    def test_exec_remote_rejects_negative_command_timeout(self) -> None:
        channel = FakeChannel(stdout_chunks=[b"ok\n"], exit_status=0)
        client = FakeClient(channel)

        with self.assertRaisesRegex(ValueError, "timeout"):
            execution.exec_remote(client, "echo ok", command_timeout=-1)

    def test_exec_remote_rejects_invalid_exit_status(self) -> None:
        channel = FakeChannel(stdout_chunks=[b"done\n"], exit_status=-1)
        client = FakeClient(channel)

        with self.assertRaisesRegex(RuntimeError, "invalid exit status"):
            execution.exec_remote(client, "printf done")

    def test_exec_remote_closes_stream_handles(self) -> None:
        channel = FakeChannel(stdout_chunks=[b"ok\n"], stderr_chunks=[], exit_status=0)
        stdin = FakeStdin()
        stdout = FakeFile(channel)
        stderr = FakeFile(channel)

        class FakeHandleClient:
            def exec_command(
                self, command: str
            ) -> tuple[FakeStdin, FakeFile, FakeFile]:
                return stdin, stdout, stderr

        client = FakeHandleClient()

        code, out, err = execution.exec_remote(client, "echo ok")

        self.assertEqual(code, 0)
        self.assertEqual(out, "ok\n")
        self.assertEqual(err, "")
        self.assertTrue(stdout.closed)
        self.assertTrue(stderr.closed)

    def test_exec_remote_never_logs_remote_command_text(self) -> None:
        channel = FakeChannel(stdout_chunks=[b"ok\n"], stderr_chunks=[], exit_status=0)
        client = FakeClient(channel)

        with self.assertLogs("ssh_tool", level="DEBUG") as captured:
            code, out, err = execution.exec_remote(client, "printf harmless-value")

        self.assertEqual(code, 0)
        self.assertEqual(out, "ok\n")
        self.assertEqual(err, "")
        self.assertTrue(
            any("<redacted remote command; length=" in line for line in captured.output)
        )
        self.assertFalse(any("harmless-value" in line for line in captured.output))

    def test_exec_remote_preserves_utf8_split_across_channel_chunks(self) -> None:
        channel = FakeChannel(
            stdout_chunks=[bytes.fromhex("e2"), bytes.fromhex("82ac")],
            stderr_chunks=[],
            exit_status=0,
        )

        code, out, err = execution.exec_remote(FakeClient(channel), "printf euro")

        self.assertEqual((code, out, err), (0, "€", ""))

    def test_run_command_streams_output_before_remote_exit_is_observed(self) -> None:
        channel = FakeChannel(
            stdout_chunks=[b"partial\n"],
            stderr_chunks=[],
            exit_status=0,
        )
        stdout = io.StringIO()
        output_seen_at_exit_check: list[bool] = []
        original_exit_status_ready = channel.exit_status_ready

        def observed_exit_status_ready() -> bool:
            output_seen_at_exit_check.append(bool(stdout.getvalue()))
            return original_exit_status_ready()

        cast(Any, channel).exit_status_ready = observed_exit_status_ready
        with redirect_stdout(stdout):
            code = execution.run_command(FakeClient(channel), "demo")

        self.assertEqual(code, 0)
        self.assertTrue(any(output_seen_at_exit_check))
        self.assertEqual(stdout.getvalue(), "partial\n")

    def test_execute_remote_caps_captured_output_while_draining_stream(self) -> None:
        limit = contracts.RUN_ALL_OUTPUT_LIMIT
        channel = FakeChannel(
            stdout_chunks=[b"x" * (limit + 100)],
            stderr_chunks=[],
            exit_status=0,
        )

        code, out, err, stdout_truncated, stderr_truncated = (
            execution.execute_remote_capture(
                FakeClient(channel),
                "large-output",
                capture_limit=limit,
            )
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(out), limit)
        self.assertIn("stdout truncated", out)
        self.assertEqual(err, "")
        self.assertTrue(stdout_truncated)
        self.assertFalse(stderr_truncated)

    def test_command_hard_timeout_arg_contract(self) -> None:
        parser = ssh_tool.build_parser()
        args = parser.parse_args(
            [
                "run",
                "--command",
                "uptime",
                "--command-hard-timeout",
                "90",
            ]
        )
        self.assertEqual(args.command_hard_timeout, 90)

        # Unset means disabled (0), and negative values are rejected.
        self.assertEqual(
            execution.command_hard_timeout_arg(
                argparse.Namespace(command_hard_timeout=None)
            ),
            0,
        )
        with self.assertRaisesRegex(ValueError, "hard timeout"):
            execution.command_hard_timeout_arg(
                argparse.Namespace(command_hard_timeout=-1)
            )


if __name__ == "__main__":
    unittest.main()

"""Launcher CLI surface: argument parsing, main() dispatch and the legacy import facade."""

from __future__ import annotations

import argparse
import io
import sys
import unittest
from contextlib import redirect_stderr
from typing import Any, cast
from unittest import mock

from vps_ssh_launcher import cli as ssh_tool
from vps_ssh_launcher import batch, connection, contracts, execution
from vps_ssh_launcher import config as target_config
from launcher_fakes import (
    patch_attr,
)


class SSHToolTests(unittest.TestCase):
    def test_configuration_import_does_not_load_cli_or_ssh_runtime(self) -> None:
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import vps_ssh_launcher.config; "
                "assert not {'vps_ssh_launcher.cli', 'vps_ssh_launcher.connection', "
                "'vps_ssh_launcher.execution', 'vps_ssh_launcher.batch', 'paramiko'} "
                "& sys.modules.keys()",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_python_facade_preserves_public_capabilities(self) -> None:
        import ssh_tool as compatibility
        import vps_ssh_launcher

        # ssh_tool installs the CLI module as its legacy runtime alias.
        legacy = cast(Any, compatibility)
        self.assertIs(legacy, ssh_tool)
        self.assertIs(legacy.apply_config, target_config.apply_config)
        self.assertIs(legacy.connect_with_retry, connection.connect_with_retry)
        self.assertIs(legacy.exec_remote, execution.exec_remote)
        self.assertIs(legacy.run_on_all, batch.run_on_all)
        self.assertIs(legacy.ProfileRunResult, batch.ProfileRunResult)
        self.assertIs(
            legacy.ConnectionErrorClassification,
            connection.ConnectionErrorClassification,
        )
        self.assertEqual(legacy.CONNECT_TIMEOUT, contracts.CONNECT_TIMEOUT)
        self.assertEqual(vps_ssh_launcher.__version__, legacy.__version__)

    def test_parser_supports_stdin_password_without_cli_secret(self) -> None:
        args = ssh_tool.build_parser().parse_args(
            ["--password-stdin", "run", "--command", "uptime"]
        )

        self.assertTrue(args.password_stdin)
        self.assertIsNone(args.password)

    def test_parser_defaults_to_strict_host_key_checking(self) -> None:
        args = ssh_tool.build_parser().parse_args(["run", "--command", "uptime"])
        self.assertTrue(args.strict_host_key_checking)

        compatibility = ssh_tool.build_parser().parse_args(
            ["--allow-unknown-host-key", "run", "--command", "uptime"]
        )
        self.assertFalse(compatibility.strict_host_key_checking)

    def test_parser_rejects_two_password_sources(self) -> None:
        with self.assertRaises(SystemExit):
            ssh_tool.build_parser().parse_args(
                [
                    "--password",
                    "secret",
                    "--password-stdin",
                    "run",
                    "--command",
                    "uptime",
                ]
            )

    def test_main_handles_pre_target_oserror_without_unboundlocal(self) -> None:
        argv = ["ssh_tool", "--config", "target.json", "check"]

        with patch_attr(sys, "argv", argv):
            with patch_attr(ssh_tool, "apply_config", side_effect=OSError("denied")):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    code = ssh_tool.main()

        self.assertEqual(code, contracts.EXIT_CONFIG_ERROR)
        self.assertIn("Config error: denied", stderr.getvalue())

    def test_main_reports_known_target_oserror_as_network_error(self) -> None:
        argv = [
            "ssh_tool",
            "--host",
            "127.0.0.1",
            "--user",
            "root",
            "--password",
            "p",
            "check",
        ]

        def fake_apply_config(args: argparse.Namespace) -> None:
            args.host = "127.0.0.1"
            args.user = "root"
            args.port = 22

        with patch_attr(sys, "argv", argv):
            with patch_attr(ssh_tool, "apply_config", side_effect=fake_apply_config):
                with patch_attr(
                    ssh_tool,
                    "connect_with_retry",
                    side_effect=OSError("network down"),
                ):
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        code = ssh_tool.main()

        self.assertEqual(code, contracts.EXIT_NETWORK_ERROR)
        self.assertIn("[root@127.0.0.1:22] Network error", stderr.getvalue())

    def test_run_all_main_action_maps_missing_config_to_documented_exit_code(
        self,
    ) -> None:
        args = argparse.Namespace(
            config="definitely-missing.json",
            command="true",
            command_timeout=60,
            command_hard_timeout=0,
            password=None,
            key=None,
            allow_agent=False,
            strict_host_key_checking=False,
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = ssh_tool._run_all_main_action(args)

        self.assertEqual(code, contracts.EXIT_CONFIG_ERROR)
        self.assertIn("Config error:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_harden_stream_errors_reconfigures_supported_streams_only(self) -> None:
        class ReconfigurableStream:
            def __init__(self) -> None:
                self.kwargs: dict[str, Any] | None = None

            def reconfigure(self, **kwargs: Any) -> None:
                self.kwargs = kwargs

        stdout_stream = ReconfigurableStream()
        stderr_stream = ReconfigurableStream()

        with (
            mock.patch.object(sys, "stdout", stdout_stream),
            mock.patch.object(sys, "stderr", stderr_stream),
        ):
            ssh_tool._harden_stream_errors()

        self.assertEqual(stdout_stream.kwargs, {"errors": "backslashreplace"})
        self.assertEqual(stderr_stream.kwargs, {"errors": "backslashreplace"})

    def test_harden_stream_errors_leaves_unsupported_streams_untouched(
        self,
    ) -> None:
        captured = io.StringIO()

        with (
            mock.patch.object(sys, "stdout", captured),
            mock.patch.object(sys, "stderr", captured),
        ):
            ssh_tool._harden_stream_errors()

        self.assertEqual(captured.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

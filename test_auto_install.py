import io
import os
import sys
import unittest
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from typing import Any
from unittest import mock

import auto_install


class FakePexpect:
    EOF = object()
    TIMEOUT = object()


class FakeChild:
    def __init__(
        self,
        expect_indices: list[int],
        *,
        alive: bool = False,
        exitstatus: int | None = 0,
        signalstatus: int | None = None,
    ) -> None:
        self._expect_indices = list(expect_indices)
        self.alive = alive
        self.exitstatus = exitstatus
        self.signalstatus = signalstatus
        self.sent_lines: list[str] = []
        self.expect_calls = 0
        self.closed = False
        self.logfile_read: Any = None

    def expect(self, _patterns: Any, timeout: int | None = None) -> int:
        _ = timeout
        self.expect_calls += 1
        if not self._expect_indices:
            raise RuntimeError("No more scripted expect results")
        return self._expect_indices.pop(0)

    def sendline(self, text: str) -> None:
        self.sent_lines.append(text)

    def isalive(self) -> bool:
        return self.alive

    def close(self) -> None:
        self.closed = True
        self.alive = False


class FakeSpawnChild(FakeChild):
    def close(self, force: bool = False) -> None:
        _ = force
        super().close()


@contextmanager
def patch_env(
    mapping: MutableMapping[str, str],
    values: dict[str, str],
    *,
    clear: bool = False,
) -> Iterator[None]:
    original = dict(mapping)
    if clear:
        mapping.clear()
    mapping.update(values)
    try:
        yield
    finally:
        mapping.clear()
        mapping.update(original)


class AutoInstallPromptTests(unittest.TestCase):
    def test_main_requires_explicit_execute_guard(self) -> None:
        stderr = io.StringIO()

        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            code = auto_install.main([])

        self.assertEqual(code, 2)
        self.assertIn("can rewrite live VPS proxy config", stderr.getvalue())

    def test_main_rejects_unsafe_expect_timeout(self) -> None:
        stderr = io.StringIO()

        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            code = auto_install.main(["--execute", "--expect-timeout", "60"])

        self.assertEqual(code, 2)
        self.assertIn("unknown installer prompts abort", stderr.getvalue())

    def test_main_rejects_invalid_expect_timeout_env(self) -> None:
        stderr = io.StringIO()
        original = os.environ.get(auto_install.EXPECT_TIMEOUT_ENV)
        os.environ[auto_install.EXPECT_TIMEOUT_ENV] = "not-an-int"
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = auto_install.main(["--execute"])
        finally:
            if original is None:
                os.environ.pop(auto_install.EXPECT_TIMEOUT_ENV, None)
            else:
                os.environ[auto_install.EXPECT_TIMEOUT_ENV] = original

        self.assertEqual(code, 2)
        self.assertIn("must be an integer", stderr.getvalue())

    def test_previous_install_config_prompt_is_answered(self) -> None:
        patterns, _responses = auto_install._prompt_plan("demo.example")
        prompt_idx = patterns.index(r"读取到上次安装的配置，是否使用")
        eof_idx = len(patterns)
        child = FakeChild([prompt_idx, eof_idx])

        with redirect_stdout(io.StringIO()):
            result = auto_install._drive_prompts(
                child,
                FakePexpect,
                install_domain="demo.example",
                expect_timeout=1,
                max_responses=10,
            )

        self.assertEqual(result.stop_reason, "eof")
        self.assertEqual(child.sent_lines, ["n"])

    def test_drive_prompts_specific_generic_then_eof(self) -> None:
        patterns, _responses = auto_install._prompt_plan("demo.example")
        domain_idx = patterns.index(r"域名:")
        generic_idx = len(patterns) - 1
        eof_idx = len(patterns)
        child = FakeChild([domain_idx, generic_idx, eof_idx])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = auto_install._drive_prompts(
                child,
                FakePexpect,
                install_domain="demo.example",
                expect_timeout=1,
                max_responses=10,
            )

        self.assertEqual(result.stop_reason, "eof")
        self.assertEqual(result.sent_count, 2)
        self.assertEqual(child.sent_lines, ["demo.example", "2"])
        text = stdout.getvalue()
        self.assertIn("Specific", text)
        self.assertIn("Sent: <redacted>", text)
        self.assertNotIn("demo.example", text)
        self.assertIn("EOF after 2 responses", text)

    def test_drive_prompts_stops_on_timeout_index(self) -> None:
        patterns, _responses = auto_install._prompt_plan("demo.example")
        timeout_idx = len(patterns) + 1
        child = FakeChild([timeout_idx])

        with redirect_stdout(io.StringIO()):
            result = auto_install._drive_prompts(
                child,
                FakePexpect,
                install_domain="demo.example",
                expect_timeout=1,
                max_responses=10,
            )

        self.assertEqual(result.stop_reason, "timeout")
        self.assertEqual(result.sent_count, 0)
        self.assertEqual(child.sent_lines, [])

    def test_drive_prompts_stops_at_max_responses(self) -> None:
        patterns, _responses = auto_install._prompt_plan("demo.example")
        generic_idx = len(patterns) - 1
        child = FakeChild([generic_idx, generic_idx, generic_idx, generic_idx])

        with redirect_stdout(io.StringIO()):
            result = auto_install._drive_prompts(
                child,
                FakePexpect,
                install_domain="demo.example",
                expect_timeout=1,
                max_responses=3,
            )

        self.assertEqual(result.stop_reason, "max_responses")
        self.assertEqual(result.sent_count, 3)
        self.assertEqual(child.sent_lines, ["2", "1", ""])

    def test_drive_prompts_stops_on_repeated_generic_select_loop(self) -> None:
        patterns, _responses = auto_install._prompt_plan("demo.example")
        generic_idx = len(patterns) - 1
        child = FakeChild([generic_idx, generic_idx, generic_idx, generic_idx])

        with redirect_stdout(io.StringIO()):
            result = auto_install._drive_prompts(
                child,
                FakePexpect,
                install_domain="demo.example",
                expect_timeout=1,
                max_responses=10,
            )

        self.assertEqual(result.stop_reason, "repeated_prompt")

    def test_response_for_log_redacts_non_empty_values(self) -> None:
        self.assertEqual(auto_install._response_for_log("demo.example"), "<redacted>")
        self.assertEqual(auto_install._response_for_log(""), "<empty>")

    def test_drive_prompts_does_not_echo_exception_buffer(self) -> None:
        class ExplodingChild(FakeChild):
            def expect(self, _patterns: Any, timeout: int | None = None) -> int:
                raise RuntimeError("SECRET-IN-PEXPECT-BUFFER")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = auto_install._drive_prompts(
                ExplodingChild([]),
                FakePexpect,
                install_domain="demo.example",
            )

        self.assertEqual(result.stop_reason, "error")
        self.assertIn("RuntimeError", stdout.getvalue())
        self.assertNotIn("SECRET-IN-PEXPECT-BUFFER", stdout.getvalue())

    def test_wait_for_child_exit_attempts_expect_when_alive(self) -> None:
        child = FakeChild([0], alive=True)

        auto_install._wait_for_child_exit(child, FakePexpect, expect_timeout=1)

        self.assertEqual(child.expect_calls, 1)

    def test_main_omits_transcript_instead_of_live_stdout_passthrough(
        self,
    ) -> None:
        fake_child = FakeSpawnChild([0], alive=False, exitstatus=0)

        class FakePexpectModule(FakePexpect):
            def spawn(self, *_args: Any, **_kwargs: Any) -> FakeSpawnChild:
                return fake_child

        fake_pexpect = FakePexpectModule()

        with patch_env(os.environ, {auto_install.EXECUTE_ENV: "1"}, clear=False):
            with mock.patch.object(
                auto_install,
                "_load_pexpect",
                return_value=fake_pexpect,
            ):
                with mock.patch.object(
                    auto_install,
                    "_drive_prompts",
                    return_value=auto_install.PromptDriveResult(
                        sent_count=1,
                        stop_reason="eof",
                    ),
                ):
                    with mock.patch.object(
                        auto_install,
                        "INSTALL_SCRIPT",
                        mock.Mock(exists=mock.Mock(return_value=True)),
                    ):
                        stdout = io.StringIO()
                        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                            code = auto_install.main([])

        self.assertEqual(code, 0)
        self.assertIsNot(fake_child.logfile_read, sys.stdout)
        self.assertIn("Transcript omitted", stdout.getvalue())

    def test_main_terminates_installer_when_controller_raises(self) -> None:
        fake_child = FakeSpawnChild([], alive=True, exitstatus=None)

        class FakePexpectModule(FakePexpect):
            def spawn(self, *_args: Any, **_kwargs: Any) -> FakeSpawnChild:
                return fake_child

        with patch_env(os.environ, {auto_install.EXECUTE_ENV: "1"}, clear=False):
            with mock.patch.object(
                auto_install,
                "_load_pexpect",
                return_value=FakePexpectModule(),
            ):
                with mock.patch.object(
                    auto_install,
                    "_drive_prompts",
                    side_effect=RuntimeError("SECRET-CONTROLLER-BUFFER"),
                ):
                    with mock.patch.object(
                        auto_install,
                        "INSTALL_SCRIPT",
                        mock.Mock(exists=mock.Mock(return_value=True)),
                    ):
                        stderr = io.StringIO()
                        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                            code = auto_install.main([])

        self.assertEqual(code, 1)
        self.assertTrue(fake_child.closed)
        self.assertFalse(fake_child.isalive())
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn("SECRET-CONTROLLER-BUFFER", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

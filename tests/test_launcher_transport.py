"""Contract tests for the shared audit transport.

``scripts/launcher_transport.py`` exists because three audit tools
independently assembled the same argv payload and ``ssh_tool.py`` invocation,
and the copies had already drifted: one had no size guard at all. These tests
pin the decisions that must stay identical across the consumers, and the
budget failure that must happen locally instead of as a WinError on Windows.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from scripts import launcher_transport as transport


class PayloadBudgetTests(unittest.TestCase):
    def test_oversized_payload_fails_before_any_process_can_start(self) -> None:
        oversized = "A" * (transport.PAYLOAD_ARGV_BUDGET + 1)
        with self.assertRaisesRegex(ValueError, "CreateProcess"):
            transport.assert_payload_budget(oversized)

    def test_assembled_command_budget_accounts_for_the_launcher_argv(self) -> None:
        # The payload bound alone is not the real ceiling: the interpreter,
        # ssh_tool.py and the launcher flags ride the same command line.
        self.assertLess(transport.COMMAND_ARGV_BUDGET, 32767)
        self.assertGreater(transport.COMMAND_ARGV_BUDGET, transport.PAYLOAD_ARGV_BUDGET)
        command = "x" * (transport.COMMAND_ARGV_BUDGET + 1)
        with self.assertRaisesRegex(ValueError, "one argv element"):
            transport.assert_command_budget(command)

    def test_remote_command_is_rejected_when_its_scaffolding_does_not_fit(self) -> None:
        # A payload inside the payload bound can still overflow once the staging
        # scaffolding and a long host path in the action are added. Borrow the
        # command head from the real builder so the comparison is exact.
        scaffold = transport.remote_temp_python_command(
            payload="QUJD",
            temp_prefix="fixture",
            action="true",
        ).replace("QUJD", "")
        # Stay inside the payload bound and make the action pay the difference:
        # that is exactly the case the payload check alone cannot catch.
        payload = "A" * transport.PAYLOAD_ARGV_BUDGET
        # scaffold still carries the 4-char "true" action it was built with.
        overhead = len(scaffold) - len("true") + len(payload)
        action = "t" * (transport.COMMAND_ARGV_BUDGET - overhead + 1)
        with self.assertRaisesRegex(ValueError, "one argv element"):
            transport.remote_temp_python_command(
                payload=payload,
                temp_prefix="fixture",
                action=action,
            )

    def test_payload_at_exactly_the_budget_is_accepted(self) -> None:
        at_budget = "A" * transport.PAYLOAD_ARGV_BUDGET
        self.assertEqual(transport.assert_payload_budget(at_budget), at_budget)

    def test_both_encodings_enforce_the_same_budget(self) -> None:
        # Random bytes are incompressible, so both forms must trip the guard on
        # the same input: the limit is about argv characters, not bytes on the
        # wire, and it is applied after encoding.
        huge = os.urandom(transport.PAYLOAD_ARGV_BUDGET * 3)
        for encoding in (transport.ENCODING_ZLIB, transport.ENCODING_BASE64):
            with self.subTest(encoding=encoding):
                with self.assertRaises(ValueError):
                    transport.encode_payload(huge, encoding=encoding)

    def test_unknown_encoding_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "encoding"):
            transport.encode_payload(b"x", encoding="rot13")


class PayloadEncodingTests(unittest.TestCase):
    def test_compressed_payload_round_trips_and_is_argv_safe(self) -> None:
        source = b"def main():\n    print('audit')\n" * 20
        payload = transport.encode_source_payload(source)
        self.assertEqual(zlib.decompress(base64.b64decode(payload)), source)
        # A newline or quote in the payload would break printf single-quoting.
        self.assertNotIn("\n", payload)
        self.assertNotIn("'", payload)

    def test_plain_payload_round_trips_and_is_argv_safe(self) -> None:
        source = b"print('forensics')\n"
        payload = transport.encode_payload(source, encoding=transport.ENCODING_BASE64)
        self.assertEqual(base64.b64decode(payload), source)
        self.assertNotIn("\n", payload)
        self.assertNotIn("'", payload)

    def test_staged_copy_threshold_matches_the_shared_budget(self) -> None:
        # cpa_natural_traffic_audit.py keeps an inline threshold because its
        # staged copy must run with no repository imports. Pin the two values
        # together so one path cannot grow past the other.
        text = (
            Path(__file__).resolve().parents[1] / "scripts/cpa_natural_traffic_audit.py"
        ).read_text(encoding="utf-8")
        self.assertIn(f"if len(payload) > {transport.PAYLOAD_ARGV_BUDGET}:", text)

    def test_compression_is_what_keeps_the_real_tools_inside_the_budget(self) -> None:
        # The guard is only survivable because the payload is compressed. If
        # this ever stopped holding, the shipped tools would start failing
        # locally -- useful to know, so pin the margin instead of discovering it.
        source = (
            Path(__file__).resolve().parents[1] / "scripts/cpa_status.py"
        ).read_bytes()
        compressed = base64.b64encode(zlib.compress(source, level=9)).decode("ascii")
        plain = base64.b64encode(source).decode("ascii")
        self.assertLess(len(compressed), transport.PAYLOAD_ARGV_BUDGET)
        self.assertGreater(len(plain), transport.PAYLOAD_ARGV_BUDGET)


class RemoteCommandShapeTests(unittest.TestCase):
    def test_compressed_command_decodes_twice_and_cleans_up(self) -> None:
        command = transport.remote_temp_python_command(
            payload="QUJD",
            temp_prefix="fixture",
            action='python3 "$tmp" --remote --json',
        )
        self.assertIn("umask 077", command)
        self.assertIn("mktemp /tmp/fixture.XXXXXXXXXX.py", command)
        self.assertIn("trap 'rm -f -- \"$tmp\"' EXIT", command)
        self.assertIn("zlib.decompress", command)
        self.assertTrue(command.endswith('> "$tmp" && python3 "$tmp" --remote --json'))

    def test_plain_command_decodes_once_and_never_invokes_python(self) -> None:
        command = transport.remote_temp_python_command(
            payload="QUJD",
            temp_prefix="fixture",
            action='python3 "$tmp" --remote --json',
            encoding=transport.ENCODING_BASE64,
        )
        self.assertIn("printf %s 'QUJD' | base64 -d | cat > \"$tmp\"", command)
        # The plain form must not pay a second Python decode stage; that shape
        # is what the live on-host forensics probe verifies.
        self.assertEqual(command.count("python3"), 1)
        # The file must still start private and be removed on every exit path.
        self.assertIn("umask 077", command)
        self.assertIn("trap 'rm -f -- \"$tmp\"' EXIT", command)

    def test_temp_prefix_cannot_escape_the_temp_directory(self) -> None:
        for bad in ("../escape", "with space", ""):
            with self.subTest(prefix=bad):
                with self.assertRaises(ValueError):
                    transport.remote_temp_python_command(
                        payload="QUJD",
                        temp_prefix=bad,
                        action="true",
                    )


class LauncherArgvTests(unittest.TestCase):
    def test_run_options_follow_the_subcommand(self) -> None:
        argv = transport.launcher_argv(
            repo_root=Path("/repo"),
            command="true",
            profile="bwg",
            config=Path("/repo/target.json"),
            idle_timeout_seconds=900,
            hard_timeout_seconds=930,
        )
        self.assertEqual(argv[0], str(Path("/repo/ssh_tool.py")))
        self.assertIn("--strict-host-key-checking", argv)
        # It is a top-level option, so it must precede the subcommand.
        self.assertLess(argv.index("--strict-host-key-checking"), argv.index("run"))
        self.assertLess(argv.index("--config"), argv.index("--profile"))
        self.assertLess(argv.index("--profile"), argv.index("run"))
        # --command-timeout belongs to the run subparser; emitting it before
        # "run" is rejected by argparse, which is why this ordering is pinned.
        self.assertLess(argv.index("run"), argv.index("--command"))
        self.assertLess(argv.index("--command"), argv.index("--command-timeout"))
        self.assertEqual(argv[argv.index("--command") + 1], "true")

    def test_unset_options_are_omitted_so_cli_defaults_apply(self) -> None:
        argv = transport.launcher_argv(repo_root=Path("/repo"), command="true")
        # Only the pinned trust policy is unconditional; the timeouts and the
        # profile still fall through to the CLI.
        self.assertEqual(
            argv[1:], ["--strict-host-key-checking", "run", "--command", "true"]
        )


class ConsumerWiringTests(unittest.TestCase):
    """The consumers must use the shared module, not a private copy."""

    def test_audit_tools_delegate_to_the_shared_transport(self) -> None:
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        for name in ("cpa_status.py", "cpa_error_dump_forensics.py"):
            with self.subTest(tool=name):
                text = (scripts / name).read_text(encoding="utf-8")
                self.assertIn("launcher_transport", text)
                self.assertNotIn("_PAYLOAD_ARGV_BUDGET = 24000", text)

    def test_uploaded_audit_tools_start_without_the_repository(self) -> None:
        # Both tools upload THEMSELVES and run ``--remote`` on the host, where
        # neither ``scripts`` nor ``bootstrap`` exists. A module-level import of
        # the local transport therefore breaks the on-host probe at startup --
        # this was a real regression, found by running the generated command on
        # Linux, and it survived the whole local suite because every local test
        # has the repository on sys.path.
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        for name in ("cpa_error_dump_forensics.py", "cpa_status.py"):
            with self.subTest(tool=name):
                with tempfile.TemporaryDirectory() as directory:
                    uploaded = Path(directory) / name
                    uploaded.write_bytes((scripts / name).read_bytes())
                    env = os.environ.copy()
                    env.pop("PYTHONPATH", None)
                    completed = subprocess.run(
                        [sys.executable, str(uploaded), "--help"],
                        cwd=directory,
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=60,
                        check=False,
                    )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{name} cannot start on the host: "
                    f"{(completed.stdout or '')}{(completed.stderr or '')}",
                )

    def test_projected_host_runtime_never_imports_the_local_layer(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        source = repo_root / "scripts" / "cpa_status.py"
        text = source.read_text(encoding="utf-8")
        head = text.split("_transport()", 1)[0]
        self.assertNotIn("\nfrom scripts import launcher_transport", head)

        # Behaviour, not just text: cpa_status.py is byte-projected to
        # /opt/cpa-status/ and started by systemd with no repository on
        # sys.path. Copy it out and run the on-host mode for real; a
        # module-level repository import would fail exactly here.
        with tempfile.TemporaryDirectory() as directory:
            projected = Path(directory) / "cpa_status.py"
            projected.write_bytes(source.read_bytes())
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            completed = subprocess.run(
                [sys.executable, str(projected), "--remote"],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            (completed.stdout or "") + (completed.stderr or ""),
        )
        # The on-host document must still be the real one.
        document = json.loads(completed.stdout)
        self.assertEqual(document["schema"], 1)


if __name__ == "__main__":
    unittest.main()

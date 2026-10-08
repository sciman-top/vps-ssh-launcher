"""Launcher target-config parsing, profile selection and auth precedence."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from vps_ssh_launcher import config as target_config
from launcher_fakes import (
    patch_attr,
    patch_env,
)


class ConfigTests(unittest.TestCase):
    def test_named_profile_never_falls_back_to_legacy_root(self) -> None:
        config = {"host": "other.invalid", "user": "root", "password": "fixture"}
        with self.assertRaisesRegex(ValueError, "Explicit --profile"):
            target_config._select_config_entry(config, "bwg")
        self.assertEqual(
            target_config._select_config_entry(config, None), ("(root)", config)
        )
        self.assertEqual(
            target_config._select_config_entry({"profiles": {"bwg": config}}, "bwg"),
            ("bwg", config),
        )

    def test_apply_config_loads_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "port": 2222,
                                "user": "root",
                                "password": "secret",
                            }
                        },
                        "default": "alpha",
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                profile=None,
                host=None,
                port=None,
                user=None,
                password=None,
                key=None,
            )

            target_config.apply_config(args)

            self.assertEqual(args.host, "10.0.0.1")
            self.assertEqual(args.port, 2222)
            self.assertEqual(args.user, "root")
            self.assertEqual(args.password, "secret")

    def test_apply_config_skips_auto_config_for_complete_direct_target(self) -> None:
        args = argparse.Namespace(
            config=None,
            profile=None,
            host="direct.example",
            port=None,
            user="root",
            password="direct-password",
            key=None,
            allow_agent=False,
        )

        with patch_attr(
            target_config,
            "resolve_default_config_path",
            return_value=Path("auto-target.json"),
        ) as resolve_default:
            with patch_attr(
                target_config,
                "load_config",
                side_effect=AssertionError("auto config must not be loaded"),
            ) as load_config:
                target_config.apply_config(args)

        resolve_default.assert_not_called()
        load_config.assert_not_called()
        self.assertEqual(args.host, "direct.example")
        self.assertIsNone(args.port)

    def test_apply_config_allows_agent_only_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                            }
                        },
                        "default": "alpha",
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                profile=None,
                host=None,
                port=None,
                user=None,
                password=None,
                key=None,
                allow_agent=True,
            )

            target_config.apply_config(args)

            self.assertEqual(args.host, "10.0.0.1")
            self.assertEqual(args.port, 22)
            self.assertEqual(args.user, "root")
            self.assertIsNone(args.password)
            self.assertIsNone(args.key)

    def test_apply_config_cli_key_skips_missing_password_env(self) -> None:
        env_name = "VPS_SSH_TOOL_TEST_MISSING_PASSWORD"
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password_env": env_name,
                            }
                        },
                        "default": "alpha",
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                profile=None,
                host=None,
                port=None,
                user=None,
                password=None,
                key="  shared-key.pem  ",
                allow_agent=False,
            )

            with patch_env(os.environ, {}, clear=False):
                os.environ.pop(env_name, None)
                target_config.apply_config(args)

            self.assertEqual(args.host, "10.0.0.1")
            self.assertEqual(args.key, "shared-key.pem")
            self.assertIsNone(args.password)

    def test_apply_config_allow_agent_skips_missing_password_env(self) -> None:
        env_name = "VPS_SSH_TOOL_TEST_MISSING_PASSWORD"
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password_env": env_name,
                            }
                        },
                        "default": "alpha",
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                profile=None,
                host=None,
                port=None,
                user=None,
                password=None,
                key=None,
                allow_agent=True,
            )

            with patch_env(os.environ, {}, clear=False):
                os.environ.pop(env_name, None)
                target_config.apply_config(args)

            self.assertEqual(args.host, "10.0.0.1")
            self.assertEqual(args.port, 22)
            self.assertEqual(args.user, "root")
            self.assertIsNone(args.password)
            self.assertIsNone(args.key)

    def test_apply_config_resolves_relative_key_from_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "conf"
            config_dir.mkdir()
            config_path = config_dir / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "key": "keys/id_rsa",
                            }
                        },
                        "default": "alpha",
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                profile=None,
                host=None,
                port=None,
                user=None,
                password=None,
                key=None,
            )

            target_config.apply_config(args)

            self.assertEqual(args.key, str(config_dir / "keys" / "id_rsa"))

    def test_resolve_key_resolves_relative_path_against_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "conf"
            config_dir.mkdir()

            resolved = target_config._resolve_key(
                {"key": "keys/id_rsa"},
                config_dir=config_dir,
            )

            self.assertEqual(resolved, str(config_dir / "keys" / "id_rsa"))

    def test_select_profile_noninteractive_requires_profile_or_default(self) -> None:
        profiles = {
            "alpha": {"host": "10.0.0.1", "user": "root"},
            "beta": {"host": "10.0.0.2", "user": "root"},
        }

        with patch_attr(sys, "stdin", SimpleNamespace(isatty=lambda: False)):
            with self.assertRaisesRegex(ValueError, "Pass --profile"):
                target_config.select_profile(profiles, None, None)

    def test_load_config_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text("{invalid json", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                target_config.load_config(config_path)

            self.assertIn("Invalid JSON", str(ctx.exception))

    def test_coerce_port_rejects_float_like_value(self) -> None:
        with self.assertRaises(ValueError):
            target_config.coerce_port(22.7, context="test")

    def test_resolve_default_config_prefers_local_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_dir = Path(tmpdir)
            (script_dir / "target.json").write_text("{}", encoding="utf-8")
            with patch_attr(
                target_config,
                "user_config_path",
                return_value=Path(tmpdir) / "missing-target.json",
            ):
                self.assertEqual(
                    target_config.resolve_default_config_path(script_dir),
                    script_dir / "target.json",
                )

    def test_resolve_default_config_prefers_user_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_dir = Path(tmpdir) / "repo"
            script_dir.mkdir()
            (script_dir / "target.json").write_text("{}", encoding="utf-8")

            user_config = Path(tmpdir) / "target.json"
            user_config.write_text("{}", encoding="utf-8")

            with patch_attr(
                target_config, "user_config_path", return_value=user_config
            ):
                self.assertEqual(
                    target_config.resolve_default_config_path(script_dir),
                    user_config,
                )

    def test_validate_profile_rejects_invalid_profiles(self) -> None:
        cases: dict[str, tuple[dict[str, Any], str]] = {
            "empty password": (
                {"host": "10.0.0.1", "user": "root", "password": ""},
                "password",
            ),
            "blank key": ({"host": "10.0.0.1", "user": "root", "key": "  "}, "key"),
            "non-string password_env": (
                {"host": "10.0.0.1", "user": "root", "password_env": 123},
                "password_env",
            ),
            "blank host": ({"host": " ", "user": "root", "password": "secret"}, "host"),
        }
        for label, (entry, field) in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError) as ctx:
                    target_config.validate_profile(entry, "test")
                self.assertIn(field, str(ctx.exception))

    def test_profile_key_precedes_missing_password_environment(self) -> None:
        env_name = "VPS_SSH_TOOL_TEST_KEY_PRECEDENCE"
        args = argparse.Namespace(password=None, key=None, allow_agent=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch_env(os.environ, {}, clear=False):
                os.environ.pop(env_name, None)
                password, key = target_config.resolve_auth_for_entry(
                    {
                        "password_env": env_name,
                        "key": "keys/id_ed25519",
                    },
                    args,
                    config_dir=Path(tmpdir),
                )

        self.assertIsNone(password)
        self.assertEqual(key, str(Path(tmpdir) / "keys" / "id_ed25519"))

    def test_default_config_callers_search_from_source_root(self) -> None:
        args = argparse.Namespace(
            config=None,
            profile=None,
            host=None,
            port=22,
            user="root",
            password=None,
            key=None,
            allow_agent=False,
        )
        with patch_attr(
            target_config,
            "resolve_default_config_path",
            return_value=None,
        ) as resolve_default:
            target_config.apply_config(args)

        resolve_default.assert_called_once_with(target_config.SOURCE_ROOT)


if __name__ == "__main__":
    unittest.main()

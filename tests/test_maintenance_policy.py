"""Refusal contract for the maintenance policy loader.

The policy file decides which hosts, resources and unattended-apply windows a
run may touch, and its fingerprint is what a stored plan is revalidated
against. Each refusal below used to be reachable only through the CLI, so a
loosened check would have widened an unattended window instead of failing a
test.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vps_ssh_launcher.maintenance import config as maintenance_config

BASE = """
[settings]
strict_host_key_checking = true
command_timeout = 10

[profiles.bwg]
enabled = true

[profiles.bwg.resources]
cpa = "deferred"
"""


class PolicyRefusalTests(unittest.TestCase):
    def _load(self, body: str) -> maintenance_config.MaintenancePolicy:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "maintenance.toml"
            path.write_text(body, encoding="utf-8")
            return maintenance_config.load_policy(path)

    def test_the_minimal_policy_loads_with_documented_defaults(self) -> None:
        policy = self._load(BASE)
        self.assertTrue(policy.strict_host_key_checking)
        self.assertEqual(policy.profile_names(), ("bwg",))
        self.assertEqual(policy.automation.mode, "observe")

    def test_relaxed_host_key_checking_is_refused(self) -> None:
        body = BASE.replace(
            "strict_host_key_checking = true",
            "strict_host_key_checking = false",
        )
        with self.assertRaisesRegex(ValueError, "must remain true"):
            self._load(body)

    def test_a_non_boolean_strictness_flag_is_refused(self) -> None:
        body = BASE.replace(
            "strict_host_key_checking = true",
            'strict_host_key_checking = "yes"',
        )
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            self._load(body)

    def test_an_unknown_top_level_section_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported maintenance policy"):
            self._load(BASE + "\n[surprise]\nvalue = 1\n")

    def test_an_unknown_settings_field_is_refused(self) -> None:
        body = BASE.replace("command_timeout = 10", "command_timeout = 10\nretries = 3")
        with self.assertRaisesRegex(ValueError, "Unsupported settings"):
            self._load(body)

    def test_a_zero_or_boolean_command_timeout_is_refused(self) -> None:
        for value in ("0", "-1", "true"):
            with self.subTest(value=value):
                body = BASE.replace(
                    "command_timeout = 10", f"command_timeout = {value}"
                )
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    self._load(body)

    def test_an_empty_profiles_table_is_refused(self) -> None:
        body = "[settings]\nstrict_host_key_checking = true\n\n[profiles]\n"
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self._load(body)

    def test_an_unknown_profile_field_is_refused(self) -> None:
        body = BASE.replace("enabled = true", 'enabled = true\nregion = "cn"')
        with self.assertRaisesRegex(ValueError, "Unsupported profiles"):
            self._load(body)

    def test_a_non_string_resource_desire_is_refused(self) -> None:
        body = BASE.replace('cpa = "deferred"', "cpa = 1")
        with self.assertRaisesRegex(ValueError, "must be a string"):
            self._load(body)

    def test_an_unknown_pin_section_is_refused(self) -> None:
        body = BASE + '\n[pins.mystery]\nversion = "1.2.3"\n'
        with self.assertRaisesRegex(ValueError, "Unsupported maintenance pin"):
            self._load(body)

    def test_unattended_apply_without_the_exact_acknowledgement_is_refused(
        self,
    ) -> None:
        body = BASE + '\n[automation]\nmode = "unattended_apply"\n'
        with self.assertRaisesRegex(ValueError, "exact explicit acknowledgement"):
            self._load(body)

    def test_unattended_apply_accepts_the_documented_acknowledgement(self) -> None:
        body = (
            BASE
            + '\n[automation]\nmode = "unattended_apply"\n'
            + 'acknowledge = "I_ACKNOWLEDGE_BWG_SINGLE_HOST_AUTOMATION"\n'
        )
        policy = self._load(body)
        self.assertEqual(policy.automation.mode, "unattended_apply")

    def test_an_automation_window_that_does_not_advance_is_refused(self) -> None:
        body = BASE + '\n[automation]\nwindow_start = "22:00"\nwindow_end = "21:00"\n'
        with self.assertRaisesRegex(ValueError, "window_start must be before"):
            self._load(body)

    def test_a_malformed_automation_time_is_refused(self) -> None:
        body = BASE + '\n[automation]\nwindow_start = "8pm"\n'
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            self._load(body)

    def test_more_than_three_attempts_per_pin_is_refused(self) -> None:
        body = BASE + "\n[automation]\nmax_attempts_per_pin = 4\n"
        with self.assertRaisesRegex(ValueError, "must not exceed 3"):
            self._load(body)

    def test_an_unsupported_automation_resource_is_refused(self) -> None:
        body = BASE + '\n[automation]\nresources = ["mystery"]\n'
        with self.assertRaisesRegex(ValueError, "unsupported resources"):
            self._load(body)

    def test_duplicate_automation_profiles_are_refused(self) -> None:
        body = BASE + '\n[automation]\nprofiles = ["bwg", "bwg"]\n'
        with self.assertRaisesRegex(ValueError, "duplicates"):
            self._load(body)

    def test_an_unknown_automation_field_is_refused(self) -> None:
        body = BASE + "\n[automation]\nretries = 2\n"
        with self.assertRaisesRegex(ValueError, "Unsupported automation"):
            self._load(body)

    def test_an_invalid_toml_document_is_refused_with_the_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid TOML"):
            self._load("[settings\n")

    def test_a_missing_policy_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Unable to read"):
                maintenance_config.load_policy(Path(directory) / "absent.toml")


class PolicyFingerprintTests(unittest.TestCase):
    def test_the_fingerprint_tracks_policy_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.toml"
            second = root / "second.toml"
            first.write_text(BASE, encoding="utf-8")
            second.write_text(
                BASE.replace("command_timeout = 10", "command_timeout = 20"),
                encoding="utf-8",
            )
            a = maintenance_config.load_policy(first)
            b = maintenance_config.load_policy(second)
        # The fingerprint must not depend on the file name, only on content.
        self.assertNotEqual(a.fingerprint, b.fingerprint)

    def test_two_identical_policies_share_a_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.toml"
            second = root / "b.toml"
            first.write_text(BASE, encoding="utf-8")
            second.write_text(BASE, encoding="utf-8")
            a = maintenance_config.load_policy(first)
            b = maintenance_config.load_policy(second)
        self.assertEqual(a.fingerprint, b.fingerprint)


class DerivedPathTests(unittest.TestCase):
    def _policy(self, root: Path, body: str) -> maintenance_config.MaintenancePolicy:
        path = root / "maintenance.toml"
        path.write_text(body, encoding="utf-8")
        return maintenance_config.load_policy(path)

    def test_defaults_hang_off_the_policy_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self._policy(root, BASE)
            self.assertEqual(
                maintenance_config.policy_state_path(policy).parent,
                Path(policy.config_path).parent,
            )
            self.assertEqual(
                maintenance_config.policy_receipt_dir(policy).parent,
                Path(policy.config_path).parent,
            )
            self.assertEqual(
                maintenance_config.policy_lock_path(policy).parent,
                Path(policy.config_path).parent,
            )

    def test_explicit_relative_paths_are_anchored_at_the_policy_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self._policy(
                root,
                BASE.replace(
                    "command_timeout = 10",
                    'command_timeout = 10\nstate_path = "custom.db"\nreceipt_dir = "out"',
                ),
            )
            self.assertEqual(
                maintenance_config.policy_state_path(policy).name, "custom.db"
            )
            self.assertEqual(maintenance_config.policy_receipt_dir(policy).name, "out")

    def test_the_default_policy_path_uses_the_shared_user_data_root(self) -> None:
        self.assertEqual(
            maintenance_config.default_policy_path(),
            maintenance_config.app_config_dir() / "maintenance.toml",
        )


class LocalPathResolutionTests(unittest.TestCase):
    def test_a_relative_local_path_hangs_off_the_policy_directory(self) -> None:
        default = Path("/etc/vps-ssh-launcher/state.db")
        self.assertEqual(
            maintenance_config.resolve_local_path("state.db", default=default),
            Path("/etc/vps-ssh-launcher/state.db"),
        )
        self.assertEqual(
            maintenance_config.resolve_local_path("../shared.db", default=default),
            Path("/etc/vps-ssh-launcher/../shared.db"),
        )

    def test_an_unset_path_falls_back_to_the_default(self) -> None:
        default = Path("/etc/vps-ssh-launcher/state.db")
        self.assertEqual(
            maintenance_config.resolve_local_path(None, default=default), default
        )


if __name__ == "__main__":
    unittest.main()

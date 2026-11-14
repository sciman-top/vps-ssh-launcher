"""Refusal and ordering contract for the Xray and Docker plan builders.

A plan is the last place a wrong decision can still be caught cheaply: after
it, the adapter talks to the host. These builders decide blocked / noop /
planned from inventory facts, so each branch is pinned here rather than being
observed only through a full plan-and-apply flow.
"""

from __future__ import annotations

import unittest

from vps_ssh_launcher.maintenance import docker, xray
from vps_ssh_launcher.maintenance.models import MaintenanceAction

VERSION = "26.3.27"
SHA = "a" * 64
DIGEST = "sha256:" + "b" * 64


class XrayPinTests(unittest.TestCase):
    def test_a_valid_pin_normalises_both_fields(self) -> None:
        self.assertEqual(
            xray.normalize_pin({"version": "v26.3.27", "sha256": "A" * 64}),
            {"version": VERSION, "sha256": SHA},
        )

    def test_a_non_table_pin_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a TOML table"):
            xray.normalize_pin(["26.3.27"])

    def test_extra_or_missing_fields_are_refused(self) -> None:
        for value in (
            {"version": VERSION},
            {"version": VERSION, "sha256": SHA, "url": "https://x"},
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "exactly version and sha256"):
                    xray.normalize_pin(value)


class XrayPlanTests(unittest.TestCase):
    def _plan(
        self, observed: str, pin: dict[str, str] | None = None
    ) -> MaintenanceAction:
        return xray.plan_upgrade(
            "bwg", "upgrade", observed, pin or {"version": VERSION}
        )

    def test_a_missing_pin_blocks_the_upgrade(self) -> None:
        action = xray.plan_upgrade("bwg", "upgrade", "26.0.0", None)
        self.assertEqual(action.status, "blocked")
        self.assertIn("explicit version", action.reason)

    def test_matching_versions_are_a_noop(self) -> None:
        for observed in (VERSION, f"v{VERSION}"):
            with self.subTest(observed=observed):
                self.assertEqual(self._plan(observed).status, "noop")

    def test_unversioned_inventory_states_block_instead_of_guessing(self) -> None:
        for observed in ("absent", "unknown", "present"):
            with self.subTest(observed=observed):
                action = self._plan(observed)
                self.assertEqual(action.status, "blocked")
                self.assertIn("concrete Xray version", action.reason)

    def test_an_unparseable_observed_version_blocks(self) -> None:
        for observed in ("latest", "26.3", "26.3.27-rc1", ""):
            with self.subTest(observed=observed):
                action = self._plan(observed)
                self.assertEqual(action.status, "blocked")

    def test_a_newer_observed_version_is_not_downgraded(self) -> None:
        action = self._plan("26.9.1")
        self.assertEqual(action.status, "blocked")
        # The wording is "older than the installed version; refusing an
        # implicit downgrade" -- pin the meaning, not a guessed word.
        self.assertIn("downgrade", action.reason.lower())

    def test_an_older_observed_version_is_planned_with_its_target(self) -> None:
        action = self._plan("26.0.1")
        self.assertEqual(action.status, "planned")
        self.assertEqual(action.target, VERSION)


class XrayCommandTests(unittest.TestCase):
    def test_the_command_pins_the_release_url_and_the_expected_digest(self) -> None:
        command = xray.build_xray_upgrade_command(version=VERSION, sha256=SHA)
        self.assertIn(f"v{VERSION}/Xray-linux-64.zip", command)
        self.assertIn(f"expected_artifact_sha256={SHA}", command)
        # Every remote write lane serialises on the fleet lock.
        self.assertIn("/run/vps-ssh-launcher-maintenance.lock", command)
        self.assertIn("MAINTENANCE_BUSY", command)

    def test_the_command_rejects_a_non_x86_arch_instead_of_installing(self) -> None:
        command = xray.build_xray_upgrade_command(version=VERSION, sha256=SHA)
        self.assertIn("UNSUPPORTED_XRAY_ARCH", command)
        # The arch guard must exit before the swap, not after it.
        self.assertLess(
            command.index("UNSUPPORTED_XRAY_ARCH"),
            command.index("backup_dir="),
        )

    def test_an_invalid_pin_is_refused_before_any_command_is_built(self) -> None:
        with self.assertRaises(ValueError):
            xray.build_xray_upgrade_command(version="latest", sha256=SHA)
        with self.assertRaises(ValueError):
            xray.build_xray_upgrade_command(version=VERSION, sha256="short")

    def test_the_command_records_the_service_state_before_restarting(self) -> None:
        command = xray.build_xray_upgrade_command(version=VERSION, sha256=SHA)
        # An intentionally stopped service must not be started by the adapter.
        self.assertIn("service_was_active=0", command)
        self.assertLess(
            command.index("service_was_active=0"),
            command.index("backup_dir="),
        )


class DockerPinTests(unittest.TestCase):
    def _pin(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "compose_file": "/srv/app/compose.yml",
            "compose_sha256": SHA,
            "services": ["api"],
            "digests": {"api": DIGEST},
        }
        base.update(overrides)
        return base

    def test_a_valid_pin_is_normalised(self) -> None:
        pin = docker.normalize_pin(self._pin())
        self.assertEqual(pin["services"], ("api",))
        self.assertEqual(pin["digests"], {"api": DIGEST})

    def test_a_non_table_or_incomplete_pin_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a TOML table"):
            docker.normalize_pin("nope")
        for missing in ("compose_file", "compose_sha256", "services", "digests"):
            with self.subTest(missing=missing):
                pin = self._pin()
                del pin[missing]
                with self.assertRaisesRegex(ValueError, "requires exactly"):
                    docker.normalize_pin(pin)


class DockerPlanTests(unittest.TestCase):
    def test_an_absent_docker_blocks_reconciliation(self) -> None:
        action = docker.plan_upgrade(
            "bwg",
            "upgrade",
            "absent",
            {"compose_file": "/srv/app/compose.yml"},
        )
        self.assertEqual(action.status, "blocked")
        self.assertIn("must be present", action.reason)

    def test_no_pin_blocks_reconciliation(self) -> None:
        action = docker.plan_upgrade("bwg", "upgrade", "present", None)
        self.assertEqual(action.status, "blocked")
        self.assertIn("absolute Compose path", action.reason)

    def test_a_pinned_present_docker_is_planned_against_its_compose_file(self) -> None:
        action = docker.plan_upgrade(
            "bwg",
            "upgrade",
            "present",
            {"compose_file": "/srv/app/compose.yml"},
        )
        self.assertEqual(action.status, "planned")
        self.assertEqual(action.target, "/srv/app/compose.yml")


if __name__ == "__main__":
    unittest.main()

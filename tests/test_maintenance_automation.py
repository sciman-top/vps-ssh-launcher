"""Refusals and recovery paths of the unattended-apply authorization.

`authorize_unattended_apply` is the only thing standing between a scheduled
task and an unattended remote write. Its refusals were exercised indirectly by
the plan/apply flow tests, so a removed guard would have looked like a wider
window rather than a failing test.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from vps_ssh_launcher.maintenance import automation
from vps_ssh_launcher.maintenance.config import load_policy
from vps_ssh_launcher.maintenance.models import (
    MaintenanceAction,
    MaintenancePlan,
    MaintenancePolicy,
)

ACK = "I_ACKNOWLEDGE_BWG_SINGLE_HOST_AUTOMATION"


def _policy_body(*, resources: str = 'resources = ["xray"]') -> str:
    return (
        "[settings]\n"
        "strict_host_key_checking = true\n"
        "command_timeout = 10\n"
        "\n"
        "[pins.xray]\n"
        'version = "26.3.27"\n'
        'sha256 = "' + "a" * 64 + '"\n'
        "\n"
        "[profiles.bwg]\n"
        "enabled = true\n"
        "\n"
        "[profiles.bwg.resources]\n"
        'xray = "upgrade"\n'
        "\n"
        "[automation]\n"
        'mode = "unattended_apply"\n'
        f'acknowledge = "{ACK}"\n'
        f"{resources}\n"
        'window_start = "00:00"\n'
        'window_end = "23:59"\n'
    )


def _action(*, profile: str = "bwg", resource: str = "xray") -> MaintenanceAction:
    return MaintenanceAction(
        profile=profile,
        resource=resource,
        desired="upgrade",
        observed="present",
        status="planned",
        reason="fixture",
    )


def _plan(actions: tuple[MaintenanceAction, ...], *, created: str) -> MaintenancePlan:
    return MaintenancePlan(
        plan_id="fixture",
        created_at=created,
        policy_fingerprint="fingerprint",
        inventory_fingerprint="inventory",
        actions=actions,
        status="planned",
    )


class AutomationRefusalTests(unittest.TestCase):
    def _fixture(
        self, root: Path, *, body: str | None = None
    ) -> tuple[MaintenancePolicy, Path]:
        policy_path = root / "maintenance.toml"
        policy_path.write_text(body or _policy_body(), encoding="utf-8")
        return load_policy(policy_path), root / "state.db"

    def _authorize(
        self,
        root: Path,
        plan: MaintenancePlan,
        *,
        body: str | None = None,
        now: datetime | None = None,
    ) -> automation.AutomationAuthorization:
        policy, state = self._fixture(root, body=body)
        return automation.authorize_unattended_apply(policy, plan, state, now=now)

    def test_a_fresh_plan_in_the_window_is_authorized(self) -> None:
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        with tempfile.TemporaryDirectory() as directory:
            authorization = self._authorize(
                Path(directory),
                _plan((_action(),), created=now.isoformat()),
                now=now,
            )
        self.assertEqual(authorization.profile, "bwg")
        self.assertEqual(authorization.resource, "xray")

    def test_automation_must_be_acknowledged(self) -> None:
        body = _policy_body().replace(f'acknowledge = "{ACK}"\n', "")
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "disabled|acknowledgement"):
                self._authorize(
                    Path(directory),
                    _plan((_action(),), created=now.isoformat()),
                    now=now,
                    body=body,
                )

    def test_a_plan_outside_the_window_is_refused(self) -> None:
        body = (
            _policy_body()
            .replace('window_start = "00:00"', 'window_start = "20:00"')
            .replace('window_end = "23:59"', 'window_end = "22:00"')
        )
        now = datetime(2026, 11, 14, 9, 0).astimezone()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "outside the unattended"):
                self._authorize(
                    Path(directory),
                    _plan((_action(),), created=now.isoformat()),
                    now=now,
                    body=body,
                )

    def test_a_stale_plan_is_refused(self) -> None:
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        stale = (now - timedelta(hours=3)).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "age window"):
                self._authorize(
                    Path(directory), _plan((_action(),), created=stale), now=now
                )

    def test_a_plan_from_the_future_is_refused(self) -> None:
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        future = (now + timedelta(hours=1)).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "age window"):
                self._authorize(
                    Path(directory), _plan((_action(),), created=future), now=now
                )

    def test_a_non_bwg_profile_is_refused(self) -> None:
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "exactly one BWG"):
                self._authorize(
                    Path(directory),
                    _plan((_action(profile="zz"),), created=now.isoformat()),
                    now=now,
                )

    def test_two_planned_actions_are_refused(self) -> None:
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "exactly one planned"):
                self._authorize(
                    Path(directory),
                    _plan((_action(), _action()), created=now.isoformat()),
                    now=now,
                )

    def test_no_planned_action_is_refused(self) -> None:
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        done = MaintenanceAction(
            profile="bwg",
            resource="xray",
            desired="upgrade",
            observed="present",
            status="verified",
            reason="fixture",
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "exactly one planned"):
                self._authorize(
                    Path(directory),
                    _plan((done,), created=now.isoformat()),
                    now=now,
                )

    def test_a_resource_outside_the_allowlist_is_refused(self) -> None:
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        body = _policy_body(resources='resources = ["docker"]')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "not in the unattended"):
                self._authorize(
                    Path(directory),
                    _plan((_action(),), created=now.isoformat()),
                    now=now,
                    body=body,
                )

    def test_a_missing_pin_is_refused(self) -> None:
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        body = (
            _policy_body()
            .replace("[pins.xray]", "[pins.docker]")
            .replace('version = "26.3.27"', 'compose_file = "/srv/app/compose.yml"')
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self._authorize(
                    Path(directory),
                    _plan((_action(),), created=now.isoformat()),
                    now=now,
                    body=body,
                )


class AutomationWindowTests(unittest.TestCase):
    def test_the_window_is_half_open(self) -> None:
        start = datetime(2026, 11, 14, 20, 0).astimezone()
        self.assertTrue(automation._in_window(start, start="20:00", end="22:00"))
        self.assertFalse(
            automation._in_window(
                datetime(2026, 11, 14, 22, 0).astimezone(),
                start="20:00",
                end="22:00",
            )
        )

    def test_a_plan_time_without_a_timezone_is_read_as_local(self) -> None:
        plan = _plan((_action(),), created="2026-11-14T12:00:00")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "maintenance.toml"
            policy_path.write_text(_policy_body(), encoding="utf-8")
            policy = load_policy(policy_path)
            now = datetime(2026, 11, 14, 12, 5).astimezone()
            authorization = automation.authorize_unattended_apply(
                policy, plan, root / "state.db", now=now
            )
        self.assertEqual(authorization.resource, "xray")

    def test_a_non_iso_plan_time_is_refused(self) -> None:
        plan = _plan((_action(),), created="yesterday")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "maintenance.toml"
            policy_path.write_text(_policy_body(), encoding="utf-8")
            policy = load_policy(policy_path)
            with self.assertRaisesRegex(ValueError, "ISO-8601"):
                automation.authorize_unattended_apply(
                    policy,
                    plan,
                    root / "state.db",
                    now=datetime(2026, 11, 14, 12, 0).astimezone(),
                )


if __name__ == "__main__":
    unittest.main()

"""Cleanup must run on every updater pass, not only when a release lands.

Defect found 2026-11-09: cpa-auto-update.sh exited 0 when CUR == TARGET
("no newer mature release") and every backup prune sat after that exit. So
cleanup only ran on days when upstream both published AND cleared the 72h
soak, while the families that actually grow (admission-*, plugin-*,
converge-* from manual transactions) have nothing to do with releases.
Measured: 117 dirs / ~800 MiB with no policy touching them.
"""

from __future__ import annotations

import unittest
from pathlib import Path

UPDATER = Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
DOCTOR = Path(__file__).parents[1] / "scripts/remote/cpa-guardrail-doctor.sh"


class DailyCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = UPDATER.read_text(encoding="utf-8")
        self.no_update_exit = self.source.index('if [[ "$CUR" == "$TARGET" ]]; then')

    def test_local_prunes_run_before_the_no_update_exit(self) -> None:
        for function in ("prune_backups", "prune_manual_backup_families"):
            with self.subTest(function=function):
                call = self.source.index("\n" + function + "\n")
                self.assertLess(
                    call,
                    self.no_update_exit,
                    function + " must run on the no-update path too",
                )

    def test_image_prune_stays_on_the_verified_update_path(self) -> None:
        # docker rmi belongs to a verified update; moving it up would be a
        # different risk class (deleting images before anyone confirmed the
        # new generation works).
        call = self.source.index("\nprune_images\n")
        self.assertGreater(call, self.no_update_exit)

    def test_prune_runs_exactly_once_each(self) -> None:
        for function in (
            "prune_backups",
            "prune_manual_backup_families",
            "prune_images",
        ):
            with self.subTest(function=function):
                self.assertEqual(
                    self.source.count("\n" + function + "\n"),
                    1,
                    function + " must not be duplicated across paths",
                )

    def test_no_update_path_still_sends_no_upstream_traffic(self) -> None:
        # The 2026-09-21 rule: the no-update pass must not consume the
        # subscription account. Cleanup moving up must not drag a generation
        # or provider probe with it.
        block = self.source[
            self.no_update_exit : self.source.index("\n# Enabling plugins")
        ]
        self.assertNotIn("health generation", block)
        self.assertNotIn("relay-soft", block)
        self.assertIn("non_consuming=true", block)

    def test_doctor_reports_backup_volume_advisory(self) -> None:
        doctor = DOCTOR.read_text(encoding="utf-8")
        self.assertIn("update_backups_advisory=", doctor)
        # An advisory, not a gate: a large retained set is legitimate after a
        # migration, so this must not call mark_fail.
        section = doctor.split("update_backups_advisory=", 1)[0].rsplit(
            'echo "==inventory=="', 1
        )[-1]
        self.assertNotIn("mark_fail", section)


if __name__ == "__main__":
    unittest.main()

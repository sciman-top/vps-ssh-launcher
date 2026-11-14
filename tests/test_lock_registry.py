"""One fleet-wide lock path, no lane-private exception, no invented second lock.

Every remote write lane serialises on /run/vps-ssh-launcher-maintenance.lock and
that identity is what makes two wrappers mutually exclusive on the same host.
A new lock name silently splits the fleet into two writers that both believe
they hold the lock, so the set is enumerated here instead of being discovered
by a collision on a live host. The alt-SNI lane used to own a private
/run/vps-ssh-launcher-alt-sni.lock while it rewrote xray serverNames, the
vhosts and restarted xray -- exactly the state every other lane protects -- so
it now takes the shared lock like every other writer.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

MAINTENANCE_LOCK = "/run/vps-ssh-launcher-maintenance.lock"
ALLOWED_LOCKS = {MAINTENANCE_LOCK}

_LOCK = re.compile(r"/run/[A-Za-z0-9._-]+\.lock")
# The two files below are the lock owner: the shared Powershell transport and
# the drift gate may name the path without going through a lane wrapper.


def _script_texts() -> list[tuple[Path, str]]:
    texts: list[tuple[Path, str]] = []
    for path in sorted(SCRIPTS.rglob("*")):
        if path.suffix not in {".ps1", ".sh", ".py"}:
            continue
        texts.append((path, path.read_text(encoding="utf-8", errors="replace")))
    return texts


class MaintenanceLockRegistryTests(unittest.TestCase):
    def test_only_the_two_known_locks_are_referenced(self) -> None:
        found: dict[str, set[str]] = {}
        for path, text in _script_texts():
            for lock in _LOCK.findall(text):
                found.setdefault(lock, set()).add(path.name)
        self.assertTrue(found, "no remote lock path found; the scan is vacuous")
        unexpected = {
            lock: sorted(files)
            for lock, files in found.items()
            if lock not in ALLOWED_LOCKS
        }
        self.assertEqual(
            unexpected,
            {},
            "a new lock name splits the fleet into two writers that both think "
            "they hold the lock; register it here and in the runbook first",
        )

    def test_the_alt_sni_lane_shares_the_fleet_lock(self) -> None:
        # It rewrites xray serverNames, both vhosts and restarts xray. A private
        # lock let it run beside a kernel/firewall lane that holds the shared
        # one, so two writers could re-render xray and nginx at the same time.
        text = (SCRIPTS / "reality_alt_sni_lane.ps1").read_text(encoding="utf-8")
        self.assertIn(
            'LOCK_FILE="${VPS_ALT_SNI_LOCK_FILE:-' + MAINTENANCE_LOCK + '}"',
            text,
        )
        self.assertIn('exec 9>"$LOCK_FILE"', text)
        self.assertIn("flock -n 9", text)

    def test_the_fixed_shell_owners_are_frozen_and_explained(self) -> None:
        # These seven sites open the lock inside the generated/projected shell
        # with no override. That is deliberate: the CPA guardrail templates are
        # byte-projected and hash-pinned, and the recovery workflows must hold
        # the fleet lock even when an operator has exported a drill override.
        # The list is frozen here so a new fixed site is a reviewed change.
        fixed = {
            "cpa_recovery_workflow.ps1",
            "google_ipv4_routing.ps1",
            "cpa-auto-update.sh",
            "cpa-guardrail-apply.sh",
            "cpa-guardrail-deactivate-oauth-luna.sh",
            "cpa-guardrail-quarantine-oauth-luna.sh",
            "cpa-guardrail-rotate-path.sh",
            # Cron wrappers hardcode the lock inside their generated shell so a
            # stray environment override cannot let an unattended lane skip the
            # single-writer guarantee.
            "system_maintenance_cron.ps1",
            "v2ray_agent_renewtls_cron.ps1",
            "v2ray-agent-script-update.sh",
        }
        found: set[str] = set()
        for path, text in _script_texts():
            for line in text.splitlines():
                if MAINTENANCE_LOCK not in line or ":-" in line:
                    continue
                if (
                    "lock_file=" in line
                    or "lockPath" in line
                    or "MaintenanceLockFile" in line
                ):
                    continue
                if re.search(r"exec\s+\d+>", line) or "LOCK_FILE=" in line:
                    found.add(path.name)
        self.assertEqual(
            found,
            fixed,
            "a new non-overridable lock owner appeared, or one removed its "
            "hardcoded open; update this frozen list with the same reasoning",
        )


if __name__ == "__main__":
    unittest.main()

"""Audit the packages actually installed in the gate interpreter.

pip-audit -r requirements.txt only ever sees the runtime pin (paramiko), so a
vulnerable dev or transitive package -- pytest, bandit, pyyaml, a stale urllib3 --
stays invisible while the dependency gate reports success. This tool snapshots
the live environment into a pinned requirement list and audits that instead.

Bootstrap tooling is excluded on purpose: pip/setuptools ship inside the venv
and are upgraded by the interpreter installer, not by this project, so their
advisories are not this repository's to fix and would make the gate fail for a
condition it cannot repair. The project's own editable distribution is excluded
too (pip-audit cannot resolve a local, unpublished name).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Sequence

# Local bootstrap plus the editable project itself. Everything else installed in
# the gate environment is audited, including the dev extra.
EXCLUDED_DISTRIBUTIONS = frozenset({"pip", "setuptools", "wheel", "vps-ssh-launcher"})


def installed_pins(
    excluded: frozenset[str] = EXCLUDED_DISTRIBUTIONS,
) -> list[str]:
    """Return one name==version line per auditable installed distribution."""
    pins: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"]
        if not name or name.lower() in excluded:
            continue
        # Wheels can ship duplicate metadata (for example an old copy left by a
        # reinstall); a duplicate pin would make pip-audit reject the file.
        pins[name] = distribution.version
    return sorted(f"{name}=={version}" for name, version in pins.items())


def audit_argv(requirements: Path) -> list[str]:
    """Build the pip-audit invocation for one pinned requirement file."""
    return [
        "--no-deps",
        "--progress-spinner",
        "off",
        "-r",
        str(requirements),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit every auditable package installed in this interpreter."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the pinned requirements instead of auditing them",
    )
    args = parser.parse_args(argv)

    pins = installed_pins()
    if not pins:
        print("DEPENDENCY_AUDIT=FAIL (no installed distributions found)")
        return 1
    if args.list:
        print("\n".join(pins))
        return 0

    with tempfile.TemporaryDirectory(prefix="vps-dependency-audit-") as temporary:
        requirements = Path(temporary) / "installed.txt"
        requirements.write_text("\n".join(pins) + "\n", encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-m", "pip_audit", *audit_argv(requirements)],
            check=False,
        )
    if completed.returncode != 0:
        print(f"DEPENDENCY_AUDIT=FAIL ({len(pins)} installed distributions)")
        return completed.returncode
    print(f"DEPENDENCY_AUDIT=PASS ({len(pins)} installed distributions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

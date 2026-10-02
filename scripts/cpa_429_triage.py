#!/usr/bin/env python3
"""Compatibility entrypoint for the unified CPA failure triage tool.

``cpa_failure_triage.py`` is the single implementation and test source for
capacity, 429, 5xx, dead-route, client-abort, and slow-success attribution.
This historical 429 name remains executable so old runbooks and operator
muscle memory do not silently break, but it delegates every argument and every
classification rule to the canonical module.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Sequence


_CANONICAL = Path(__file__).with_name("cpa_failure_triage.py")
_MODULE = runpy.run_path(str(_CANONICAL))
main = _MODULE["main"]


def run(argv: Sequence[str] | None = None) -> int:
    """Delegate to the canonical CLI without changing its arguments."""

    return int(main(argv))


if __name__ == "__main__":
    raise SystemExit(run())

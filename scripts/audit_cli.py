"""Shared lossy-console support for local, read-only audit entrypoints.

These tools are run directly from the repository root. The adapter stays
local to these entrypoints so byte-deployed remote runtimes and their
integrity pins do not acquire a host-environment dependency.
"""

from __future__ import annotations

import sys

from vps_ssh_launcher.platform_utils import harden_stream_errors


def make_stdout_lossy_safe() -> None:
    """Preserve Chinese output while escaping symbols unsupported by cp936."""
    harden_stream_errors(sys.stdout, replacement="replace")


def make_streams_lossy_safe() -> None:
    """Keep audit reports and diagnosis lines printable on legacy consoles."""
    harden_stream_errors(sys.stdout, sys.stderr)

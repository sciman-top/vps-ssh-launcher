"""Small cross-platform primitives that must not depend on SSH or profiles."""

from __future__ import annotations

import sys
from typing import TextIO


def harden_stream_errors(
    *streams: TextIO,
    replacement: str = "backslashreplace",
) -> None:
    """Prevent a legacy console code page from aborting a report mid-write."""
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors=replacement)
            except (ValueError, OSError):
                # A detached or closed stream cannot be reconfigured. Keep
                # the caller's existing error path; this is not a verdict.
                pass


def default_streams() -> tuple[TextIO, TextIO]:
    return sys.stdout, sys.stderr

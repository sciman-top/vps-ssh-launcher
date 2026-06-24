"""Compatibility wrapper for the packaged launcher implementation."""

from __future__ import annotations

import sys

from vps_ssh_launcher import cli as _cli

sys.modules[__name__] = _cli

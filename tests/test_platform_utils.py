"""Shared terminal compatibility primitives used by local entrypoints."""

from __future__ import annotations

import unittest
from io import BytesIO, TextIOWrapper
from unittest import mock

from vps_ssh_launcher.platform_utils import harden_stream_errors


class PlatformUtilsTests(unittest.TestCase):
    def test_cp936_stream_escapes_an_unrepresentable_arrow(self) -> None:
        buffer = BytesIO()
        stream = TextIOWrapper(buffer, encoding="cp936", newline="")
        try:
            harden_stream_errors(stream)
            print("route \u21d2 ok", file=stream)
            stream.flush()
        finally:
            if not stream.closed:
                stream.flush()
            stream.detach()
        text = buffer.getvalue().decode("cp936")
        self.assertIn("route", text)
        self.assertIn("\\u21d2", text)

    def test_reconfigure_failure_does_not_replace_original_output_path(self) -> None:
        stream = mock.Mock()
        stream.reconfigure.side_effect = ValueError("detached")
        harden_stream_errors(stream)
        stream.reconfigure.assert_called_once_with(errors="backslashreplace")


if __name__ == "__main__":
    unittest.main()

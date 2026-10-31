"""Tests for scripts/cpa_natural_traffic_audit.py.

The audit must turn journal lines into lane/model aggregates without ever
retaining request IDs, prompts, or response text.  The synthetic lines below
cover a completed SSE stream, a capacity response, a client disconnect, a
non-SSE completion, and a missing start line.
"""

from __future__ import annotations

import io
import json
import runpy
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, cast
from unittest import mock

MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "cpa_natural_traffic_audit.py")
)
parse_journal = cast(Any, MODULE["parse_journal"])
summarize = cast(Any, MODULE["summarize"])
render = cast(Any, MODULE["render"])
main = cast(Any, MODULE["main"])


LOG = """\
2026-10-31T00:00:01+00:00 host python3[1]: INFO http request_id=r1 route=responses
2026-10-31T00:00:05+00:00 host python3[1]: INFO upstream_result lane=chatgpt-oauth model=gpt-6-luna status=200 capacity=false retry_after=absent waited_ms=2 successful=true protocol_result=completed upstream_header_ms=1000 upstream_first_byte_ms=1001 sse_bytes=10000 sse_events=20 sse_last_event=response.completed sse_inspection_complete=true sse_malformed_json=false sse_terminal_seen=true request_id=r1 route=responses
2026-10-31T00:00:10+00:00 host python3[1]: INFO http request_id=r2 route=responses
2026-10-31T00:00:12+00:00 host python3[1]: WARNING upstream_result lane=workbuddy-oauth model=deepseek-v4.1-flash status=500 capacity=true retry_after=absent waited_ms=1 successful=false protocol_result=capacity upstream_header_ms=2000 upstream_first_byte_ms=2000 sse_bytes=0 sse_events=0 sse_last_event=none sse_inspection_complete=na sse_malformed_json=na sse_terminal_seen=na request_id=r2 route=responses
2026-10-31T00:00:20+00:00 host python3[1]: INFO http request_id=r3 route=responses
2026-10-31T00:00:21+00:00 host python3[1]: INFO downstream_disconnect lane=antigravity-oauth model=gemini-3.8-flash-high waited_ms=3 sse_bytes=500 sse_events=2 sse_last_event=response.created sse_inspection_complete=false request_id=r3
2026-10-31T00:00:30+00:00 host python3[1]: INFO upstream_result lane=zhipu-coding-plan model=glm-5.3-flash status=200 capacity=false retry_after=absent waited_ms=0 successful=true protocol_result=completed upstream_header_ms=3000 upstream_first_byte_ms=3001 sse_bytes=0 sse_events=0 sse_last_event=none sse_inspection_complete=na sse_malformed_json=na sse_terminal_seen=na request_id=r4 route=chat
"""


class NaturalTrafficAuditTests(unittest.TestCase):
    def test_pairs_start_and_result_and_keeps_aggregates_only(self) -> None:
        records, starts = parse_journal(io.StringIO(LOG))
        self.assertEqual(starts, 3)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["stream_duration_s"], 4.0)
        self.assertEqual(records[1]["stream_duration_s"], 2.0)
        self.assertIsNone(records[2]["stream_duration_s"])
        summary = summarize(records, 24, starts)
        self.assertEqual(summary["records"], 3)
        names = {lane["lane_model"] for lane in summary["lanes"]}
        self.assertIn("chatgpt-oauth/gpt-6-luna", names)
        self.assertIn("workbuddy-oauth/deepseek-v4.1-flash", names)
        self.assertIn("zhipu-coding-plan/glm-5.3-flash", names)
        gpt = next(
            lane
            for lane in summary["lanes"]
            if lane["lane_model"] == "chatgpt-oauth/gpt-6-luna"
        )
        self.assertEqual(gpt["completed"], 1)
        self.assertEqual(gpt["capacity"], 0)
        self.assertEqual(gpt["header_completed_p50_ms"], 1000.0)
        self.assertEqual(gpt["sse_bytes_per_s_p50"], 2500.0)
        workbuddy = next(
            lane
            for lane in summary["lanes"]
            if lane["lane_model"] == "workbuddy-oauth/deepseek-v4.1-flash"
        )
        self.assertEqual(workbuddy["capacity"], 1)
        rendered = "\n".join(render(summary))
        self.assertNotIn("request_id", rendered)
        self.assertNotIn("r1", rendered)
        self.assertIn("sse_bytes_per_s", rendered)

    def test_peak_window_and_tiny_sse(self) -> None:
        lines = []
        for index in range(16):
            lines.append(
                f"2026-10-31T00:00:{index:02d}+00:00 host python3[1]: "
                f"INFO upstream_result lane=workbuddy-oauth "
                f"model=deepseek-v4.1-flash status=200 capacity=false "
                f"successful=true protocol_result=completed "
                f"upstream_header_ms=100 upstream_first_byte_ms=101 "
                f"sse_bytes=100 sse_events=2 sse_inspection_complete=true "
                f"request_id=r{index}"
            )
        records, _ = parse_journal(lines)
        summary = summarize(records, 24)
        lane = summary["lanes"][0]
        self.assertEqual(lane["peak_60s"], 16)
        self.assertEqual(lane["tiny_sse_completed"], 16)

    def test_json_mode_writes_parseable_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.log"
            path.write_text(LOG, encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--file", str(path), "--json"])
        self.assertEqual(code, 0)
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed["records"], 3)

    def test_remote_transfer_uses_private_temp_and_preserves_failure(self) -> None:
        remote = cast(Any, MODULE["_analyse_remotely"])
        payload = "Zml4dHVyZQ=="
        commands: list[str] = []

        def capture(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            commands.append(args[-1])
            return subprocess.CompletedProcess(args, 0, "", "")

        with (
            mock.patch.dict(remote.__globals__, {"_remote_payload": lambda: payload}),
            mock.patch("subprocess.run", side_effect=capture),
        ):
            code, _ = remote("bwg", 24)
        self.assertEqual(code, 0)
        command = commands[0]
        self.assertIn("umask 077", command)
        self.assertIn("mktemp /tmp/cpa-natural-traffic.", command)
        self.assertIn("trap 'rm -f --", command)
        self.assertIn("journalctl -u cpa-admission", command)
        self.assertIn("--remote", command)


if __name__ == "__main__":
    unittest.main()

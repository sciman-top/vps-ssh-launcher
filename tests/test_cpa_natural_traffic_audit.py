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
from datetime import datetime, timezone
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
origins_from_dumps = cast(Any, MODULE["origins_from_dumps"])
recover_usage_from_dump = cast(Any, MODULE["_recover_usage_from_dump"])


LOG = """\
2026-10-31T00:00:01+00:00 host python3[1]: INFO http request_id=r1 route=responses
2026-10-31T00:00:05+00:00 host python3[1]: INFO upstream_result lane=chatgpt-oauth model=gpt-6-luna status=200 capacity=false retry_after=absent waited_ms=2 successful=true protocol_result=completed upstream_header_ms=1000 upstream_first_byte_ms=1001 first_text_ms=1200 sse_bytes=10000 sse_events=20 sse_last_event=response.completed sse_inspection_complete=true sse_malformed_json=false sse_terminal_seen=true request_id=r1 route=responses
2026-10-31T00:00:10+00:00 host python3[1]: INFO http request_id=r2 route=responses
2026-10-31T00:00:12+00:00 host python3[1]: WARNING upstream_result lane=workbuddy-oauth model=deepseek-v4.1-flash status=500 capacity=true retry_after=absent waited_ms=1 successful=false protocol_result=capacity upstream_header_ms=2000 upstream_first_byte_ms=2000 sse_bytes=0 sse_events=0 sse_last_event=none sse_inspection_complete=na sse_malformed_json=na sse_terminal_seen=na request_id=r2 route=responses
2026-10-31T00:00:20+00:00 host python3[1]: INFO http request_id=r3 route=responses
2026-10-31T00:00:21+00:00 host python3[1]: INFO downstream_disconnect lane=antigravity-oauth model=gemini-3.8-flash-high waited_ms=3 sse_bytes=500 sse_events=2 sse_last_event=response.created sse_inspection_complete=false request_id=r3
2026-10-31T00:00:30+00:00 host python3[1]: INFO upstream_result lane=zhipu-coding-plan model=glm-5.3-flash status=200 capacity=false retry_after=absent waited_ms=0 successful=true protocol_result=completed upstream_header_ms=3000 upstream_first_byte_ms=3001 sse_bytes=0 sse_events=0 sse_last_event=none sse_inspection_complete=na sse_malformed_json=na sse_terminal_seen=na request_id=r4 route=chat
"""


class NaturalTrafficAuditTests(unittest.TestCase):
    def test_aggregates_reported_token_usage_into_cache_hit_ratio(self) -> None:
        """Usage numbers are summed per lane; the ratio uses reported input only.

        A request whose upstream reported no usage must not contribute a zero
        that drags the ratio down, and it must not be counted in the support.
        """

        log = (
            "2026-10-31T00:00:05+00:00 host py[1]: INFO upstream_result "
            "lane=chatgpt-oauth model=gpt-6-luna status=200 capacity=false "
            "retry_after=absent waited_ms=2 successful=true "
            "protocol_result=completed upstream_header_ms=1000 "
            "upstream_first_byte_ms=1001 sse_bytes=100 sse_events=5 "
            "sse_last_event=response.completed sse_inspection_complete=true "
            "sse_malformed_json=false sse_terminal_seen=true "
            "usage_in=1000 usage_cached=800 usage_out=200 request_id=r1\n"
            "2026-10-31T00:00:09+00:00 host py[1]: INFO upstream_result "
            "lane=chatgpt-oauth model=gpt-6-luna status=200 capacity=false "
            "retry_after=absent waited_ms=2 successful=true "
            "protocol_result=completed upstream_header_ms=900 "
            "upstream_first_byte_ms=901 sse_bytes=100 sse_events=5 "
            "sse_last_event=response.completed sse_inspection_complete=true "
            "sse_malformed_json=false sse_terminal_seen=true "
            "usage_in=1000 usage_cached=600 usage_out=100 request_id=r2\n"
            "2026-10-31T00:00:11+00:00 host py[1]: INFO upstream_result "
            "lane=chatgpt-oauth model=gpt-6-luna status=200 capacity=false "
            "retry_after=absent waited_ms=2 successful=true "
            "protocol_result=completed upstream_header_ms=900 "
            "upstream_first_byte_ms=901 sse_bytes=100 sse_events=5 "
            "sse_last_event=response.completed sse_inspection_complete=true "
            "sse_malformed_json=false sse_terminal_seen=true "
            "usage_in=na usage_cached=na usage_out=na request_id=r3\n"
        )
        records, _ = parse_journal(
            (
                raw
                for raw in log.splitlines()
                if not (
                    "host py[1]: INFO http request_id=d" in raw and "00:00:01" in raw
                )
            )
        )
        summary = summarize(records, 24)
        lane = next(
            item
            for item in summary["lanes"]
            if item["lane_model"] == "chatgpt-oauth/gpt-6-luna"
        )
        self.assertEqual(lane["token_reports"], 2)
        self.assertEqual(lane["tokens_input"], 2000)
        self.assertEqual(lane["tokens_cached"], 1400)
        self.assertEqual(lane["tokens_output"], 300)
        self.assertAlmostEqual(lane["cache_hit_ratio"], 0.7)
        rendered = "\n".join(render(summary))
        self.assertIn("cache_hit_ratio=0.70", rendered)

    def test_pairs_start_and_result_and_keeps_aggregates_only(self) -> None:
        records, starts = parse_journal(io.StringIO(LOG))
        self.assertEqual(starts, 3)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["stream_duration_s"], 4.0)
        self.assertEqual(records[0]["first_text_ms"], 1200)
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
                f"INFO lane_admit lane=workbuddy-oauth "
                f"model=deepseek-v4.1-flash waited_ms=0 request_id=r{index}"
            )
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
        self.assertEqual(lane["admit_peak_60s"], 16)
        self.assertEqual(lane["tiny_sse_completed"], 16)
        self.assertTrue(
            any("community 14003 range" in item for item in summary["warnings"])
        )
        self.assertTrue(
            any("tiny_sse_completed=16" in item for item in summary["warnings"])
        )

    def test_completed_peak_does_not_invent_an_admission_burst(self) -> None:
        """Long requests can finish together even when admissions are bounded."""

        lines = []
        for index in range(16):
            admitted_seconds = index * 6
            admitted_minute = admitted_seconds // 60
            admitted_second = admitted_seconds % 60
            lines.append(
                f"2026-10-31T00:{admitted_minute:02d}:{admitted_second:02d}"
                f"+00:00 host py[1]: "
                f"INFO lane_admit lane=workbuddy-oauth "
                f"model=deepseek-v4.1-flash waited_ms=0 request_id=r{index}"
            )
            result_second = index // 2
            result_fraction = 0 if index % 2 == 0 else 5
            lines.append(
                f"2026-10-31T00:02:{result_second:02d}.{result_fraction}"
                f"+00:00 host py[1]: INFO upstream_result lane=workbuddy-oauth "
                f"model=deepseek-v4.1-flash status=200 capacity=false "
                f"successful=true protocol_result=completed "
                f"upstream_header_ms=100 upstream_first_byte_ms=101 "
                f"sse_bytes=2000 sse_events=10 sse_inspection_complete=true "
                f"request_id=r{index}"
            )
        records, _ = parse_journal(lines)
        summary = summarize(records, 24)
        lane = summary["lanes"][0]
        self.assertEqual(lane["peak_60s"], 16)
        # A half-open [T, T+60s) rolling window excludes the :60 boundary
        # even when those requests complete at :00.0 and :07.5.
        self.assertEqual(lane["admit_peak_60s"], 10)
        self.assertFalse(
            any("community 14003 range" in item for item in summary["warnings"])
        )

    def test_slow_turn_attribution_separates_queue_upstream_and_decode(
        self,
    ) -> None:
        """A total-duration tail must identify the stage that actually stalled."""

        def result(
            request: str,
            waited: int,
            header: int,
            first_byte: int,
            usage_out: int,
            text_finish: str,
        ) -> str:
            return (
                f"2026-10-31T00:00:20+00:00 host py[1]: INFO upstream_result "
                f"lane=chatgpt-oauth model=gpt-6-luna status=200 "
                f"capacity=false waited_ms={waited} successful=true "
                f"protocol_result=completed upstream_header_ms={header} "
                f"upstream_first_byte_ms={first_byte} "
                f"sse_bytes=10000 sse_events=20 "
                f"sse_inspection_complete=true usage_in=1000 "
                f"usage_cached=800 usage_out={usage_out} "
                f"sse_text_finish={text_finish} request_id={request}\n"
            )

        log = (
            "2026-10-31T00:00:00+00:00 host py[1]: INFO lane_admit "
            "lane=chatgpt-oauth model=gpt-6-luna waited_ms=0 request_id=q\n"
            "2026-10-31T00:00:00+00:00 host py[1]: INFO lane_admit "
            "lane=chatgpt-oauth model=gpt-6-luna waited_ms=0 request_id=u\n"
            "2026-10-31T00:00:00+00:00 host py[1]: INFO lane_admit "
            "lane=chatgpt-oauth model=gpt-6-luna waited_ms=0 request_id=d\n"
            "2026-10-31T00:00:01+00:00 host py[1]: INFO http request_id=q\n"
            "2026-10-31T00:00:01+00:00 host py[1]: INFO http request_id=u\n"
            "2026-10-31T00:00:05+00:00 host py[1]: INFO http request_id=d\n"
            + result("q", 10000, 1000, 1001, 100, "stop")
            + result("u", 0, 8000, 8001, 100, "stop")
            + result("d", 0, 1000, 1001, 100, "stop")
            .replace(
                "2026-10-31T00:00:20+00:00 host py[1]: INFO upstream_result",
                "2026-10-31T00:00:31+00:00 host py[1]: INFO upstream_result",
            )
            .replace("sse_inspection_complete=true", "sse_inspection_complete=na")
        )
        records, _ = parse_journal(io.StringIO(log))
        self.assertEqual(records[2]["stream_duration_s"], 26.0)
        self.assertEqual(records[2]["first_output_ms"], 1001)
        lane = summarize(records, 24)["lanes"][0]
        self.assertEqual(
            lane["slow_5s_attribution"],
            {"queue": 1, "upstream": 1, "decode": 1},
        )
        self.assertEqual(lane["first_output_p50_ms"], 1001)
        rendered = "\n".join(render(summarize(records, 24)))
        self.assertIn("slow_5s=queue:1,upstream:1,decode:1", rendered)

    def test_reports_empty_and_truncated_text_without_counting_reasoning_as_text(
        self,
    ) -> None:
        """Explicit canary dumps classify prose, truncation, and hidden reasoning."""

        lane = "workbuddy-oauth/deepseek-v4.1-flash"
        origins = (
            f"{lane}:empty",
            f"{lane}:truncated",
            f"{lane}:reasoning_only",
        )
        lines = [
            (
                "2026-10-31T00:00:05+00:00 host py[1]: INFO upstream_result "
                "lane=workbuddy-oauth model=deepseek-v4.1-flash status=200 "
                "capacity=false waited_ms=2 successful=true "
                "protocol_result=completed upstream_header_ms=1000 "
                "upstream_first_byte_ms=1001 sse_bytes=100 sse_events=5 "
                "sse_inspection_complete=true usage_in=1000 "
                "usage_cached=800 usage_out=100 request_id=r\n"
            )
        ]
        data = summarize(parse_journal(lines)[0], 24, origins=origins)
        lane_data = data["lanes"][0]
        self.assertEqual(lane_data["output_origins"], 3)
        self.assertEqual(lane_data["empty_text_completed"], 1)
        self.assertEqual(lane_data["truncated_text_completed"], 1)
        self.assertEqual(lane_data["reasoning_only_completed"], 1)
        warnings = "\n".join(data["warnings"])
        self.assertIn("empty_text_completed=1", warnings)
        self.assertIn("truncated_text_completed=1", warnings)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dumps = {
                "empty.dump": (
                    "=== REQUEST BODY ===\n"
                    '{"type":"response.completed","finish_reason":"stop"}\n'
                    "=== RESPONSE ===\n"
                    '{"type":"response.output_text.done","text":""}\n'
                    '{"type":"response.completed","finish_reason":"stop"}\n'
                ),
                "truncated.dump": (
                    'data: {"choices":[{"delta":{"content":"part"},"finish_reason":"length"}]}\n'
                ),
                "reasoning.dump": (
                    "=== RESPONSE ===\n"
                    'data: {"type":"response.completed","response":'
                    '{"usage":{"output_tokens":4}}}\n'
                ),
                "text.dump": (
                    'data: {"type":"response.output_text.done","text":"OK"}\n'
                ),
            }
            files = []
            for name, content in dumps.items():
                path = root / name
                path.write_text(content, encoding="utf-8")
                files.append((lane, path))
            read_origins = origins_from_dumps(files)
            self.assertEqual(
                read_origins[lane],
                ("empty", "truncated", "reasoning_only", "output"),
            )
            observed = summarize(
                parse_journal(lines)[0],
                24,
                origins=tuple(
                    f"{label}:{state}"
                    for label, states in read_origins.items()
                    for state in states
                ),
            )["lanes"][0]
        self.assertEqual(observed["empty_text_completed"], 1)
        self.assertEqual(observed["truncated_text_completed"], 1)
        self.assertEqual(observed["reasoning_only_completed"], 1)
        self.assertEqual(observed["text_output_completed"], 1)

    def test_admission_text_finish_field_marks_empty_and_truncated_text(
        self,
    ) -> None:
        lines = [
            "2026-10-31T00:00:05+00:00 h p[1]: INFO upstream_result "
            "lane=workbuddy-oauth model=deepseek-v4.1-flash status=200 "
            "waited_ms=0 successful=true protocol_result=completed "
            "upstream_header_ms=100 request_id=empty sse_text_finish=empty_text\n",
            "2026-10-31T00:00:06+00:00 h p[1]: INFO upstream_result "
            "lane=workbuddy-oauth model=deepseek-v4.1-flash status=200 "
            "waited_ms=0 successful=true protocol_result=completed "
            "upstream_header_ms=100 request_id=truncated "
            "sse_text_finish=max_output_tokens\n",
        ]
        lane = summarize(parse_journal(lines)[0], 24)["lanes"][0]
        self.assertEqual(lane["empty_text_completed"], 1)
        self.assertEqual(lane["truncated_text_completed"], 1)

    def test_origin_label_does_not_bleed_to_longer_model_name(self) -> None:
        labels = ("chatgpt-oauth/gpt-6-luna:output",)
        data = summarize(
            parse_journal(
                [
                    "2026-10-31T00:00:05+00:00 h p[1]: INFO upstream_result "
                    "lane=chatgpt-oauth model=gpt-6-luna-long status=200 "
                    "waited_ms=0 successful=true protocol_result=completed "
                    "upstream_header_ms=1 request_id=r\n"
                ]
            )[0],
            24,
            origins=labels,
        )
        self.assertEqual(data["lanes"][0]["output_origins"], 0)

    def test_origin_path_uses_last_colon_for_windows_drive(self) -> None:
        parsed = MODULE["_origin_files"](
            ["chatgpt-oauth/gpt-6-luna:C:\\temp\\response.dump"]
        )
        self.assertEqual(parsed[0][0], "chatgpt-oauth/gpt-6-luna")
        self.assertEqual(str(parsed[0][1]), r"C:\temp\response.dump")

    def test_explicit_dump_usage_never_reads_request_half(self) -> None:
        """Usage recovery must not inspect prompts, keys, or request headers."""

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.dump"
            path.write_text(
                "=== REQUEST BODY ===\n"
                '{"prompt_tokens":999999,"prompt":"secret"}\n'
                "=== RESPONSE ===\n"
                'data: {"usage":{"prompt_tokens":1200,'
                '"prompt_tokens_details":{"cached_tokens":1000},'
                '"completion_tokens":42}}\n'
                "data: [DONE]\n",
                encoding="utf-8",
            )
            self.assertEqual(recover_usage_from_dump(path), (1200, 1000, 42))

    def test_estimates_decode_tokens_per_second(self) -> None:
        log = (
            "2026-10-31T00:00:00+00:00 host py[1]: INFO http request_id=r1\n"
            "2026-10-31T00:00:00+00:00 host py[1]: INFO lane_admit "
            "lane=chatgpt-oauth model=gpt-6-luna waited_ms=0 request_id=r1\n"
            "2026-10-31T00:00:04+00:00 host py[1]: INFO upstream_result "
            "lane=chatgpt-oauth model=gpt-6-luna status=200 capacity=false "
            "waited_ms=0 successful=true protocol_result=completed "
            "upstream_header_ms=1000 upstream_first_byte_ms=1000 "
            "sse_bytes=1000 sse_events=10 sse_inspection_complete=true "
            "sse_terminal_seen=true "
            "usage_in=1000 usage_cached=800 usage_out=100 request_id=r1\n"
        )
        records, _ = parse_journal(io.StringIO(log))
        summary = summarize(records, 24)
        lane = summary["lanes"][0]
        self.assertEqual(lane["output_rate_reports"], 1)
        self.assertAlmostEqual(lane["output_tokens_per_s_p50"], 100.0)
        rendered = "\n".join(render(summary))
        self.assertIn("output_tokens_per_s", rendered)
        self.assertIn("uncached=200", rendered)

    def test_output_rate_excludes_stream_queue_and_ttfb(self) -> None:
        """A queued SSE turn is not decoded at thousands of tokens/s."""

        log = (
            "2026-10-31T00:00:00+00:00 host py[1]: INFO http request_id=r1\n"
            "2026-10-31T00:00:00+00:00 host py[1]: INFO lane_admit "
            "lane=workbuddy-oauth model=deepseek-v4.1-flash waited_ms=5000 "
            "request_id=r1\n"
            "2026-10-31T00:00:16+00:00 host py[1]: INFO upstream_result "
            "lane=workbuddy-oauth model=deepseek-v4.1-flash status=200 "
            "capacity=false waited_ms=5000 successful=true "
            "protocol_result=completed upstream_header_ms=4000 "
            "upstream_first_byte_ms=4000 sse_bytes=1000 sse_events=10 "
            "sse_inspection_complete=true sse_terminal_seen=true "
            "usage_in=1000 usage_out=70 "
            "request_id=r1\n"
        )
        records, _ = parse_journal(io.StringIO(log))
        summary = summarize(records, 24)
        lane = summary["lanes"][0]
        self.assertEqual(lane["output_rate_reports"], 1)
        self.assertAlmostEqual(lane["output_tokens_per_s_p50"], 70.0 / 4.0)

    def test_output_rate_uses_post_header_window_for_non_sse(self) -> None:
        """A non-SSE http line includes upstream time and subtracts TTFB."""

        log = (
            "2026-10-31T00:00:00+00:00 host py[1]: INFO lane_admit "
            "lane=workbuddy-oauth model=deepseek-v4.1-flash waited_ms=1000 "
            "request_id=r1\n"
            "2026-10-31T00:00:12+00:00 host py[1]: INFO http request_id=r1\n"
            "2026-10-31T00:00:12+00:00 host py[1]: INFO upstream_result "
            "lane=workbuddy-oauth model=deepseek-v4.1-flash status=200 "
            "capacity=false waited_ms=1000 successful=true "
            "protocol_result=completed upstream_header_ms=3000 "
            "upstream_first_byte_ms=3000 sse_bytes=0 sse_events=0 "
            "sse_inspection_complete=na usage_in=1000 usage_out=6 "
            "request_id=r1\n"
        )
        records, _ = parse_journal(io.StringIO(log))
        summary = summarize(records, 24)
        lane = summary["lanes"][0]
        self.assertEqual(lane["output_rate_reports"], 1)
        self.assertAlmostEqual(lane["output_tokens_per_s_p50"], 1.5)

    def test_single_frame_stream_has_no_fabricated_output_rate(self) -> None:
        """A one-frame completion has no inner decode interval to measure."""

        log = (
            "2026-10-31T00:00:00+00:00 host py[1]: INFO lane_admit "
            "lane=chatgpt-oauth model=gpt-6.1-sol waited_ms=2 "
            "request_id=r1\n"
            "2026-10-31T00:00:05+00:00 host py[1]: INFO upstream_result "
            "lane=chatgpt-oauth model=gpt-6.1-sol status=200 capacity=false "
            "waited_ms=0 successful=true protocol_result=completed "
            "upstream_header_ms=5400 upstream_first_byte_ms=5400 sse_bytes=40 "
            "sse_events=1 sse_inspection_complete=true sse_terminal_seen=true "
            "usage_in=100 usage_out=10 request_id=r1\n"
        )
        records, _ = parse_journal(io.StringIO(log))
        lane = summarize(records, 24)["lanes"][0]
        self.assertEqual(lane["token_reports"], 1)
        self.assertEqual(lane["output_rate_reports"], 0)
        self.assertIsNone(lane["output_tokens_per_s_p50"])

    def test_provider_reported_zero_cost_is_not_treated_as_free_capacity(
        self,
    ) -> None:
        log = (
            "2026-10-31T00:00:04+00:00 host py[1]: INFO upstream_result "
            "lane=workbuddy-oauth model=deepseek-v4.1-flash status=200 "
            "waited_ms=0 successful=true protocol_result=completed "
            "upstream_header_ms=100 upstream_first_byte_ms=101 "
            "usage_in=1000 usage_cached=800 usage_out=100 "
            "usage_cost_usd=0.00000000 request_id=r1\n"
        )
        summary = summarize(parse_journal(io.StringIO(log))[0], 24)
        lane = summary["lanes"][0]
        self.assertEqual(lane["cost_reports"], 1)
        self.assertEqual(lane["cost_usd"], 0.0)
        self.assertEqual(lane["zero_cost_reports"], 1)
        self.assertTrue(
            any("zero_cost_reports=1" in warning for warning in summary["warnings"])
        )

    def test_flags_material_cache_cold_lane(self) -> None:
        lines = []
        for index in range(10):
            lines.append(
                f"2026-10-31T00:00:{index:02d}+00:00 host py[1]: "
                f"INFO upstream_result lane=chatgpt-oauth "
                f"model=gpt-5.6-luna status=200 capacity=false "
                f"successful=true protocol_result=completed "
                f"upstream_header_ms=100 upstream_first_byte_ms=101 "
                f"usage_in=120000 usage_cached=0 usage_out=100 "
                f"request_id=r{index}"
            )
        records, _ = parse_journal(lines)
        summary = summarize(records, 24)
        self.assertTrue(
            any(
                "cache_hit_ratio=0.00 across 1200000 input tokens" in warning
                for warning in summary["warnings"]
            )
        )

    def test_flags_moderately_low_cache_after_enough_samples(self) -> None:
        lines = []
        for index in range(10):
            lines.append(
                f"2026-10-31T00:00:{index:02d}+00:00 host py[1]: "
                f"INFO upstream_result lane=chatgpt-oauth "
                f"model=gpt-5.6-luna status=200 capacity=false "
                f"successful=true protocol_result=completed "
                f"upstream_header_ms=100 upstream_first_byte_ms=101 "
                f"usage_in=120000 usage_cached=48000 usage_out=100 "
                f"request_id=r{index}"
            )
        summary = summarize(parse_journal(lines)[0], 24)
        self.assertTrue(
            any(
                "cache_hit_ratio=0.40 across 1200000 input tokens "
                "(review threshold 0.50)" in warning
                for warning in summary["warnings"]
            )
        )

    def test_does_not_flag_low_cache_from_small_sample(self) -> None:
        lines = []
        for index in range(9):
            lines.append(
                f"2026-10-31T00:00:{index:02d}+00:00 host py[1]: "
                f"INFO upstream_result lane=chatgpt-oauth "
                f"model=gpt-5.6-luna status=200 capacity=false "
                f"successful=true protocol_result=completed "
                f"upstream_header_ms=100 upstream_first_byte_ms=101 "
                f"usage_in=200000 usage_cached=0 usage_out=100 "
                f"request_id=r{index}"
            )
        summary = summarize(parse_journal(lines)[0], 24)
        self.assertFalse(
            any("cache_hit_ratio=" in warning for warning in summary["warnings"])
        )

    def test_natural_metrics_exclude_health_probes(self) -> None:
        """Cold, tiny, or slow health probes must not distort user traffic."""

        log = (
            "2026-10-31T00:00:01+00:00 host py[1]: INFO lane_probe "
            "lane=chatgpt-oauth model=gpt-6-luna request_id=probe\n"
            "2026-10-31T00:00:10+00:00 host py[1]: INFO upstream_result "
            "lane=chatgpt-oauth model=gpt-6-luna status=200 capacity=false "
            "waited_ms=9000 successful=true protocol_result=completed "
            "upstream_header_ms=9000 upstream_first_byte_ms=9001 "
            "sse_bytes=10 sse_events=1 sse_inspection_complete=true "
            "usage_in=1000 usage_cached=0 usage_out=1 request_id=probe\n"
            "2026-10-31T00:00:20+00:00 host py[1]: INFO upstream_result "
            "lane=chatgpt-oauth model=gpt-6-luna status=200 capacity=false "
            "waited_ms=1 successful=true protocol_result=completed "
            "upstream_header_ms=100 upstream_first_byte_ms=101 "
            "sse_bytes=100 sse_events=5 sse_inspection_complete=true "
            "usage_in=1000 usage_cached=900 usage_out=100 request_id=natural\n"
        )
        records, _ = parse_journal(io.StringIO(log))
        self.assertEqual(sum(record["probe"] for record in records), 1)

        lane = summarize(records, 24)["lanes"][0]
        self.assertEqual(lane["requests"], 2)
        self.assertEqual(lane["natural_requests"], 1)
        self.assertEqual(lane["probe_requests"], 1)
        self.assertEqual(lane["completed"], 1)
        self.assertEqual(lane["token_reports"], 1)
        self.assertEqual(lane["tokens_input"], 1000)
        self.assertEqual(lane["tokens_cached"], 900)
        self.assertAlmostEqual(lane["cache_hit_ratio"], 0.9)
        self.assertEqual(lane["header_completed_p50_ms"], 100.0)
        self.assertEqual(lane["first_output_p50_ms"], 101.0)

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
        commands: list[str] = []

        def capture(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            commands.append(args[-1])
            response = kwargs.get("stdout")
            if isinstance(response, io.TextIOBase):
                response.write('{"records": 0}\n')
            return subprocess.CompletedProcess(
                args, 0, None if isinstance(response, io.TextIOBase) else "", ""
            )

        with mock.patch("subprocess.run", side_effect=capture):
            code, _ = remote("bwg", 24)
        self.assertEqual(code, 0)
        command = commands[-1]
        upload = cast(Any, MODULE["_remote_upload_command"])(
            (
                Path(__file__).parents[1] / "scripts/cpa_natural_traffic_audit.py"
            ).read_bytes()
        )
        self.assertLess(len(upload), 32767)
        self.assertIn("umask 077", command)
        self.assertIn("mktemp /tmp/cpa-natural-traffic.", command)
        self.assertIn('rm -f -- "$tmp"', command)
        self.assertIn("journalctl -u cpa-admission", command)
        self.assertIn("--remote", command)

    def test_remote_transfer_preserves_a_failing_router_process(self) -> None:
        remote = cast(Any, MODULE["_analyse_remotely"])

        def failure(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            response = kwargs.get("stdout")
            if isinstance(response, io.TextIOBase):
                response.write("router failed\n")
            return subprocess.CompletedProcess(args, 5, None, None)

        with mock.patch("subprocess.run", side_effect=failure):
            code, output = remote("bwg", 24)
        self.assertEqual(code, 5)
        self.assertIn("router failed", output)

    def test_usage_aggregation_ignores_unreported_requests(self) -> None:
        """A lane with no usage reports must render na, not a fabricated zero."""

        records = [
            {
                "lane": "zhipu-coding-plan",
                "model": "glm-5.3",
                "status": 200,
                "capacity": False,
                "successful": True,
                "protocol_result": "completed",
                "waited_ms": 1,
                "header_ms": 500,
                "first_byte_ms": 500,
                "sse_bytes": 10,
                "sse_events": 2,
                "sse_inspection": "true",
                "terminal_seen": True,
                "usage_in": None,
                "usage_cached": None,
                "usage_out": None,
                "usage_cost_usd": None,
                "result_ts": datetime(2026, 10, 31, tzinfo=timezone.utc),
                "stream_duration_s": 1.0,
            }
        ]
        summary = summarize(records, 24)
        lane = summary["lanes"][0]
        self.assertEqual(lane["token_reports"], 0)
        self.assertIsNone(lane["tokens_input"])
        self.assertIsNone(lane["cache_hit_ratio"])
        rendered = "\n".join(render(summary))
        self.assertIn("cache_hit_ratio=na", rendered)


if __name__ == "__main__":
    unittest.main()

"""Tests for scripts/cpa_error_dump_forensics.py.

The tool exists to answer "does the upstream send a capacity signal the admission
gate does not recognise?" from the bytes on the wire. Two properties matter more
than any single finding: it must never read the request side of a dump (which
carries prompt text and produced phantom markers once already), and it must not
call a response recognised unless the *deployed* vocabulary would have counted it.
"""

from __future__ import annotations

import io
import json
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, cast

MODULE = runpy.run_path(
    str(Path(__file__).parent / "scripts" / "cpa_error_dump_forensics.py")
)
response_text = cast(Any, MODULE["response_text"])
census = cast(Any, MODULE["census"])
analyse = cast(Any, MODULE["analyse"])
admission_vocabulary = cast(Any, MODULE["admission_vocabulary"])
render = cast(Any, MODULE["render"])
main = cast(Any, MODULE["main"])

MARKERS = frozenset({"server_is_overloaded", "usage_limit_reached", "rate limit"})
STATUSES = frozenset({429, 503})


def dump(*, request: str = "", response: str = "") -> str:
    return (
        "Timestamp: 2026-10-04T00:27:12.557138537+08:00\n"
        "=== request ===\n"
        f"{request}\n"
        "=== response ===\n"
        f"{response}\n"
    )


def admission_config() -> dict[str, Any]:
    return {
        "version": 1,
        "retry_after_max_seconds": 86400,
        "lanes": [
            {
                "name": "chatgpt-oauth",
                "models": ["gpt-6.1-sol"],
                "capacity_statuses": [429, 503],
                "capacity_markers": ["server_is_overloaded", "usage_limit_reached"],
            }
        ],
    }


class ResponseIsolationTests(unittest.TestCase):
    def test_only_the_response_section_is_read(self) -> None:
        raw = dump(
            request='{"messages":[{"content":"please explain server_is_overloaded"}]}',
            response="Status: 503\n\nplain body",
        )
        text = response_text(raw)
        self.assertIn("Status: 503", text)
        self.assertNotIn("please explain", text)

    def test_a_marker_in_the_request_body_does_not_count(self) -> None:
        # The measured contamination: prompt text quoting the marker produced a
        # phantom capacity signal in the 2026-09-21 doctor review.
        raw = dump(
            request='{"content":"server_is_overloaded"}',
            response="Status: 200\n\n{}",
        )
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        self.assertEqual(facts["markers"], [])
        self.assertEqual(facts["verdict"], "other")

    def test_an_unbannered_dump_yields_nothing(self) -> None:
        self.assertEqual(response_text("just prose\nserver_is_overloaded"), "")

    def test_a_file_without_a_response_banner_is_not_a_clean_bill(self) -> None:
        # Measured 2026-10-04: feeding an already-extracted response (no banner)
        # made the tool report "not a capacity response; correctly ignored" and
        # exit 0 -- a clean-looking result for a file it never analysed.
        raw = "Status: 503\nserver_is_overloaded\n"
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        self.assertEqual(facts["verdict"], "unreadable")
        self.assertEqual(facts["response_bytes"], 0)
        self.assertEqual(facts["reasons"], ["no response section"])


class VerdictTests(unittest.TestCase):
    def test_the_measured_capacity_shape_is_recognised(self) -> None:
        # Every retained dump on 2026-10-04 had exactly this shape.
        raw = dump(
            response=(
                "Status: 503\nRetry-After: 27\nContent-Type: application/json\n\n"
                '{"error":{"message":"auth_unavailable: no auth available '
                "(providers=codex, model=gpt-6.1-sol; last upstream error: "
                "server_is_overloaded: Our servers are currently overloaded.)"
                '","type":"server_error","code":"internal_server_error"}}'
            )
        )
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        self.assertEqual(facts["verdict"], "recognised")
        self.assertEqual(facts["statuses"], [503])
        self.assertEqual(facts["headers"], {"retry-after": "27"})
        self.assertEqual(facts["markers"], ["server_is_overloaded"])

    def test_a_200_with_an_in_body_marker_is_recognised_by_the_marker(self) -> None:
        # stream-bootstrap-buffering=false makes CPA echo overload as 200 + marker,
        # so the status alone would read as success.
        raw = dump(
            response=(
                "Status: 200\n\n"
                '{"error":{"message":"usage_limit_reached","type":"server_error"}}'
            )
        )
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        self.assertEqual(facts["verdict"], "recognised")
        self.assertEqual(facts["reasons"], ["marker usage_limit_reached"])

    def test_a_capacity_token_outside_the_vocabulary_is_unrecognised(self) -> None:
        # The finding this tool exists to produce: an account-capacity signal the
        # configured markers and statuses do not cover.
        raw = dump(
            response=(
                "Status: 502\n\n"
                '{"error":{"message":"insufficient_quota","type":"server_error"}}'
            )
        )
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        self.assertEqual(facts["verdict"], "unrecognised")
        self.assertEqual(facts["reasons"], ["insufficient_quota"])

    def test_a_502_without_a_capacity_token_is_not_a_capacity_response(self) -> None:
        raw = dump(response="Status: 502\n\nupstream connect error")
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        self.assertEqual(facts["verdict"], "other")

    def test_rate_limit_headers_are_surfaced_when_present(self) -> None:
        raw = dump(
            response=(
                "Status: 429\nx-ratelimit-remaining-requests: 0\n"
                "x-retry-metadata: NO_MORE_RETRY\n\n{}"
            )
        )
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        self.assertEqual(facts["verdict"], "recognised")
        self.assertEqual(
            facts["headers"],
            {
                "x-ratelimit-remaining-requests": "0",
                "x-retry-metadata": "NO_MORE_RETRY",
            },
        )

    def test_status_line_shapes_are_both_understood(self) -> None:
        self.assertEqual(census("Status: 503")["statuses"], [503])
        self.assertEqual(census("HTTP/1.1 429 Too Many Requests")["statuses"], [429])

    def test_upstream_cause_is_extracted_from_the_wrapper(self) -> None:
        # Measured 2026-10-04 (slot 2, codex.ciii.club): the provider refused
        # service and CPA wrapped it, so the 503 is counted by the gate even
        # though the account is not out of capacity.
        raw = dump(
            response=(
                "Status: 503\nRetry-After: 60\n\n"
                '{"error":{"message":"auth_unavailable: no auth available '
                "(providers=openai-compatible-codex-ciii, model=gpt-6.1-sol-ciii; "
                "last upstream error: upstream_error: Upstream access forbidden, "
                'please contact administrator)","type":"server_error",'
                '"code":"internal_server_error"}}'
            )
        )
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        self.assertEqual(facts["verdict"], "recognised")
        self.assertEqual(facts["markers"], [])
        self.assertEqual(
            facts["upstream_cause"],
            "upstream_error: Upstream access forbidden, please contact administrator",
        )

    def test_a_route_cause_is_annotated_not_silently_called_capacity(self) -> None:
        raw = dump(
            response=(
                "Status: 502\n\n"
                '{"error":{"message":"Upstream access forbidden, please contact '
                'administrator","type":"upstream_error"}}'
            )
        )
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        facts["file"] = "a.log"
        # 502 is not a capacity status and the cause is not a capacity token, so
        # the gate correctly ignores it.
        self.assertEqual(facts["verdict"], "other")
        self.assertEqual(facts["error_types"], ["upstream_error"])

    def test_recognised_by_status_only_is_annotated(self) -> None:
        raw = dump(response="Status: 503\n\nplain body with no marker")
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        facts["file"] = "a.log"
        lines = render([facts], len(MARKERS), STATUSES)
        self.assertIn("    verdict: RECOGNISED via status 503", lines)
        self.assertTrue(
            any("recognised by status only" in line for line in lines), lines
        )

    def test_a_marker_backed_recognition_is_not_annotated(self) -> None:
        raw = dump(response="Status: 200\n\nserver_is_overloaded")
        facts = analyse(response_text(raw), MARKERS, STATUSES)
        facts["file"] = "a.log"
        lines = render([facts], len(MARKERS), STATUSES)
        self.assertFalse(
            any("recognised by status only" in line for line in lines), lines
        )


class VocabularyTests(unittest.TestCase):
    def test_vocabulary_is_the_union_of_the_lanes(self) -> None:
        markers, statuses = admission_vocabulary(admission_config())
        self.assertEqual(markers, {"server_is_overloaded", "usage_limit_reached"})
        self.assertEqual(statuses, {429, 503})

    def test_vocabulary_of_an_empty_config_is_empty(self) -> None:
        self.assertEqual(admission_vocabulary({}), (frozenset(), frozenset()))


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = self.tmp / "admission.json"
        self.config.write_text(json.dumps(admission_config()), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _dump(self, name: str, response: str) -> Path:
        path = self.tmp / name
        path.write_text(dump(response=response), encoding="utf-8")
        return path

    def test_main_exits_zero_when_every_response_is_recognised(self) -> None:
        path = self._dump("a.log", "Status: 503\nserver_is_overloaded")
        with redirect_stdout(io.StringIO()) as buffer:
            code = main(["--file", str(path), "--admission-config", str(self.config)])
        self.assertEqual(code, 0)
        self.assertIn("verdict: RECOGNISED", buffer.getvalue())

    def test_main_exits_one_on_an_unrecognised_signal(self) -> None:
        path = self._dump("a.log", "Status: 502\ninsufficient_quota")
        with redirect_stdout(io.StringIO()) as buffer:
            code = main(["--file", str(path), "--admission-config", str(self.config)])
        self.assertEqual(code, 1)
        self.assertIn("UNRECOGNISED", buffer.getvalue())

    def test_main_exits_two_on_an_unreadable_config(self) -> None:
        path = self._dump("a.log", "Status: 503")
        with redirect_stdout(io.StringIO()):
            code = main(
                ["--file", str(path), "--admission-config", str(self.tmp / "nope.json")]
            )
        self.assertEqual(code, 2)

    def test_main_exits_two_when_there_is_no_response_section(self) -> None:
        path = self.tmp / "not-a-dump.txt"
        path.write_text("Status: 503\nserver_is_overloaded\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()) as buffer:
            code = main(["--file", str(path), "--admission-config", str(self.config)])
        self.assertEqual(code, 2)
        self.assertIn("NO RESPONSE SECTION", buffer.getvalue())
        self.assertIn("do not read this as a clean result", buffer.getvalue())

    def test_render_counts_an_unreadable_input_separately(self) -> None:
        facts = analyse("", MARKERS, STATUSES)
        facts["file"] = "a.log"
        lines = render([facts], len(MARKERS), STATUSES)
        self.assertIn("-- dumps=1 recognised=0 unrecognised=0 unreadable=1 --", lines)

    def test_json_output_is_machine_readable(self) -> None:
        path = self._dump("a.log", "Status: 503\nserver_is_overloaded")
        with redirect_stdout(io.StringIO()) as buffer:
            main(
                [
                    "--file",
                    str(path),
                    "--admission-config",
                    str(self.config),
                    "--json",
                ]
            )
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["results"][0]["verdict"], "recognised")
        self.assertEqual(payload["results"][0]["file"], "a.log")

    def test_render_marks_a_not_a_capacity_response_as_ignored(self) -> None:
        facts = analyse("Status: 404\n\n{}", MARKERS, STATUSES)
        facts["file"] = "a.log"
        lines = render([facts], len(MARKERS), STATUSES)
        self.assertIn("    verdict: not a capacity response; correctly ignored", lines)


if __name__ == "__main__":
    unittest.main()

"""test_stream_acceptance.py - split from test_scripts.py (cpa_stream_acceptance)."""

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import cast


class StreamAcceptanceTests(unittest.TestCase):
    def test_stream_acceptance_multiline_frames_and_comments(self) -> None:
        import io
        from scripts.cpa_stream_acceptance import sse_frames

        stream = io.BytesIO(
            b": keepalive\r\n\r\nevent: response.created\r\n"
            b'data: {"type":\r\ndata: "response.created"}\r\n\r\n'
            b"data: [DONE]\n\n"
        )
        self.assertEqual(
            list(sse_frames(stream)),
            [("response.created", '{"type":\n"response.created"}'), ("", "[DONE]")],
        )

    def test_stream_acceptance_first_event_is_not_first_text(self) -> None:
        from scripts.cpa_stream_acceptance import StreamResult

        result = StreamResult()
        result.feed("", '{"type":"response.created"}', 100)
        result.feed("", '{"type":"response.output_text.delta","delta":""}', 500)
        result.feed("", '{"type":"response.output_text.delta","delta":"po"}', 2100)
        result.feed("", '{"type":"response.output_text.delta","delta":"ng"}', 2500)
        result.feed(
            "",
            '{"type":"response.completed","response":{"status":"completed","usage":{"output_tokens":2}}}',
            2600,
        )
        result.feed("", "[DONE]", 2700)
        report = result.report()
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["first_event_ms"], 100)
        self.assertEqual(report["first_text_ms"], 2100)
        self.assertEqual(report["text_gap_max_ms"], 400)
        self.assertEqual(report["usage"], {"output_tokens": 2})

    def test_stream_acceptance_error_cannot_be_overwritten_by_completion(self) -> None:
        from scripts.cpa_stream_acceptance import StreamResult

        for event in ("error", "response.failed", "response.incomplete"):
            with self.subTest(event=event):
                result = StreamResult()
                result.feed(
                    "",
                    json.dumps(
                        {"type": event, "message": "Selected model is at capacity"}
                    ),
                    100,
                )
                result.feed(
                    "", '{"type":"response.output_text.delta","delta":"pong"}', 200
                )
                result.feed(
                    "",
                    '{"type":"response.completed","response":{"status":"completed"}}',
                    300,
                )
                report = result.report()
                self.assertEqual(report["result"], "FAIL")
                self.assertIn("stream_error", cast("list[str]", report["failures"]))
                self.assertTrue(report["capacity_marker"])

    def test_stream_acceptance_capacity_marker_fails_even_with_completion(self) -> None:
        from scripts.cpa_stream_acceptance import StreamResult

        for marker in ("Selected model is at capacity", "server_is_overloaded"):
            with self.subTest(marker=marker):
                result = StreamResult()
                result.feed(
                    "",
                    json.dumps({"type": "response.created", "message": marker}),
                    100,
                )
                result.feed(
                    "", '{"type":"response.output_text.delta","delta":"pong"}', 200
                )
                result.feed(
                    "",
                    '{"type":"response.completed","response":{"status":"completed"}}',
                    300,
                )
                report = result.report()
                self.assertEqual(report["result"], "FAIL")
                self.assertIn("capacity_marker", cast("list[str]", report["failures"]))

    def test_stream_acceptance_rejects_malformed_json_and_invalid_events(self) -> None:
        from scripts.cpa_stream_acceptance import AcceptanceError, StreamResult

        for data in ("not-json", "[]", "{}", '{"type":3}'):
            with self.subTest(data=data), self.assertRaises(AcceptanceError):
                StreamResult().feed("", data, 100)

    def test_stream_acceptance_completion_requires_success_status(self) -> None:
        from scripts.cpa_stream_acceptance import StreamResult

        for response in (
            {},
            {"status": "incomplete"},
            {"status": "completed", "error": {"message": "SECRET"}},
        ):
            with self.subTest(response=response):
                result = StreamResult()
                result.feed(
                    "", '{"type":"response.output_text.delta","delta":"pong"}', 100
                )
                result.feed(
                    "",
                    json.dumps({"type": "response.completed", "response": response}),
                    200,
                )
                self.assertEqual(result.report()["result"], "FAIL")
                self.assertNotIn("SECRET", json.dumps(result.report()))

    def test_stream_acceptance_requires_text_and_completion(self) -> None:
        from scripts.cpa_stream_acceptance import StreamResult

        result = StreamResult()
        result.feed("", "[DONE]", 100)
        self.assertEqual(result.report()["result"], "FAIL")
        result = StreamResult()
        result.feed("", '{"type":"response.output_text.delta","delta":"pong"}', 100)
        self.assertIn(
            "missing_completion",
            cast("list[str]", result.report()["failures"]),
        )

    def test_stream_acceptance_rejects_unterminated_frames(self) -> None:
        import io
        from scripts.cpa_stream_acceptance import AcceptanceError, sse_frames

        with self.assertRaisesRegex(AcceptanceError, "unterminated_frame"):
            list(sse_frames(io.BytesIO(b'data: {"type":"response.completed"}\n')))

    def test_stream_acceptance_rejects_data_after_done(self) -> None:
        from scripts.cpa_stream_acceptance import AcceptanceError, StreamResult

        result = StreamResult()
        result.feed("", "[DONE]", 100)
        with self.assertRaisesRegex(AcceptanceError, "data_after_done"):
            result.feed("", '{"type":"response.created"}', 200)

    def test_stream_acceptance_http_errors_fail_without_retry_or_secret_output(
        self,
    ) -> None:
        import io
        from scripts.cpa_stream_acceptance import main

        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(
                json.dumps({"apiKeys": [{"key": "SECRET", "enabled": True}]}),
                encoding="utf-8",
            )
            for status in (302, 401, 429, 503):
                with self.subTest(status=status):
                    connection = mock.Mock()
                    response = connection.getresponse.return_value
                    response.status = status
                    response.getheader.side_effect = lambda name, default=None: {
                        "Retry-After": "40"
                    }.get(name, default)
                    output = io.StringIO()
                    with (
                        mock.patch(
                            "http.client.HTTPConnection", return_value=connection
                        ),
                        mock.patch("threading.Timer"),
                        mock.patch("sys.stdout", output),
                    ):
                        code = main(
                            ["--manifest", str(manifest), "--model", "gpt-6-luna"]
                        )
                    self.assertEqual(code, 1)
                    self.assertNotIn("SECRET", output.getvalue())
                    self.assertIn('"result": "FAIL"', output.getvalue())
                    connection.request.assert_called_once()
                    response.readline.assert_not_called()

    def test_stream_acceptance_missing_key_does_not_send_request(self) -> None:
        from scripts.cpa_stream_acceptance import probe

        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text('{"apiKeys":[]}', encoding="utf-8")
            with mock.patch("http.client.HTTPConnection") as connection:
                result = probe(manifest, "gpt-6-luna", 14185, 240)
            connection.assert_not_called()
            self.assertEqual(result["result"], "FAIL")
            self.assertIn(
                "no_unique_enabled_key", cast("list[str]", result["failures"])
            )


if __name__ == "__main__":
    unittest.main()

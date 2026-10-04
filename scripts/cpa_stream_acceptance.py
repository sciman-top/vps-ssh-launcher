"""Single-attempt, redacted Responses SSE acceptance through a local sidecar."""

import argparse
import http.client
import json
import re
import socket
import statistics
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO


class AcceptanceError(ValueError):
    pass


def sse_frames(stream: BinaryIO) -> Iterator[tuple[str, str]]:
    event = ""
    parts: list[str] = []
    frame_size = 0
    first_line = True
    while True:
        raw = stream.readline(65537)
        if not raw:
            if parts or event:
                raise AcceptanceError("unterminated_frame")
            return
        if len(raw) > 65536:
            raise AcceptanceError("oversized_frame")
        line = raw.decode("utf-8-sig" if first_line else "utf-8").rstrip("\r\n")
        first_line = False
        if not line:
            if parts:
                yield event, "\n".join(parts)
            event, parts, frame_size = "", [], 0
            continue
        frame_size += len(raw)
        if frame_size > 1048576:
            raise AcceptanceError("oversized_frame")
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            parts.append(value)


class StreamResult:
    def __init__(self) -> None:
        self.failures: set[str] = set()
        self.first_event_ms: float | None = None
        self.text_times: list[float] = []
        self.text: list[str] = []
        self.completed_ms: float | None = None
        self.done = False
        self.capacity = False
        self.events = 0
        self.usage: dict[str, int] = {}

    def feed(self, event: str, data: str, elapsed_ms: float) -> None:
        if self.done:
            raise AcceptanceError("data_after_done")
        if data == "[DONE]":
            self.done = True
            return
        if self.first_event_ms is None:
            self.first_event_ms = elapsed_ms
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise AcceptanceError("malformed_json") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            raise AcceptanceError("invalid_event")
        kind = payload["type"]
        if event and event != "message" and event != kind:
            raise AcceptanceError("event_type_mismatch")
        self.events += 1
        encoded = json.dumps(payload).lower()
        self.capacity |= "at capacity" in encoded or "server_is_overloaded" in encoded
        if kind in ("error", "response.failed", "response.incomplete") or payload.get(
            "error"
        ):
            self.failures.add("stream_error")
        if kind == "response.output_text.delta":
            delta = payload.get("delta")
            if not isinstance(delta, str):
                raise AcceptanceError("invalid_text_delta")
            if self.completed_ms is not None:
                raise AcceptanceError("text_after_completion")
            if delta:
                self.text_times.append(elapsed_ms)
                self.text.append(delta)
        if kind == "response.completed":
            response = payload.get("response")
            if not isinstance(response, dict):
                raise AcceptanceError("invalid_completion")
            if response.get("status") != "completed" or response.get("error"):
                self.failures.add("invalid_completion")
            if self.completed_ms is not None:
                self.failures.add("duplicate_completion")
            self.completed_ms = elapsed_ms
            usage = response.get("usage")
            if isinstance(usage, dict):
                for name in ("input_tokens", "output_tokens", "total_tokens"):
                    value = usage.get(name)
                    if type(value) is int and value >= 0:
                        self.usage[name] = value
                details = usage.get("output_tokens_details")
                if isinstance(details, dict):
                    value = details.get("reasoning_tokens")
                    if type(value) is int and value >= 0:
                        self.usage["reasoning_tokens"] = value

    def report(self) -> dict[str, object]:
        if self.capacity:
            self.failures.add("capacity_marker")
        if self.completed_ms is None:
            self.failures.add("missing_completion")
        if not self.text_times:
            self.failures.add("missing_text_delta")
        matched = "".join(self.text).strip() == "pong"
        if not matched:
            self.failures.add("unexpected_text")
        # Consecutive pairs: slicing both sides keeps the two iterables the
        # same length, so `strict=True` is honest here. `zip(times, times[1:])`
        # is the same pairing but with unequal lengths, which would make a bare
        # `strict=True` raise on every run.
        gaps = [
            later - earlier
            for earlier, later in zip(
                self.text_times[:-1], self.text_times[1:], strict=True
            )
        ]
        return {
            "result": "FAIL" if self.failures else "PASS",
            "failures": sorted(self.failures),
            "first_event_ms": self.first_event_ms,
            "first_text_ms": self.text_times[0] if self.text_times else None,
            "completed_ms": self.completed_ms,
            "text_delta_count": len(self.text_times),
            "text_characters": sum(map(len, self.text)),
            "text_gap_median_ms": statistics.median(gaps) if gaps else None,
            "text_gap_max_ms": max(gaps) if gaps else None,
            "text_matches": matched,
            "data_event_count": self.events,
            "capacity_marker": self.capacity,
            "usage": self.usage,
        }


def probe(manifest: Path, model: str, port: int, timeout: float) -> dict[str, object]:
    started = time.monotonic()
    result = StreamResult()
    metadata: dict[str, object] = {
        "model": model,
        "port": port,
        "attempt": 1,
        "retries": 0,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "timeout_seconds": timeout,
    }
    connection = None
    response = None
    timer = None
    expired = threading.Event()
    stage = "manifest"
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
        keys = [
            entry["key"]
            for entry in document.get("apiKeys", [])
            if isinstance(entry, dict)
            and entry.get("enabled", True)
            and isinstance(entry.get("key"), str)
            and entry["key"]
        ]
        if len(keys) != 1:
            raise AcceptanceError("no_unique_enabled_key")
        body = json.dumps(
            {
                "model": model,
                "input": "Reply with the single word pong, with no punctuation.",
                "max_output_tokens": 512,
                "stream": True,
            }
        ).encode()
        stage = "transport"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        connection.connect()
        transport_socket = connection.sock
        if transport_socket is None:
            raise AcceptanceError("missing_transport_socket")

        def expire() -> None:
            expired.set()
            try:
                transport_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(
            max(0.001, timeout - (time.monotonic() - started)), expire
        )
        timer.daemon = True
        timer.start()
        connection.request(
            "POST",
            "/v1/responses",
            body,
            {
                "Authorization": "Bearer " + keys[0],
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        response = connection.getresponse()
        metadata["http_status"] = response.status
        metadata["header_ms"] = round((time.monotonic() - started) * 1000, 3)
        request_id = response.getheader("X-CPA-Request-Id", "")
        metadata["request_id"] = (
            request_id if re.fullmatch(r"[a-zA-Z0-9-]{8,64}", request_id) else None
        )
        retry_after = response.getheader("Retry-After")
        metadata["retry_after_present"] = retry_after is not None
        if retry_after and re.fullmatch(r"\d{1,6}", retry_after):
            metadata["retry_after_seconds"] = int(retry_after)
        reason = response.getheader("X-CPA-Admission-Reason", "")
        if reason in ("cooldown", "queue_timeout", "busy", "downstream_gone"):
            metadata["admission_reason"] = reason
        if response.status != 200:
            result.failures.add("http_error")
        elif (
            response.getheader("Content-Type", "").split(";")[0].strip().lower()
            != "text/event-stream"
        ):
            result.failures.add("invalid_content_type")
        else:
            stage = "stream"
            for event, data in sse_frames(response):
                result.feed(event, data, round((time.monotonic() - started) * 1000, 3))
    except AcceptanceError as exc:
        result.failures.add(str(exc))
    except (OSError, http.client.HTTPException):
        result.failures.add(
            "manifest_unavailable" if stage == "manifest" else "transport_error"
        )
    except (UnicodeError, ValueError, TypeError, AttributeError):
        result.failures.add(
            "invalid_manifest" if stage == "manifest" else "invalid_stream"
        )
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()
    if expired.is_set() or time.monotonic() - started > timeout:
        result.failures.add("deadline_exceeded")
    metadata["total_ms"] = round((time.monotonic() - started) * 1000, 3)
    return metadata | result.report()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", choices=("gpt-6-luna", "gpt-6.1-sol"), required=True)
    parser.add_argument("--port", type=int, choices=(10909, 14185), default=10909)
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be greater than zero and at most 300 seconds")
    report = probe(args.manifest, args.model, args.port, args.timeout)
    print("STREAM_ACCEPTANCE=" + json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

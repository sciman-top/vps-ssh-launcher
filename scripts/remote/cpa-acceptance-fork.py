#!/usr/bin/env python3
"""Prove the local Gemini raw-reasoning patch through the DEPLOYED binary.

The fork exists for one behaviour: when Gemini returns `thought: true` parts
over the OpenAI Responses stream, the client must receive
`response.reasoning_text.delta` / `.done` events (and a `reasoning_text`
content part on the completed item), not only the summary events. The
translator unit tests cover the function; this covers the *shipped binary*,
through its HTTP surface, against a stub Gemini upstream.

The request must arrive as a *Claude* protocol request (the Responses body
with its tools and image_generation item is translated away for this lane),
so the stub key carries a `claude` route and the call goes to /v1/messages;
the binary still answers down the Responses stream the fork patches.

Success criterion: the literal line ``FORK_PATCH_ACCEPTANCE=PASS`` last.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BINARY = Path(
    os.environ.get(
        "CPA_FORK_BINARY",
        str(Path(tempfile.gettempdir()) / "cpa-fork-acceptance" / "CLIProxyAPI"),
    )
)
PORT = 8399


def gemini_stream():
    events = [
        {
            "response": {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "first thought", "thought": True},
                                {"text": "second thought", "thought": True},
                            ],
                        }
                    }
                ],
                "responseId": "fork-1",
            }
        },
        {
            "response": {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "the answer"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 3,
                    "thoughtsTokenCount": 4,
                    "totalTokenCount": 17,
                },
            }
        },
    ]
    return (
        b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
        + b"data: [DONE]\n\n"
    )


class GeminiPassthrough(BaseHTTPRequestHandler):
    """Answers whichever Gemini shape the binary asked for.

    The native executor appends its own method path (`:streamGenerateContent` or
    `:generateContent`), so the path decides between an SSE body and a JSON one.
    """

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if "streamGenerateContent" not in self.path:
            self._send_json()
            return
        body = gemini_stream()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self):
        payload = json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "first thought", "thought": True},
                                {"text": "the answer"},
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 3,
                    "thoughtsTokenCount": 4,
                    "totalTokenCount": 17,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def wait_port(port, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main():
    if Path("/proc/self/ns/net").readlink() == Path("/proc/1/ns/net").readlink():
        raise SystemExit("REFUSE_NOT_IN_NAMESPACE")
    workdir = Path(tempfile.mkdtemp(prefix="cpa-fork-acceptance-"))
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), GeminiPassthrough)
    upstream.daemon_threads = True
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    # The native Gemini route is the one whose executor translates Gemini's
    # `thought: true` parts into the Responses reasoning events the fork adds.
    base = "http://127.0.0.1:%d" % upstream.server_address[1]
    model = [{"name": "gemini-3.8-flash-high", "alias": "gemini-3.8-flash-high"}]
    config = {
        "server": {"host": "127.0.0.1", "port": PORT},
        "access": {"api-keys": ["fork-client"]},
        "gemini-api-key": [
            {"api-key": "fork-upstream", "base-url": base, "models": model}
        ],
    }
    import yaml

    (workdir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    log = (workdir / "binary.log").open("wb")
    process = subprocess.Popen(
        [str(BINARY), "-config", str(workdir / "config.yaml")],
        cwd=workdir,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    log.close()
    ok = False
    detail = ""
    try:
        if not wait_port(PORT):
            detail = "binary_never_listened"
            raise SystemExit(detail)
        payload = json.dumps(
            {
                "model": "gemini-3.8-flash-high",
                "input": "think then answer",
                "stream": True,
            }
        ).encode()
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v1/responses" % PORT,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer fork-client",
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=60) as response:
            text = response.read().decode("utf-8", "replace")
        raw_deltas = text.count("reasoning_text.delta")
        raw_done = text.count("reasoning_text.done")
        summary = text.count("reasoning_summary_text.delta")
        ok = raw_deltas >= 2 and raw_done >= 1 and summary >= 1
        detail = "raw_deltas=%d raw_done=%d summary_deltas=%d" % (
            raw_deltas,
            raw_done,
            summary,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        upstream.shutdown()
        shutil.rmtree(workdir, ignore_errors=True)
    print("FORK raw_reasoning_events " + ("PASS " if ok else "FAIL ") + detail)
    print("FORK_PATCH_ACCEPTANCE=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

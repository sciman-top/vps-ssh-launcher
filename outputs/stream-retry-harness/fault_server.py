#!/usr/bin/env python3
"""Local SSE fault injector for verifying Codex stream-retry behaviour.

First request: 200 + a partial SSE event, then an abrupt socket close.
Second and later: replays a complete, schema-valid event stream.

Serves only 127.0.0.1 traffic and prints no request bodies.
"""

import http.server
import json
import pathlib
import socket
import struct
import sys
import threading

SSE_FILE = pathlib.Path(sys.argv[1])
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8899
MODE = sys.argv[3] if len(sys.argv) > 3 else "truncate"
FULL = SSE_FILE.read_bytes()
SPLIT = max(1, int(len(FULL) * 0.35))
PARTIAL = FULL[:SPLIT]
STATE = {"attempts": 0}
LOCK = threading.Lock()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A002
        sys.stderr.write("srv " + fmt % args + "\n")

    def do_GET(self):  # noqa: N802
        if self.path == "/reset":
            with LOCK:
                STATE["attempts"] = 0
            payload = json.dumps(STATE).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/state":
            payload = json.dumps(STATE).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        with LOCK:
            STATE["attempts"] += 1
            attempt = STATE["attempts"]
        if MODE == "503" and attempt == 1 and attempt == STATE["attempts"]:
            payload = b'{"error":{"message":"CPA upstream is temporarily unavailable.","type":"upstream_unavailable","code":"upstream_transport_error"}}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "1")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            return
        if attempt == 1:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(f"{len(PARTIAL):X}\r\n".encode() + PARTIAL + b"\r\n")
            self.wfile.flush()
            # Abort without the terminating chunk: exactly the client-visible
            # "stream interrupted" shape.
            self.connection.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            self.connection.close()
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.write(f"{len(FULL):X}\r\n".encode() + FULL + b"\r\n0\r\n\r\n")
        self.wfile.flush()
        self.close_connection = True


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    server = Server(("127.0.0.1", PORT), Handler)
    print(json.dumps({"listening": PORT, "full_bytes": len(FULL), "partial_bytes": SPLIT}), flush=True)
    server.serve_forever()

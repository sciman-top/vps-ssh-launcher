#!/usr/bin/env python3
"""Bounded admission and circuit breaking for shared official-account lanes.

The process is deliberately a one-shot proxy: it never retries an upstream
request and it never rewrites a requested model. It serializes only models
declared as sharing one reviewed provider account, while unrelated providers
and unparsed request shapes pass through CPA unchanged.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("admission config must be a version 1 mapping")
    for key in ("max_body_bytes", "probe_bytes", "retry_after_max_seconds"):
        _positive_int(config.get(key), key)
    if config.get("listen_host") != "127.0.0.1":
        raise ValueError("listen_host must remain 127.0.0.1")
    if config.get("upstream_host") != "127.0.0.1":
        raise ValueError("upstream_host must remain 127.0.0.1")
    if config.get("listen_port") != 8318:
        raise ValueError("listen_port must remain 8318")
    if config.get("upstream_port") != 8317:
        raise ValueError("upstream_port must remain 8317")
    if config["probe_bytes"] > config["max_body_bytes"]:
        raise ValueError("probe_bytes must not exceed max_body_bytes")

    lanes = config.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise ValueError("lanes must be a non-empty list")
    model_lanes: dict[str, str] = {}
    normalized_lanes: list[dict[str, Any]] = []
    for index, raw_lane in enumerate(lanes):
        if not isinstance(raw_lane, dict):
            raise ValueError(f"lanes[{index}] must be a mapping")
        name = raw_lane.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"lanes[{index}].name must be non-empty")
        name = name.strip()
        models = raw_lane.get("models")
        if (
            not isinstance(models, list)
            or not models
            or not all(isinstance(item, str) and item.strip() for item in models)
        ):
            raise ValueError(f"lanes[{index}].models must be a non-empty list")
        normalized_models = tuple(
            dict.fromkeys(item.strip().lower() for item in models)
        )
        if len(normalized_models) != len(models):
            raise ValueError(f"lanes[{index}].models must not contain duplicates")
        for model in normalized_models:
            if model in model_lanes:
                raise ValueError(f"model {model!r} is assigned to multiple lanes")
            model_lanes[model] = name
        max_inflight = _positive_int(
            raw_lane.get("max_inflight"), f"lanes[{index}].max_inflight"
        )
        if max_inflight != 1:
            raise ValueError(f"lanes[{index}].max_inflight must remain 1")
        max_pending = raw_lane.get("max_pending")
        if isinstance(max_pending, bool) or not isinstance(max_pending, int):
            raise ValueError(f"lanes[{index}].max_pending must be 0 or 1")
        if max_pending < 0 or max_pending > 1:
            raise ValueError(f"lanes[{index}].max_pending must be 0 or 1")
        queue_timeout = _positive_int(
            raw_lane.get("queue_timeout_seconds"),
            f"lanes[{index}].queue_timeout_seconds",
        )
        if queue_timeout > 60:
            raise ValueError(
                f"lanes[{index}].queue_timeout_seconds must be at most 60"
            )
        schedule = raw_lane.get("cooldown_schedule_seconds")
        if (
            not isinstance(schedule, list)
            or not schedule
            or not all(type(item) is int and item > 0 for item in schedule)
        ):
            raise ValueError(
                f"lanes[{index}].cooldown_schedule_seconds must be positive"
            )
        schedule_cap = _positive_int(
            raw_lane.get("cooldown_cap_seconds"),
            f"lanes[{index}].cooldown_cap_seconds",
        )
        if schedule[-1] != schedule_cap:
            raise ValueError(
                f"lanes[{index}] cooldown schedule must end at cooldown_cap_seconds"
            )
        if schedule_cap > config["retry_after_max_seconds"]:
            raise ValueError(
                f"lanes[{index}].cooldown_cap_seconds must not exceed "
                "retry_after_max_seconds"
            )
        statuses = raw_lane.get("capacity_statuses")
        if (
            not isinstance(statuses, list)
            or not statuses
            or not all(
                type(item) is int and 100 <= item <= 599 for item in statuses
            )
        ):
            raise ValueError(
                f"lanes[{index}].capacity_statuses must contain HTTP statuses"
            )
        if 429 not in statuses:
            raise ValueError(
                f"lanes[{index}].capacity_statuses must include 429"
            )
        markers = raw_lane.get("capacity_markers")
        if (
            not isinstance(markers, list)
            or not markers
            or not all(isinstance(item, str) and item.strip() for item in markers)
        ):
            raise ValueError(
                f"lanes[{index}].capacity_markers must be non-empty strings"
            )
        normalized_lanes.append(
            {
                "name": name,
                "models": normalized_models,
                "max_inflight": max_inflight,
                "max_pending": max_pending,
                "queue_timeout_seconds": queue_timeout,
                "cooldown_schedule_seconds": tuple(schedule),
                "cooldown_cap_seconds": schedule_cap,
                "retry_after_max_seconds": config["retry_after_max_seconds"],
                "capacity_statuses": frozenset(statuses),
                "capacity_markers": tuple(
                    item.strip().lower() for item in markers
                ),
            }
        )
    config["lanes"] = tuple(normalized_lanes)
    config["model_lanes"] = model_lanes
    return config


def parse_retry_after(value: str | None, now: float | None = None) -> int | None:
    """Return a bounded positive Retry-After duration, or None if invalid."""

    if not value:
        return None
    value = value.strip()
    try:
        seconds = int(value)
        return max(1, seconds)
    except ValueError:
        pass
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        current = time.time() if now is None else now
        return max(1, math.ceil(date.timestamp() - current))
    except (TypeError, ValueError, OverflowError):
        return None


def is_capacity_response(
    status: int,
    body_prefix: bytes,
    retry_after: str | None,
    config: dict[str, Any],
) -> bool:
    if status in config["capacity_statuses"]:
        return True
    text = body_prefix.decode("utf-8", errors="ignore").lower()
    return any(marker in text for marker in config["capacity_markers"])


def requested_lane(
    path: str, body: bytes, config: dict[str, Any]
) -> tuple[str, str] | None:
    route = urlsplit(path).path.rstrip("/").lower()
    if not (route.endswith("/chat/completions") or route.endswith("/responses")):
        return None
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    model = payload.get("model") if isinstance(payload, dict) else None
    if not isinstance(model, str):
        return None
    normalized = model.strip().lower()
    lane = config["model_lanes"].get(normalized)
    return (lane, normalized) if lane is not None else None


@dataclass(frozen=True)
class Lease:
    admitted: bool
    reason: str
    retry_after: int
    probe: bool = False


class LaneState:
    """Thread-safe single-flight lane with bounded pending work."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._condition = threading.Condition()
        self._max_inflight = config["max_inflight"]
        self._max_pending = config["max_pending"]
        self._queue_timeout = float(config["queue_timeout_seconds"])
        self._schedule = config["cooldown_schedule_seconds"]
        self._retry_after_max = config["retry_after_max_seconds"]
        self.inflight = 0
        self.pending = 0
        self.failure_streak = 0
        self.open_until = 0.0
        self.probe_inflight = False

    def _open_retry_after(self, now: float) -> int:
        return max(1, math.ceil(self.open_until - now))

    def acquire(self) -> Lease:
        deadline = time.monotonic() + self._queue_timeout
        with self._condition:
            while True:
                now = time.monotonic()
                if self.open_until > now:
                    return Lease(False, "cooldown", self._open_retry_after(now))
                if self.probe_inflight:
                    return Lease(False, "half_open_probe", 1)
                if self.open_until and self.open_until <= now:
                    self.probe_inflight = True
                    self.inflight += 1
                    return Lease(True, "half_open", 0, probe=True)
                if self.inflight < self._max_inflight:
                    self.inflight += 1
                    return Lease(True, "admitted", 0)
                if self.pending >= self._max_pending:
                    return Lease(False, "busy", 1)
                self.pending += 1
                remaining = deadline - now
                if remaining <= 0:
                    self.pending -= 1
                    return Lease(False, "queue_timeout", 1)
                self._condition.wait(timeout=remaining)
                self.pending -= 1

    def release(
        self,
        lease: Lease,
        *,
        capacity_error: bool,
        retry_after: int | None,
    ) -> None:
        now = time.monotonic()
        with self._condition:
            self.inflight = max(0, self.inflight - 1)
            if lease.probe:
                self.probe_inflight = False
            if capacity_error:
                self.failure_streak += 1
                if retry_after is None:
                    index = min(self.failure_streak - 1, len(self._schedule) - 1)
                    delay = self._schedule[index]
                else:
                    delay = max(1, retry_after)
                self.open_until = now + min(delay, self._retry_after_max)
            elif lease.probe:
                self.failure_streak = 0
                self.open_until = 0.0
            self._condition.notify_all()

    def snapshot(self) -> dict[str, int | float | bool]:
        now = time.monotonic()
        with self._condition:
            return {
                "inflight": self.inflight,
                "pending": self.pending,
                "failure_streak": self.failure_streak,
                "cooldown_remaining": max(0, math.ceil(self.open_until - now)),
                "half_open_probe": self.probe_inflight,
            }


class AdmissionProxy:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.lanes = {
            lane["name"]: LaneState(lane)
            for lane in config["lanes"]
        }
        self.lane_config = {
            lane["name"]: lane
            for lane in config["lanes"]
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "listen": (
                f"{self.config['listen_host']}:{self.config['listen_port']}"
            ),
            "upstream": f"{self.config['upstream_host']}:{self.config['upstream_port']}",
            "retry_after_max_seconds": self.config["retry_after_max_seconds"],
            "lanes": {
                name: {
                    "models": list(self.lane_config[name]["models"]),
                    "state": lane.snapshot(),
                }
                for name, lane in self.lanes.items()
            },
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self) -> None:
        proxy: AdmissionProxy = self.server.proxy  # type: ignore[attr-defined]
        config = proxy.config
        if self.command == "GET" and urlsplit(self.path).path == "/healthz":
            self._send_json(200, proxy.health())
            return
        body = self._read_body(config["max_body_bytes"])
        selection = requested_lane(self.path, body, config)
        lane_name = selection[0] if selection is not None else None
        model = selection[1] if selection is not None else None
        lane_config = (
            proxy.lane_config[lane_name]
            if lane_name is not None
            else None
        )
        lease: Lease | None = None
        if lane_name is not None and lane_config is not None:
            lease = proxy.lanes[lane_name].acquire()
            if not lease.admitted:
                self._send_json(
                    429,
                    {
                        "error": {
                            "message": (
                                f"Shared upstream lane {lane_name} is cooling "
                                "down or busy; retry after the advertised interval."
                            ),
                            "type": "official_account_admission_circuit_open",
                            "code": "shared_upstream_capacity",
                        }
                    },
                    retry_after=lease.retry_after,
                )
                logging.info(
                    "lane_reject lane=%s model=%s reason=%s retry_after=%s",
                    lane_name,
                    model,
                    lease.reason,
                    lease.retry_after,
                )
                return
        capacity_error = False
        retry_after: int | None = None
        response_started = False
        conn: http.client.HTTPConnection | None = None
        try:
            conn = http.client.HTTPConnection(
                config["upstream_host"], config["upstream_port"], timeout=300
            )
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
            }
            headers["Host"] = f"{config['upstream_host']}:{config['upstream_port']}"
            headers["Content-Length"] = str(len(body))
            headers["Connection"] = "close"
            conn.request(self.command, self.path, body=body, headers=headers)
            response = conn.getresponse()
            retry_after_header = response.getheader("Retry-After")
            retry_after = parse_retry_after(retry_after_header)
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() in HOP_BY_HOP_HEADERS:
                    continue
                self.send_header(key, value)
            self.end_headers()
            response_started = True
            probe = bytearray()
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                if len(probe) < config["probe_bytes"]:
                    probe.extend(chunk[: config["probe_bytes"] - len(probe)])
                self.wfile.write(chunk)
                self.wfile.flush()
            capacity_error = (
                lease is not None
                and lane_config is not None
                and is_capacity_response(
                    response.status,
                    bytes(probe),
                    retry_after_header,
                    lane_config,
                )
            )
            logging.info(
                "upstream_result lane=%s model=%s status=%s capacity=%s retry_after=%s",
                lane_name or "passthrough",
                model or "other",
                response.status,
                str(capacity_error).lower(),
                "present" if retry_after_header else "absent",
            )
        except (OSError, http.client.HTTPException) as exc:
            capacity_error = lease is not None
            logging.warning(
                "upstream_error lane=%s model=%s type=%s",
                lane_name or "passthrough",
                model or "other",
                type(exc).__name__,
            )
            if not response_started:
                self._send_json(
                    503,
                    {
                        "error": {
                            "message": "CPA upstream is temporarily unavailable.",
                            "type": "upstream_unavailable",
                            "code": "upstream_transport_error",
                        }
                    },
                    retry_after=60 if lease else None,
                )
        finally:
            if conn is not None:
                conn.close()
            if lease is not None and lane_name is not None:
                proxy.lanes[lane_name].release(
                    lease, capacity_error=capacity_error, retry_after=retry_after
                )

    def _read_body(self, max_body_bytes: int) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if transfer_encoding:
            if transfer_encoding != "chunked":
                raise ValueError("unsupported transfer encoding")
            body = bytearray()
            while True:
                line = self.rfile.readline(65537)
                if not line or len(line) > 65536:
                    raise ValueError("invalid chunked request body")
                try:
                    size = int(line.split(b";", 1)[0].strip(), 16)
                except ValueError as exc:
                    raise ValueError("invalid chunk size") from exc
                if size < 0:
                    raise ValueError("invalid chunk size")
                if size == 0:
                    while True:
                        trailer = self.rfile.readline(65537)
                        if not trailer or trailer in {b"\r\n", b"\n"}:
                            return bytes(body)
                        if len(trailer) > 65536:
                            raise ValueError("invalid chunked trailer")
                if len(body) + size > max_body_bytes:
                    raise ValueError("request body exceeds configured limit")
                body.extend(self.rfile.read(size))
                if self.rfile.read(2) != b"\r\n":
                    raise ValueError("invalid chunk delimiter")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length < 0 or length > max_body_bytes:
            raise ValueError("request body exceeds configured limit")
        return self.rfile.read(length)

    def _send_json(
        self, status: int, payload: dict[str, Any], retry_after: int | None = None
    ) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        if retry_after is not None:
            self.send_header("Retry-After", str(max(1, retry_after)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._proxy()
        except ValueError as exc:
            self._send_json(
                400, {"error": {"message": str(exc), "type": "invalid_request"}}
            )

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._proxy()
        except ValueError as exc:
            self._send_json(
                400, {"error": {"message": str(exc), "type": "invalid_request"}}
            )

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("http " + fmt, *args)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], proxy: AdmissionProxy) -> None:
        self.proxy = proxy
        super().__init__(address, Handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    proxy = AdmissionProxy(config)
    server = Server((config["listen_host"], config["listen_port"]), proxy)
    lane_summary = ",".join(
        f"{lane['name']}:{len(lane['models'])}" for lane in config["lanes"]
    )
    logging.info(
        "ready listen=%s:%s upstream=%s:%s lanes=%s",
        config["listen_host"],
        config["listen_port"],
        config["upstream_host"],
        config["upstream_port"],
        lane_summary,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

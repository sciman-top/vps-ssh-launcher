#!/usr/bin/env python3
"""One controlled live generation through the desktop's real fq.sciman.top entry.

Asserts the deployed admission generation (5273f59e) admits a healthy request
end to end: HTTP 200, no admission-reason/retry-after headers, no capacity
marker in the SSE stream, a completed response with usage. The API key is read
from the Cockpit provider registry and never printed. Proxy env is disabled so
the probe rides the same direct path as the desktop entry.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

REGISTRY = Path.home() / ".antigravity_cockpit" / "codex_model_providers.json"
MODEL = "gpt-6.1-sol"
MODEL_MARKERS = ("selected model is at capacity", "model_at_capacity")
LANE_MARKERS = MODEL_MARKERS + (
    "server_is_overloaded",
    "rate limit",
    "rate_limit",
    "usage limit",
    "usage_limit_reached",
)


def load_entry() -> tuple[str, str, str]:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    providers = data if isinstance(data, list) else data.get("providers", data)
    items = providers if isinstance(providers, list) else list(providers.values())
    for prov in items:
        if isinstance(prov, dict) and "fq.sciman.top" in str(prov.get("baseUrl", "")):
            key = prov["apiKeys"][0]["apiKey"]
            key_id = prov["apiKeys"][0]["id"]
            return prov["baseUrl"], key, key_id
    raise SystemExit("fq.sciman.top provider entry not found")


def main() -> None:
    base_url, key, key_id = load_entry()
    url = base_url.rstrip("/") + "/responses"
    body = json.dumps(
        {
            "model": MODEL,
            "input": "Reply with exactly: ok",
            "stream": True,
            "reasoning": {"effort": "low"},
        }
    ).encode("utf-8")

    started = time.perf_counter()
    header_ms = first_event_ms = first_text_ms = None
    completed_ms = None
    status = None
    resp_headers: dict[str, str] = {}
    data_events = 0
    text_deltas = 0
    text_chars = 0
    usage: dict[str, object] = {}
    failures: list[str] = []
    body_sample = ""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    try:
        with opener.open(request, timeout=120) as resp:
            status = resp.status
            resp_headers = {
                k.lower(): v for k, v in resp.headers.items()
            }
            header_ms = (time.perf_counter() - started) * 1000
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if header_ms is not None and first_event_ms is None and line:
                    first_event_ms = (time.perf_counter() - started) * 1000
                if not line.startswith("data:"):
                    continue
                data_events += 1
                payload = line[5:].strip()
                if body_sample == "" and payload:
                    body_sample = payload[:400]
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type", "")
                if etype == "response.output_text.delta":
                    if first_text_ms is None:
                        first_text_ms = (time.perf_counter() - started) * 1000
                    text_deltas += 1
                    text_chars += len(event.get("delta", ""))
                elif etype == "response.completed":
                    completed_ms = (time.perf_counter() - started) * 1000
                    usage = event.get("response", {}).get("usage", {})
                    text = json.dumps(event)
                    if any(marker in text.lower() for marker in LANE_MARKERS):
                        failures.append("capacity_marker_in_completed")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body_sample = exc.read(400).decode("utf-8", "replace")
        failures.append(f"http_{status}")
    except Exception as exc:  # noqa: BLE001 - probe records then judges
        failures.append(f"{type(exc).__name__}: {exc}")

    if status != 200:
        failures.append(f"http_status_{status}")
    if resp_headers.get("x-cpa-admission-reason"):
        failures.append("admission_reason_present")
    if resp_headers.get("retry-after"):
        failures.append("retry_after_present")
    if any(marker in body_sample.lower() for marker in LANE_MARKERS):
        failures.append("capacity_marker_in_first_event")
    if completed_ms is None:
        failures.append("no_completed_event")

    receipt = {
        "result": "PASS" if not failures else "FAIL",
        "failures": failures,
        "http_status": status,
        "endpoint_host": "fq.sciman.top",
        "endpoint": base_url.split("//", 1)[1].split("/", 1)[0],
        "model": MODEL,
        "key_id": key_id,
        "key_sha12": hashlib.sha256(key.encode()).hexdigest()[:12],
        "admission_generation_expected": "5273f59e",
        "x_cpa_request_id": resp_headers.get("x-cpa-request-id"),
        "admission_reason": resp_headers.get("x-cpa-admission-reason"),
        "retry_after": resp_headers.get("retry-after"),
        "header_ms": round(header_ms or -1, 1),
        "first_event_ms": round(first_event_ms or -1, 1),
        "first_text_ms": round(first_text_ms or -1, 1),
        "completed_ms": round(completed_ms or -1, 1),
        "data_event_count": data_events,
        "text_delta_count": text_deltas,
        "text_characters": text_chars,
        "usage": usage,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()

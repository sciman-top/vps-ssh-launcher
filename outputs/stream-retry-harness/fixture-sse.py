#!/usr/bin/env python3
"""Emit a minimal schema-valid Responses SSE fixture without a network call.

This is the fallback for the reachability check when the public gateway cannot
be used for a fresh capture. The fixture deliberately carries the requested
DeepSeek model so the test cannot silently pass against another model.
"""

import argparse
import json
from pathlib import Path


def frame(kind: str, payload: dict[str, object]) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def fixture(model: str) -> str:
    response = {
        "id": "resp_retry_fixture",
        "object": "response",
        "created": 1793530000,
        "created_at": 1793530000,
        "status": "in_progress",
        "model": model,
        "output": [],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }
    frames = [
        frame("response.created", {"type": "response.created", "response": response}),
        frame(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": "msg_retry_fixture",
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                },
            },
        ),
        frame(
            "response.output_text.delta",
            {
                "type": "response.output_text.delta",
                "item_id": "msg_retry_fixture",
                "output_index": 0,
                "content_index": 0,
                "delta": "OK",
            },
        ),
        frame(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    **response,
                    "status": "completed",
                    "output": [
                        {
                            "id": "msg_retry_fixture",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "OK",
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            },
        ),
    ]
    return "".join(frames)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(fixture(args.model), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

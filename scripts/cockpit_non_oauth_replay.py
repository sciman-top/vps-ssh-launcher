"""Single bounded non-OAuth replay through the local provider gateway.

This is the maintained counterpart of the historical outputs harness.  It uses
the configured local bearer token and the zhipu ``glm-5.3`` lane by default, so
it does not consume the single ChatGPT OAuth account.  It performs one request,
never retries, and prints only a redacted response prefix.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import time
import urllib.error
import urllib.request


def read_bearer(config: pathlib.Path) -> str:
    text = config.read_text(encoding="utf-8")
    match = re.search(r'experimental_bearer_token\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("experimental_bearer_token not found")
    return match.group(1)


def run(endpoint: str, model: str, token: str, timeout: float) -> int:
    payload = json.dumps(
        {"model": model, "input": "reply with the single word ok", "stream": False}
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    except (OSError, TimeoutError) as exc:
        print(f"TRANSPORT_ERROR={type(exc).__name__}")
        return 1
    elapsed = time.monotonic() - started
    text = body.decode("utf-8", "replace")
    completed = '"status":"completed"' in text or '"status": "completed"' in text
    print(f"HTTP_STATUS={status}")
    print(f"ELAPSED_SECONDS={elapsed:.3f}")
    print(f"RESPONSE_BYTES={len(body)}")
    print(f"COMPLETED_MARKER={str(completed).lower()}")
    print("BODY_HEAD=" + text[:240].replace("\n", " "))
    ok = status == 200 and completed
    print("CONTROLLED_REPLAY_PASS" if ok else "CONTROLLED_REPLAY_FAIL")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codex" / "config.toml",
    )
    parser.add_argument("--endpoint", default="http://127.0.0.1:10909/v1/responses")
    parser.add_argument("--model", default="glm-5.3")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 300:
        parser.error("--timeout must be greater than zero and at most 300 seconds")
    if args.model != "glm-5.3":
        parser.error(
            "this bounded replay is intentionally limited to non-OAuth glm-5.3"
        )
    token = read_bearer(args.config)
    print(f"ENDPOINT={args.endpoint}")
    print(f"MODEL={args.model}")
    print(f"TOKEN_LENGTH={len(token)}")
    return run(args.endpoint, args.model, token, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())

#requires -Version 7
# Focused post-upgrade acceptance for the v8.0.61 + WorkBuddy 0.9.89 window.
# The v8.0.59 lane singles already proved every lane end-to-end; this window
# changed only the WorkBuddy plugin semantics (model-scoped 11134 provider
# faults cool one credential/model pair, issue #80). So this script proves:
#   1. the WorkBuddy data plane still completes through the rebuilt host,
#   2. the rebuilt fork binary still emits raw Gemini reasoning deltas, which
#      is the only reason this deployment carries a local patch at all.
# Exactly one streamed request per probe, sequential, no retries. The client
# key is read on bwg only and never echoed; bodies are never printed.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
ROOT=$(mktemp -d /root/lane-singles-v8061-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$ROOT"' EXIT
API_KEY=$(python3 - <<'PY'
import yaml
from pathlib import Path

data = yaml.safe_load(Path('/opt/cliproxyapi/config.yaml').read_text())
print(data['access']['api-keys'][0])
PY
)
[ -n "$API_KEY" ]

python3 - "$API_KEY" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

api_key = sys.argv[1]
BASE = "http://127.0.0.1:8318"


def stream(path, body):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, response.read().decode(errors="replace"), started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace"), started
    except OSError as exc:
        return 0, "transport_error:" + type(exc).__name__, started



def workbuddy():
    status, text, started = stream(
        "/v1/chat/completions",
        {
            "model": "deepseek-v4.1-flash",
            "stream": True,
            "messages": [{"role": "user", "content": "Return the word PONG only."}],
        },
    )
    events, terminal, parts = 0, False, []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            terminal = True
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        events += 1
        if event.get("type") in (
            "response.completed",
            "response.failed",
            "response.incomplete",
        ):
            terminal = True
        choices = event.get("choices") or [{}]
        parts.append(str((choices[0].get("delta") or {}).get("content") or ""))
    body = "".join(parts).strip()
    print(
        f"PROBE workbuddy-deepseek-v4.1-flash http={status} events={events} "
        f"terminal={str(terminal).lower()} latency={time.time() - started:.1f}s "
        f"text={body!r}"
    )
    return 0 if status == 200 and terminal and "PONG" in body.upper() else 30


def gemini_raw_reasoning():
    status, text, started = stream(
        "/v1/responses",
        {
            "model": "gemini-3.8-flash-high",
            "stream": True,
            "input": (
                "Walk through, step by step, what 17 times 23 is. "
                "Show every partial product before giving the final number."
            ),
            "reasoning": {"effort": "high", "summary": "auto"},
            "max_output_tokens": 1600,
        },
    )
    kinds = {}
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            kinds["[DONE]"] = kinds.get("[DONE]", 0) + 1
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        kind = str(event.get("type"))
        kinds[kind] = kinds.get(kind, 0) + 1
    raw_delta = kinds.get("response.reasoning_text.delta", 0)
    summary_delta = kinds.get("response.reasoning_summary_text.delta", 0)
    reasoning_item = kinds.get("response.output_item.added", 0)
    completed = kinds.get("response.completed", 0)
    print(
        f"PROBE gemini-3.8-flash-high http={status} completed={completed} "
        f"reasoning_text_delta={raw_delta} reasoning_summary_delta={summary_delta} "
        f"latency={time.time() - started:.1f}s kinds={json.dumps(kinds, sort_keys=True)}"
    )
    if status != 200 or completed < 1:
        return 30
    if raw_delta < 1:
        # Without raw reasoning deltas the local patch would be pointless; a
        # silent absence in a reasoning-bearing turn means the rebuild lost it
        # and an image-only upgrade would have been equivalent.
        return 31
    return 0


rc = workbuddy()
rc |= gemini_raw_reasoning()
raise SystemExit(rc)
PY
echo LANE_SINGLES_V8061_ACCEPTANCE=PASS
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 600

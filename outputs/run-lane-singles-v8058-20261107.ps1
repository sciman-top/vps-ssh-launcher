#requires -Version 7
# Post-upgrade lane singles for the v8.0.58 window: exactly one streamed
# PONG per lane that had no natural traffic in the post-swap audit window --
# antigravity-oauth (gemini-3.8-flash-high) and the non-OAuth zhipu route
# (glm-5.3-flash, the updater's representative generation model). Sequential
# single shots, no retries; the second fires only after the first completes.
# The client key is read only on bwg and is never echoed. Bodies are never
# printed; receipts carry status/event/terminal/text metadata only.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
ROOT=$(mktemp -d /root/lane-singles-v8058-XXXXXXXX)
trap 'rm -rf --one-file-system -- "$ROOT"' EXIT
API_KEY=$(python3 - <<'PY'
import yaml
from pathlib import Path

data = yaml.safe_load(Path('/opt/cliproxyapi/config.yaml').read_text())
print(data['access']['api-keys'][0])
PY
)
[ -n "$API_KEY" ]

singles() {
python3 - "$API_KEY" "$ROOT" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

api_key, root = sys.argv[1], sys.argv[2]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
MODEL_LANES = (
    ("antigravity-oauth", "gemini-3.8-flash-high"),
    ("zhipu-coding-plan", "glm-5.3-flash"),
)


def single(model):
    body = json.dumps(
        {
            "model": model,
            "stream": True,
            "messages": [{"role": "user", "content": "Return the word PONG only."}],
        }
    ).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8318/v1/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
    )
    started = time.time()
    try:
        with opener.open(request, timeout=120) as response:
            status = response.status
            text = response.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        print(f"LANE_SINGLE {model} http={exc.code} TERMINAL=false")
        return 30
    except OSError as exc:
        print(f"LANE_SINGLE {model} transport_error class={type(exc).__name__}")
        return 30
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
    text_body = "".join(parts).strip()
    print(
        f"LANE_SINGLE {model} http={status} events={events} "
        f"terminal={str(terminal).lower()} latency={time.time() - started:.1f}s "
        f"text={text_body!r}"
    )
    return 0 if terminal and "PONG" in text_body.upper() else 30


rc = 0
for lane, model in MODEL_LANES:
    rc |= single(model)
raise SystemExit(rc)
PY
}
singles
echo LANE_SINGLES_V8058_ACCEPTANCE=PASS
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile bwg -Command $command `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 420

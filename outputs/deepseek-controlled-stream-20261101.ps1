#requires -Version 7
# One controlled DeepSeek Responses SSE request through BWG admission.
# The client key is read inside the remote process and never printed.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$remote = @'
python3 - <<'PY'
import json
import time
import urllib.request
import yaml

with open("/opt/cliproxyapi/config.yaml", encoding="utf-8") as handle:
    key = yaml.safe_load(handle)["access"]["api-keys"][0]
body = json.dumps({
    "model": "deepseek-v4.1-flash",
    "input": "Reply with exactly: OK",
    "max_output_tokens": 1024,
    "stream": True,
}).encode()
request = urllib.request.Request(
    "http://127.0.0.1:8318/v1/responses",
    data=body,
    headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    },
    method="POST",
)
started = time.monotonic()
try:
    with urllib.request.urlopen(request, timeout=120) as response:
        content_type = response.headers.get("Content-Type", "")
        request_id = response.headers.get("X-CPA-Request-Id", "")
        status = response.status
        events = 0
        malformed = 0
        last_event = "none"
        completed = False
        failed = False
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                malformed += 1
                continue
            events += 1
            kind = event.get("type")
            if isinstance(kind, str):
                last_event = kind
            if kind == "response.completed":
                completed = True
            elif kind in ("response.failed", "response.incomplete", "error"):
                failed = True
except Exception as exc:
    print("CONTROLLED_STREAM status=transport_error error_class=" + type(exc).__name__)
    raise SystemExit(10)
elapsed = round(time.monotonic() - started, 3)
request_id_prefix = request_id[:16] if len(request_id) >= 16 else request_id
print(
    "CONTROLLED_STREAM "
    f"status={status} content_type={content_type} "
    f"elapsed_s={elapsed} events={events} malformed={malformed} "
    f"last_event={last_event} completed={str(completed).lower()} "
    f"failed={str(failed).lower()} request_id_prefix={request_id_prefix}"
)
raise SystemExit(0 if completed and not malformed and not failed else 10)
PY
'@

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 140 -HardTimeoutSeconds 180 `
  -RemoteTempPrefix "deepseek-controlled-20261101"

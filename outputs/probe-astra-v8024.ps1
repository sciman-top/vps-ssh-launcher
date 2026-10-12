#requires -Version 7
# 2026-10-12 BWG sol-input focused probe after two quality-canary 502/defer
# results: one single-shot generation on gpt-6-astra, plus marker-only reads of
# the newest retained error dumps (status/code lines only, never bodies).
param([string]$Profile = "bwg")
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$remote = @'
set -Eeuo pipefail
D=/opt/cliproxyapi
KEY=$(python3 -c "import yaml;print(yaml.safe_load(open('$D/config.yaml'))['access']['api-keys'][0])")
echo "== single astra probe =="
python3 - "$KEY" <<'PY'
import json, sys, time, urllib.request
key = sys.argv[1]
body = json.dumps({
    "model": "gpt-6.1-sol-input",
    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    "max_tokens": 1024,
}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8317/v1/chat/completions",
    data=body,
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
)
started = time.monotonic()
try:
    with urllib.request.urlopen(req, timeout=60) as response:
        data = json.load(response)
        choice = (data.get("choices") or [{}])[0]
        print("SOLINPUT_PROBE status=200 model=%s finish=%s content_ok=%s latency_ms=%.0f" % (
            data.get("model"), choice.get("finish_reason"),
            str(choice.get("message", {}).get("content")).strip() == "OK",
            (time.monotonic() - started) * 1000))
except urllib.error.HTTPError as error:
    print("SOLINPUT_PROBE status=%d latency_ms=%.0f body_head=%s" % (
        error.code, (time.monotonic() - started) * 1000, error.read()[:200].decode(errors="replace")))
PY
echo "== catalog now =="
python3 - "$KEY" <<'PY'
import json, sys, urllib.request
key = sys.argv[1]
req = urllib.request.Request("http://127.0.0.1:8317/v1/models", headers={"Authorization": "Bearer " + key})
with urllib.request.urlopen(req, timeout=10) as response:
    data = json.load(response)
print("CATALOG_IDS=" + json.dumps(sorted(m["id"] for m in data["data"])))
PY
echo "== newest error dump markers (status/code only) =="
for f in $(ls -t "$D"/auth/logs/error-*.log 2>/dev/null | head -3); do
  echo "-- $(basename "$f") mtime=$(stat -c %y "$f" | cut -d. -f1)"
  grep -aoE '"(status|code|type)": *"?[A-Za-z0-9_.-]+"?' "$f" | sort | uniq -c | sort -rn | head -8
done
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300
Write-Host "SOLINPUT_PROBE_DONE"

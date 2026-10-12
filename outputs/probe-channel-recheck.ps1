#requires -Version 7
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
python3 - "$KEY" <<'PY'
import json, sys, time, urllib.request
key = sys.argv[1]
for model in ("gpt-6-astra", "gpt-6.1-sol-input"):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 64,
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
            print("RECHECK %s status=200 finish=%s content_ok=%s latency_ms=%.0f" % (
                model, choice.get("finish_reason"),
                str(choice.get("message", {}).get("content")).strip() == "OK",
                (time.monotonic() - started) * 1000))
    except urllib.error.HTTPError as error:
        print("RECHECK %s status=%d latency_ms=%.0f body_head=%s" % (
            model, error.code, (time.monotonic() - started) * 1000,
            error.read()[:120].decode(errors="replace")))
PY
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300
Write-Host "CHANNEL_RECHECK_DONE"

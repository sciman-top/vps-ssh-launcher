#requires -Version 7
# 2026-10-12 BWG decisive ai.input.im lane test: call the channel DIRECTLY with
# the configured key (extracted server-side, never printed) bypassing CPA.
# Direct rejection => channel-side account/key block; direct 200 => CPA v8.0.24
# request-shape regression and the canary must be rolled back.
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
KEY=$(python3 - <<'PYK'
import yaml
config = yaml.safe_load(open("/opt/cliproxyapi/config.yaml"))
for entry in config["api-keys"]["openai-compatibility"]:
    if entry.get("name") == "ai.input.im" or "ai.input.im" in str(entry.get("base-url", "")):
        keys = entry.get("keys") or []
        if keys:
            item = keys[0]
            print(item if isinstance(item, str) else item.get("api-key", ""))
            break
PYK
)
if [ -z "$KEY" ]; then echo "KEY_EXTRACT=failed"; exit 1; fi
echo "KEY_EXTRACT=ok len=${#KEY}"
for MODEL in gpt-6-astra gpt-6.1-sol-input; do
  CODE=$(curl -s -o /tmp/.direct-probe-body -w "%{http_code}" --max-time 60 \
    -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
    -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: OK\"}],\"max_tokens\":16}" \
    https://ai.input.im/v1/chat/completions)
  echo "DIRECT_$MODEL http=$CODE body_head=$(head -c 160 /tmp/.direct-probe-body)"
  rm -f /tmp/.direct-probe-body
done
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300
Write-Host "DIRECT_PROBE_DONE"

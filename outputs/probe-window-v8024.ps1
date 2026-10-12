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
echo "== container =="
docker inspect cli-proxy-api --format '{{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}}'
echo "== rollback set retained =="
docker images --format '{{.ID}} {{.Repository}}:{{.Tag}}' | grep -a "v8.0.23" || echo V8023_IMAGE_MISSING
echo "== panic/fatal since upgrade =="
docker logs --since 35m cli-proxy-api 2>&1 | grep -aciE "panic|fatal error" || echo 0
echo "== catalog now (astra recovery?) =="
KEY=$(python3 -c "import yaml;print(yaml.safe_load(open('$D/config.yaml'))['access']['api-keys'][0])")
python3 - "$KEY" <<'PY'
import json, sys, urllib.request
req = urllib.request.Request("http://127.0.0.1:8317/v1/models", headers={"Authorization": "Bearer " + sys.argv[1]})
with urllib.request.urlopen(req, timeout=10) as response:
    print("CATALOG_IDS=" + json.dumps(sorted(m["id"] for m in json.load(response)["data"])))
PY
echo "== admission journal since upgrade (aggregates) =="
journalctl -u cpa-admission.service --since "2026-10-12 04:04:57 UTC" --no-pager 2>/dev/null | grep -aoE "capacity=true|lane_reject|model_cooldown" | sort | uniq -c || echo none
echo "== gateway log since upgrade: status histogram =="
docker logs --since 35m cli-proxy-api 2>&1 | grep -aoE 'GIN.*\| ([0-9]{3}) \|' | grep -oE '[0-9]{3}' | sort | uniq -c || echo none
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300
Write-Host "WINDOW_PROBE_DONE"

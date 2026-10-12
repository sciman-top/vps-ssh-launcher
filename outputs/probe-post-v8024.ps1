#requires -Version 7
# 2026-10-12 BWG CPA v8.0.24 post-upgrade probes: quality canary (OAuth lane
# exempt by default), GLM-lane cache canary (double sample), and container log
# hygiene. Read-only with respect to configuration; consumes the documented
# canary probe budget only.
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
echo "== container since upgrade =="
docker inspect cli-proxy-api --format '{{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}}'
echo "== container log hygiene (panic/fatal since start) =="
docker logs --since 30m cli-proxy-api 2>&1 | grep -ciE "panic|fatal error" || true
echo "== quality-canary (oauth exempt by default) =="
python3 "$D/cpa-health.py" quality-canary
echo "== cache-canary glm-5.3-flash =="
python3 "$D/cpa-health.py" cache-canary
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote -IdleTimeoutSeconds 240 -HardTimeoutSeconds 900
Write-Host "POST_PROBES_V8024=DONE"

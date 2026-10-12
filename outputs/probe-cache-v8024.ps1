#requires -Version 7
# 2026-10-12 BWG CPA v8.0.24 remaining post-probes: GLM-lane cache canary
# (double sample; zhipu-plan lane is independent of the ai.input.im channel
# incident) and the OAuth-lane controlled replay through the real public entry.
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
echo "== cache-canary glm-5.3-flash (2 samples) =="
python3 "$D/cpa-health.py" cache-canary
echo "== admission service =="
systemctl is-active cpa-admission.service
echo "== container status =="
docker inspect cli-proxy-api --format '{{.State.Status}} restarts={{.RestartCount}}'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote -IdleTimeoutSeconds 180 -HardTimeoutSeconds 600
Write-Host "CACHE_CANARY_DONE"

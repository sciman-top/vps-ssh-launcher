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
echo "== /root cpa entries =="
ls -la /root/ 2>/dev/null | grep -E "cpa-adm|modelstreak|modelscope" || echo "none"
echo "== stage dirs =="
ls -lad /root/.cpa-adm-stage-* 2>/dev/null || echo "none"
echo "== policy semantic log =="
ls -la /tmp/cpa-policy-modelstreak.log /tmp/cpa-policy-modelscope.log 2>/dev/null || echo "none"
tail -3 /tmp/cpa-policy-modelstreak.log 2>/dev/null || true
echo "== admission service journal last 20 min =="
journalctl -u cpa-admission --since '20 minutes ago' --no-pager 2>/dev/null | grep -E "systemd|Started|Stopping|Main PID" | tail -n 10
echo "== who restarted: sudo/journal scope =="
journalctl --since '20 minutes ago' --no-pager 2>/dev/null | grep -E "cpa-admission.service" | grep -vE "python3" | tail -n 12
echo "== maintenance lock holders recently =="
journalctl --since '20 minutes ago' --no-pager 2>/dev/null | grep -iE "flock|maintenance.lock" | tail -n 5 || true
echo "== other ssh sessions =="
who || true
last -20 2>/dev/null | head -12 || true
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote
Write-Host "FORENSICS_PROBE=DONE"

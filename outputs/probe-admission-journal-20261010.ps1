#requires -Version 7
# Read-only check whether the model-scoped capacity path has actually fired in
# production since it was deployed. `model_cooldown` and `scope=model` cannot
# appear in the previous generation's output, so any hit proves post-deploy use.
param(
  [string]$Profile = "bwg",
  [int]$IdleTimeoutSeconds = 90,
  [int]$HardTimeoutSeconds = 240
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$remote = @'
set -Eeuo pipefail
journalctl -u cpa-admission --since '24 hours ago' -o cat > /tmp/cpa-adm-journal.txt 2>/dev/null || true
echo "JOURNAL_LINES=$(wc -l < /tmp/cpa-adm-journal.txt)"
echo "MODEL_COOLDOWN_REJECTS=$(grep -c 'reason=model_cooldown' /tmp/cpa-adm-journal.txt || true)"
echo "MODEL_PROBES=$(grep -c 'scope=model' /tmp/cpa-adm-journal.txt || true)"
echo "LANE_COOLDOWN_REJECTS=$(grep -c 'reason=cooldown' /tmp/cpa-adm-journal.txt || true)"
echo "UPSTREAM_RESULTS=$(grep -c 'upstream_result' /tmp/cpa-adm-journal.txt || true)"
echo "CAPACITY_TRUE=$(grep -c 'capacity=true' /tmp/cpa-adm-journal.txt || true)"
echo "== first/last model-scope lines =="
grep -E 'reason=model_cooldown|scope=model' /tmp/cpa-adm-journal.txt | head -n 3 || echo NONE
grep -E 'reason=model_cooldown|scope=model' /tmp/cpa-adm-journal.txt | tail -n 3 || echo NONE
echo "== service generation =="
systemctl show -p MainPID --value cpa-admission.service
sha256sum /opt/cliproxyapi/cpa-admission.py
echo "PIN=$(cat /etc/vps-ssh-launcher/cpa-admission.sha256)"
echo "PIN_MATCH=$( [ "$(sha256sum /opt/cliproxyapi/cpa-admission.py | awk '{print $1}')" = "$(cat /etc/vps-ssh-launcher/cpa-admission.sha256)" ] && echo yes || echo no )"
rm -f /tmp/cpa-adm-journal.txt
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $remote `
  -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
Write-Host "JOURNAL_PROBE=DONE"

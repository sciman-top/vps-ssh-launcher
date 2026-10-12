#requires -Version 7
# 2026-10-12 BWG CPA v8.0.24 canary pre-flight: read-only. Confirms the
# production pin, parallel-session residue (backups/pending/lock), disk headroom
# and the VPS-direct v8.0.24 digest (second source; local proxy fetch is first).
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
echo "== compose pin =="
python3 -c "import yaml;print(yaml.safe_load(open('/opt/cliproxyapi/compose.yml'))['services']['cli-proxy-api']['image'])"
echo "== container =="
docker inspect cli-proxy-api --format '{{.State.Status}} pid={{.State.Pid}} restarts={{.RestartCount}} started={{.State.StartedAt}}'
echo "== v8024 digest via registry (VPS direct) =="
TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:eceasy/cli-proxy-api:pull" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -D - -o /dev/null -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json" "https://registry-1.docker.io/v2/eceasy/cli-proxy-api/manifests/v8.0.24" | grep -iE 'docker-content-digest|^HTTP/'
echo "== v8024 digest via hub api =="
curl -s "https://hub.docker.com/v2/repositories/eceasy/cli-proxy-api/tags/v8.0.24" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('name'),d.get('digest'),d.get('last_updated'))"
echo "== recent backups =="
ls -1t "$D/backups/" | head -6
echo "== update status =="
cat /var/lib/vps-ssh-launcher/maintenance-status/cpa-update.status 2>/dev/null || echo no-status
if [ -f /var/lib/vps-ssh-launcher/maintenance-status/cpa-update.pending ]; then cat /var/lib/vps-ssh-launcher/maintenance-status/cpa-update.pending; else echo no-pending; fi
echo "== admission pin =="
awk 'NF {print $1; exit}' /etc/vps-ssh-launcher/cpa-admission.sha256
sha256sum "$D/cpa-admission.py" | cut -d' ' -f1
systemctl is-active cpa-admission.service
echo "== disk =="
df -Pk "$D" | tail -1
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote
Write-Host "PREFLIGHT_PROBE_V8024=DONE"

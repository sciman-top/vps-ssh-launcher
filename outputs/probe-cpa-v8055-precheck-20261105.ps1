#requires -Version 7
# Read-only precheck probe for the bwg CPA v8.0.55 host-only swap.
# No file, container, credential, or lock state is changed by this script.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
echo "PROBE_START $(date -u +%FT%TZ)"
echo "--- container ---"
docker inspect --format 'image={{.Image}} started={{.State.StartedAt}} restarts={{.RestartCount}} status={{.State.Status}}' cli-proxy-api
docker logs cli-proxy-api 2>&1 | grep -m1 'CLIProxyAPI Version:' || echo 'VERSION_LINE_MISSING'
grep -F 'image:' /opt/cliproxyapi/compose.yml
echo "--- files ---"
sha256sum /opt/cliproxyapi/plugins/workbuddy.so /opt/cliproxyapi/config.yaml
grep -c 'flash-sg' /opt/cliproxyapi/cpa-guardrail-doctor.sh /opt/cliproxyapi/cpa-guardrail-apply.sh /opt/cliproxyapi/cpa-admission.json 2>/dev/null || echo 'flash-sg absent somewhere'
echo "--- admission state ---"
for f in chatgpt-risk.json workbuddy-risk.json antigravity-risk.json; do
  python3 -c "import json;d=json.load(open('/var/lib/cpa-admission/$f'));print('$f hold_reason=%s' % d.get('hold_reason'))" || echo "$f READ_FAILED"
done
echo "--- health ---"
python3 /opt/cliproxyapi/cpa-health.py readiness && echo READINESS_RC=0 || echo "READINESS_RC=$?"
echo "--- recent traffic (10m docker log request lines) ---"
docker logs --since 10m cli-proxy-api 2>&1 | grep -ci 'request' || echo 0
echo "--- disk ---"
df -Pk /opt/cliproxyapi | awk 'NR==2{print "free_kib="$4}'
echo "--- updater last status ---"
cat /var/lib/vps-ssh-launcher/maintenance-status/cpa-update.status 2>/dev/null || echo 'no status file'
echo "PROBE_END $(date -u +%FT%TZ)"
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300

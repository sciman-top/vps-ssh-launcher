#requires -Version 7
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
echo "=== time ==="; date -u '+%Y-%m-%dT%H:%M:%SZ'
echo "=== compose pin ==="; grep -o 'image: eceasy/cli-proxy-api:[^ ]*' /opt/cliproxyapi/compose.yml
echo "=== container ==="; docker inspect --format '{{.Config.Image}} started={{.State.StartedAt}} restarts={{.RestartCount}} status={{.State.Status}}' cli-proxy-api
echo "=== plugin sha ==="; sha256sum /opt/cliproxyapi/plugins/workbuddy.so
echo "=== plugin registered (tail) ==="; docker logs cli-proxy-api 2>&1 | grep 'plugin registered plugin_id=workbuddy' | tail -2
echo "=== holds ==="
for f in chatgpt workbuddy antigravity claude; do
  printf '%s: ' "$f"
  python3 -c "import json;d=json.load(open('/var/lib/cpa-admission/$f-risk.json'));print('hold=%s not_before=%s'%(d.get('hold_reason'),d.get('not_before')))"
done
echo "=== admission live sha ==="; sha256sum /opt/cliproxyapi/cpa-admission.py
echo "=== workbuddy recent model_cooldown ==="
journalctl -u cpa-admission --since '-6h' -o cat 2>/dev/null | grep 'reason=model_cooldown' | tail -3 || true
echo "=== healthz head ==="; curl -s http://127.0.0.1:8318/healthz | head -c 300; echo
echo "=== disk free KiB ==="; df -Pk /opt/cliproxyapi | awk 'NR==2{print $4}'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300

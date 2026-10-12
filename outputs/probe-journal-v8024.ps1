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
echo "== admission journal for the replay request =="
journalctl -u cpa-admission.service --since "2026-10-12 04:10:00 UTC" --no-pager 2>/dev/null | grep -a "0f14bcc4eee14d7fbb7f8edfe6323bbf" | tail -6
echo "== container log for the replay request =="
docker logs --since 30m cli-proxy-api 2>&1 | grep -a "0f14bcc4" | tail -8
echo "== container log non-200 since upgrade =="
docker logs --since 40m cli-proxy-api 2>&1 | grep -aE "\| (4[0-9]{2}|5[0-9]{2}) \|" | tail -10
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote
Write-Host "JOURNAL_PROBE_DONE"

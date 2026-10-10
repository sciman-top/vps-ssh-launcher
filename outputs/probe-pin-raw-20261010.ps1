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
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
echo "== raw bytes =="
od -c "$PIN" | head -5
echo "== awk output od =="
awk 'NF {print $1; exit}' "$PIN" | od -c | head -3
echo "== awk length =="
awk 'NF {print length($1); exit}' "$PIN"
echo "== expected length =="
echo -n "183fdd1f68d7a2b8de4fc5dd478ea81c7965dcb49d807e995e54c6bb682fd2c6" | wc -c
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote
Write-Host "PIN_RAW_PROBE=DONE"

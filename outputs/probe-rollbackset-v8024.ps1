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
echo "== v8.0.23 image by digest =="
docker image inspect "eceasy/cli-proxy-api@sha256:fc250aafe345d178fe7fb1b2966efb2ca204e69ce0cf73eed661209f33e24b70" --format 'PRESENT id={{.Id}} size={{.Size}}' 2>/dev/null || echo ABSENT_BY_DIGEST
echo "== all eceasy images incl dangling =="
docker images -a --format '{{.ID}} {{.Repository}}:{{.Tag}}' | grep -a eceasy || echo none
echo "== backup compose rollback reference =="
grep -a "^ *image:" /opt/cliproxyapi/backups/20261012T040455.886900154Z-from-v8.0.23-canary-v8024/compose.yml
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $remote
Write-Host "ROLLBACKSET_DONE"

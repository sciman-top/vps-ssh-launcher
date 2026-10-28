#requires -Version 7
# Driver for the authorized bwg v8.0.46 + WorkBuddy 0.9.81 joint canary,
# 2026-10-28. One phase per invocation; every phase is idempotent-refusing
# (remote scripts test ! -e their scratch dirs / pin exact shas).
# Phases:
#   build  - isolated on-box plugin build (upstream v0.8.89 tarball + local
#            patches) inside golang:1.26-bookworm, 1 CPU / 768 MiB
#   stage  - pull pinned v8.0.46 image, extract binary + new .so into the
#            credential-free fixture dir
#   accept - repository acceptance fixture in an isolated mount+net namespace
#   load   - isolated plugin-load of 0.9.81 on the new binary
#   deploy - the joint production transaction (drain + swap + readiness)
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('build', 'stage', 'accept', 'load', 'deploy')]
  [string]$Phase
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$idle = 300
$hard = 1800
switch ($Phase) {
  'build' {
    $command = @'
set -Eeuo pipefail
umask 077
V=/root/cpa-workbuddy-0981-20261028
test ! -e "$V"
mkdir -m 700 "$V"
curl --fail --location --max-time 120 https://api.github.com/repos/mmqz/cpa-multi-plugins/tarball/69a472436426a8b1f67d146fdc428efd4b917932 -o "$V/source.tar.gz"
test "$(sha256sum "$V/source.tar.gz" | cut -d ' ' -f 1)" = 2f2f2c9d7a32fb9d51adda353fce8a971bb637565cd4b3dcc7a3c7d241077ba0
sha256sum "$V/source.tar.gz"
tar xzf "$V/source.tar.gz" -C "$V" --strip-components=1
cd "$V"
'@
    foreach ($name in @('selected-account','account-failover')) {
      $patchVersion = '0.8.89'; $patch = Get-Content -LiteralPath (Join-Path $repoRoot "scripts/patches/workbuddy-v$patchVersion-$name.patch") -Raw
      $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($patch.Replace("`r`n","`n")))
      $command += "`nprintf '%s' '$encoded' | base64 -d > '$name.patch'`ngit apply --recount --check '$name.patch'`ngit apply --recount '$name.patch'`n"
    }
    $command += @'
test -f plugins/workbuddy/stream_termination_test.go
grep -q 0.9.81 plugins/workbuddy/VERSION
grep -q 'MIT License' LICENSE
docker run --rm --cpus=1 --memory=768m -v "$V":/src -w /src/plugins/workbuddy golang:1.26-bookworm bash -c 'go vet ./... && go test -count=1 ./... && CGO_ENABLED=1 go build -buildmode=c-shared -o workbuddy.so . && sha256sum workbuddy.so'
'@
  }
  'stage' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'stage-v8046-20261028.sh') -Raw
    $idle = 900; $hard = 1800
  }
  'accept' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'accept-v8046-20261028.sh') -Raw
    $idle = 600; $hard = 1800
  }
  'load' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'plugin-load-v8046-20261028.sh') -Raw
    $idle = 120; $hard = 600
  }
  'deploy' {
    $command = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deploy-cpa-v8046-plugin-0981-20261028.sh') -Raw
    if ($command -match '__PLUGIN_NEW_SHA__') {
      throw 'deploy script still carries the __PLUGIN_NEW_SHA__ placeholder; fill it from the build phase output first'
    }
    $idle = 900; $hard = 2700
  }
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds $idle -HardTimeoutSeconds $hard

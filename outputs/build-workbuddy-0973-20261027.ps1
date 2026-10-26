#requires -Version 7
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
V=/root/cpa-workbuddy-0973-20261027
test -f "$V/source.tar.gz"
mkdir -p "$V"
curl --fail --location --max-time 120 https://api.github.com/repos/mmqz/cpa-multi-plugins/tarball/e9524a0486517d80a9cabd0869c78a417f3ba291 -o "$V/source.tar.gz"
test "$(sha256sum "$V/source.tar.gz" | cut -d ' ' -f 1)" = 2033a87973731f8f6913ed6bbb09fa9479502057d40bc533f75b644353e71eb9
sha256sum "$V/source.tar.gz"
tar xzf "$V/source.tar.gz" -C "$V" --strip-components=1
cd "$V"
'@
foreach ($name in @('selected-account','account-failover')) {
  $patchVersion = if ($name -eq 'selected-account') { '0.8.86' } else { '0.8.84' }; $patch = Get-Content -LiteralPath (Join-Path $repoRoot "scripts/patches/workbuddy-v$patchVersion-$name.patch") -Raw
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($patch.Replace("`r`n","`n")))
  $command += "`nprintf '%s' '$encoded' | base64 -d > '$name.patch'`ngit apply --recount --check '$name.patch'`ngit apply --recount '$name.patch'`n"
}
$command += @'
test -f plugins/workbuddy/stream_termination_test.go
grep -q 0.9.73 plugins/workbuddy/VERSION
grep -q 'MIT License' LICENSE
docker run --rm --cpus=1 --memory=768m -v "$V":/src -w /src/plugins/workbuddy golang:1.26-bookworm bash -c 'go vet ./... && go test -count=1 ./... && CGO_ENABLED=1 go build -buildmode=c-shared -o workbuddy.so . && sha256sum workbuddy.so'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

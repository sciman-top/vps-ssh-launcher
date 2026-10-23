#requires -Version 7
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
V=/root/cpa-workbuddy-0966-20261024
test ! -e "$V"
mkdir -m 700 "$V"
curl --fail --location --max-time 120 https://api.github.com/repos/mmqz/cpa-multi-plugins/tarball/0ac024072c271a0a5d2d74ed1ba127417a777826 -o "$V/source.tar.gz"
test "$(sha256sum "$V/source.tar.gz" | cut -d ' ' -f 1)" = 09612bcce37d45bbc7cbc0e5a98585abb33654a49bc96b502047601f7cb9eab3
sha256sum "$V/source.tar.gz"
tar xzf "$V/source.tar.gz" -C "$V" --strip-components=1
cd "$V"
'@
foreach ($name in @('selected-account','account-failover')) {
  $patch = Get-Content -LiteralPath (Join-Path $repoRoot "scripts/patches/workbuddy-v0.8.68-$name.patch") -Raw
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($patch.Replace("`r`n","`n")))
  $command += "`nprintf '%s' '$encoded' | base64 -d > '$name.patch'`ngit apply --recount --check '$name.patch'`ngit apply --recount '$name.patch'`n"
}
$command += @'
test -f plugins/workbuddy/stream_termination_test.go
grep -q 0.9.66 plugins/workbuddy/VERSION
grep -q 'MIT License' LICENSE
docker run --rm --cpus=1 --memory=768m -v "$V":/src -w /src/plugins/workbuddy golang:1.26-bookworm bash -c 'go vet ./... && go test -count=1 ./... && CGO_ENABLED=1 go build -buildmode=c-shared -o workbuddy.so . && sha256sum workbuddy.so'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

#requires -Version 7
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = @'
set -Eeuo pipefail
umask 077
V=/root/cpa-workbuddy-0977-20261027
test ! -e "$V"
mkdir -m 700 "$V"
curl --fail --location --max-time 120 https://api.github.com/repos/mmqz/cpa-multi-plugins/tarball/8c0af5dd24f4fd8e870e990080086d9f26863310 -o "$V/source.tar.gz"
test "$(sha256sum "$V/source.tar.gz" | cut -d ' ' -f 1)" = e316e2b512f69f9a181defebf3d48fde06c21eb70a1d1fad7df2c3446a19ab77
sha256sum "$V/source.tar.gz"
tar xzf "$V/source.tar.gz" -C "$V" --strip-components=1
cd "$V"
'@
foreach ($name in @('selected-account','account-failover')) {
  $patchVersion = if ($name -eq 'selected-account') { '0.8.87' } else { '0.8.84' }; $patch = Get-Content -LiteralPath (Join-Path $repoRoot "scripts/patches/workbuddy-v$patchVersion-$name.patch") -Raw
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($patch.Replace("`r`n","`n")))
  $command += "`nprintf '%s' '$encoded' | base64 -d > '$name.patch'`ngit apply --recount --check '$name.patch'`ngit apply --recount '$name.patch'`n"
}
$command += @'
test -f plugins/workbuddy/stream_termination_test.go
grep -q 0.9.77 plugins/workbuddy/VERSION
grep -q 'MIT License' LICENSE
docker run --rm --cpus=1 --memory=768m -v "$V":/src -w /src/plugins/workbuddy golang:1.26-bookworm bash -c 'go vet ./... && go test -count=1 ./... && CGO_ENABLED=1 go build -buildmode=c-shared -o workbuddy.so . && sha256sum workbuddy.so'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile bwg -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

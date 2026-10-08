#requires -Version 7
# 2026-10-08 BWG CPA v8.0.20 fixture acceptance: upload repo acceptance
# scripts (LF-normalized, sha-verified), pull the digest-pinned image, then
# run the binary-level acceptance in a disposable mount+net namespace.
# Production processes, mounts and network are untouched.
param(
  [string]$Profile = "bwg",
  [Parameter(Mandatory = $true)][string]$ImageRef,
  [string]$Tag = "v8020"
)
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

function Get-LfSha256AndB64([string]$Path) {
  $text = (Get-Content -LiteralPath $Path -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $hash = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)))).Replace("-", "").ToLowerInvariant()
  }
  finally {
    $sha.Dispose()
  }
  return @{
    Sha = $hash
    B64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text))
  }
}

$acc = Get-LfSha256AndB64 (Join-Path $repoRoot "scripts\remote\cpa-acceptance.py")
$upd = Get-LfSha256AndB64 (Join-Path $repoRoot "scripts\remote\cpa-update-acceptance.py")

function Invoke-Remote([string]$Command, [int]$IdleTimeoutSeconds = 0, [int]$HardTimeoutSeconds = 0) {
  Write-Host ("-- remote: " + $Command.Substring(0, [Math]::Min(90, $Command.Length)) + " ...")
  Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile `
    -Command $Command -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
}

function Upload-LfFile([string]$Name, [string]$B64, [string]$Sha) {
  $remotePath = "/tmp/cpa-$Name-$Tag.py"
  $tmpB64 = "/tmp/.upload-$Name-$Tag.b64"
  $parts = @("umask 077; : > '$tmpB64'")
  for ($offset = 0; $offset -lt $B64.Length; $offset += 6000) {
    $len = [Math]::Min(6000, $B64.Length - $offset)
    $parts += "printf %s '$($B64.Substring($offset, $len))' >> '$tmpB64'"
  }
  $verify = @"
base64 -d '$tmpB64' > '$remotePath'
rm -f '$tmpB64'
chmod 600 '$remotePath'
GOT=`$(sha256sum '$remotePath' | cut -d' ' -f1)
echo "`$GOT"
[ "`$GOT" = '$Sha' ] || { echo 'REFUSE upload sha mismatch'; exit 2; }
python3 -m py_compile '$remotePath' && echo 'UPLOAD_COMPILE=ok'
"@
  $parts += $verify
  Invoke-Remote ($parts -join "`n")
  Write-Host "== uploaded $remotePath sha=$Sha"
}

Write-Host "== step 1: upload acceptance scripts =="
Upload-LfFile "acceptance" $acc.B64 $acc.Sha
Upload-LfFile "update-acceptance" $upd.B64 $upd.Sha

Write-Host "== step 2: pull image =="
Invoke-Remote "docker pull '$ImageRef' && docker image inspect '$ImageRef' >/dev/null && echo 'PULL_OK'" 120 600

Write-Host "== step 3: fixture acceptance =="
$fixture = @"
set -Eeuo pipefail
FIX=`$(mktemp -d /tmp/cpa-accept-$Tag.XXXXXX)
echo "FIXTURE_ROOT=`$FIX"
cid=`$(docker create '$ImageRef')
docker cp "`$cid":/CLIProxyAPI/CLIProxyAPI "`$FIX/CLIProxyAPI"
docker rm "`$cid" >/dev/null
chmod 700 "`$FIX/CLIProxyAPI"
echo "== fixture binary sha (prefix)"
sha256sum "`$FIX/CLIProxyAPI" | cut -c1-16
D=/opt/cliproxyapi
cp "`$D"/cpa-health.py "`$D"/cpa_policy.py "`$D"/cpa_provider_routes.json "`$D"/auto-update.sh "`$FIX/"
mv /tmp/cpa-acceptance-$Tag.py "`$FIX/cpa-acceptance.py"
mv /tmp/cpa-update-acceptance-$Tag.py "`$FIX/cpa-update-acceptance.py"
chmod 700 "`$FIX"/*
touch "`$FIX/FIXTURE_ONLY"
set +e
unshare --mount --net --fork bash -c "mount --bind '`$FIX' /opt/cliproxyapi && ip link set lo up && exec python3 /opt/cliproxyapi/cpa-acceptance.py"
rc=`$?
set -e
echo "ACCEPTANCE_EXIT=`$rc"
echo "== leftover fixture processes (absolute /opt/cliproxyapi path = leak)"
pgrep -af 'CLIProxyAPI' || true
if pgrep -f '/opt/cliproxyapi/CLIProxyAPI' >/dev/null; then
  echo "CLEANUP_DEFERRED fixture process still alive root=`$FIX"
else
  rm -rf "`$FIX"
  echo "CLEANUP_OK root=`$FIX"
fi
"@
Invoke-Remote $fixture 180 900

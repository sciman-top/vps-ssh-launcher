#requires -Version 7
# Narrow, authorized projection of the reviewed read-only CPA diagnostic tool.
# It does not touch CPA config, credentials, admission, Nginx, or service state.
param(
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLiveSha256 = "0a38749a1a20c0aa34490819f1238ec4e3f12fa9f16463adff486e79fba4c396"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$sourcePath = Join-Path $repoRoot "scripts/remote/cpa-health.py"
$sourceBytes = [IO.File]::ReadAllBytes($sourcePath)
$text = [Text.Encoding]::UTF8.GetString($sourceBytes).Replace("`r`n", "`n").Replace("`r", "`n")
$bytes = [Text.Encoding]::UTF8.GetBytes($text)
$wantedSha256 = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($bytes)
).ToLowerInvariant()
if ($wantedSha256 -ne "18a948d8f97bba8b227338a107bc527f1a8f76eba3a08d7982d5618436363e7e") {
  throw "Reviewed cpa-health.py hash changed; refusing projection."
}
$payload = [Convert]::ToBase64String($bytes)

$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
TARGET=/opt/cliproxyapi/cpa-health.py
OLD_SHA256=__OLD_SHA256__
NEW_SHA256=__NEW_SHA256__
STAGED=/opt/cliproxyapi/.cpa-health.py.new-20261101.$$
NEXT=/opt/cliproxyapi/.cpa-health.py.next-20261101.$$
trap 'rm -f -- "$STAGED" "$NEXT"' EXIT

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'REFUSE maintenance_busy'; exit 75; }
printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
chmod 644 "$STAGED"
chown root:root "$STAGED"
[ "$(sha256sum "$TARGET" | awk '{print $1}')" = "$OLD_SHA256" ] || {
  echo 'REFUSE live_baseline_drift'
  exit 20
}
[ "$(sha256sum "$STAGED" | awk '{print $1}')" = "$NEW_SHA256" ] || {
  echo 'REFUSE staged_sha_mismatch'
  exit 20
}
python3 -m py_compile "$STAGED"
install -m 644 -o root -g root "$STAGED" "$NEXT"
mv -f "$NEXT" "$TARGET"
python3 "$TARGET" readiness
echo "CPA_HEALTH_PROJECTED sha256=$NEW_SHA256"
'@

$remote = $remote.
  Replace("__OLD_SHA256__", $ExpectedLiveSha256).
  Replace("__NEW_SHA256__", $wantedSha256).
  Replace("__PAYLOAD__", $payload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled cpa-health projection placeholder."
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 60 -HardTimeoutSeconds 180 `
  -RemoteTempPrefix "cpa-health-20261101"

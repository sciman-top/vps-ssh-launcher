#requires -Version 7
# Read-only trace of one live gateway request (admission journal + CPA container
# log). Key-shaped substrings are redacted before printing, so no credential
# material reaches the receipt. Frame buffering and admission wait are what this
# separates from upstream generation time.
param(
  [string]$Profile = "bwg",
  [string]$RequestId = "f52818a5d97f419ebcf9795f6dd12ca9",
  [int]$IdleTimeoutSeconds = 90,
  [int]$HardTimeoutSeconds = 240
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }
if ($RequestId -notmatch '^[0-9a-f]{32}$') { throw "RequestId must be 32 hex characters." }
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$remote = @'
set -Eeuo pipefail
ID='__REQUEST_ID__'
redact() {
  sed -E 's/(sk-|cmk_|cpa_)[A-Za-z0-9_-]{8,}/<redacted>/g'
}
echo "== admission journal lines for the request =="
journalctl -u cpa-admission --since '24 hours ago' -o cat 2>/dev/null |
  grep -F "$ID" | redact || echo NO_ADMISSION_LINE
echo "== admission journal: any queueing around that time =="
journalctl -u cpa-admission --since '24 hours ago' -o cat 2>/dev/null |
  grep -c 'waited_ms=0' || true
echo "== cpa container log lines for the request =="
for container in $(docker ps --format '{{.Names}}'); do
  matches=$(docker logs --since 3h "$container" 2>&1 | grep -F "$ID" | redact || true)
  if [ -n "$matches" ]; then
    echo "CONTAINER=$container"
    printf '%s\n' "$matches" | head -n 8
  fi
done
echo "== cpa container identity =="
docker ps --format '{{.Names}} {{.Image}}' | redact
'@
$remote = $remote.Replace('__REQUEST_ID__', $RequestId)
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $remote `
  -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
Write-Host "REQUEST_TRACE=DONE"

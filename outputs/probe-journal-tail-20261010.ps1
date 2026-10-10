#requires -Version 7
# Read-only: last upstream_result / lane_probe / lane_reject lines with second
# resolution, to attribute the currently open lane cooldown. Fields are already
# redacted by admission (no request ids, no client text in these lines).
param(
  [string]$Profile = "bwg",
  [int]$IdleTimeoutSeconds = 90,
  [int]$HardTimeoutSeconds = 240
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "restricted to bwg" }
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$remote = @'
set -Eeuo pipefail
journalctl -u cpa-admission --since '6 hours ago' -o short-iso 2>/dev/null | grep -E 'lane_probe|lane_reject|upstream_result|upstream_error' | tail -n 40
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $remote `
  -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
Write-Host "JOURNAL_TAIL_PROBE=DONE"

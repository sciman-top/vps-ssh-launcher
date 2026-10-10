#requires -Version 7
# Controlled BWG admission replay for the current generation.  The shell
# fixture is derived from the reviewed model-scope replay, but answers a
# mixed account/model capacity marker with HTTP 503 so the pre-fix and fixed
# generations can be distinguished without touching live CPA or OAuth.
param(
  [string]$Profile = "bwg",
  [int]$IdleTimeoutSeconds = 420,
  [int]$HardTimeoutSeconds = 900
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") { throw "This acceptance is restricted to the bwg profile." }

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$body = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "accept-modelscope-20261010.sh")

# Compare the deployed 5273f59e generation with the immediately previous
# 1ae590d8 generation saved by its reviewed deployment transaction.
$backup = "/root/cpa-admission-modelstreak-backup-20261010T172705Z"
$body = [regex]::Replace($body, '(?m)^BACKUP=.*$', "BACKUP=$backup")
$oldMarker = @'
MARKER = b'{"error":{"type":"capacity_error","message":"Selected model is at capacity"}}'
'@.Trim()
$newMarker = @'
MARKER = b'{"error":{"type":"capacity_error","code":"server_is_overloaded","message":"server_is_overloaded: Selected model is at capacity"}}'
'@.Trim()
$body = $body.Replace($oldMarker, $newMarker)
$body = $body.Replace(
  'self.send_response(200)',
  'self.send_response(503 if capacity else 200)'
)
$body = $body.Replace(
  'if capacity and mode == "marker_retry":',
  'if capacity:'
)
$body = $body.Replace(
  'NEW_marker) echo "200 -|200 -|429 model_cooldown|200 -"',
  'NEW_marker) echo "503 -|429 cooldown|429 cooldown|429 cooldown"'
)
$body = $body.Replace(
  'NEW_marker_retry) echo "200 -|429 model_cooldown|429 model_cooldown|200 -"',
  'NEW_marker_retry) echo "503 -|429 cooldown|429 cooldown|429 cooldown"'
)
$body = $body.Replace(
  'OLD_marker) echo "200 -|200 -|200 -|200 -"',
  'OLD_marker) echo "503 -|429 model_cooldown|429 model_cooldown|200 -"'
)
$body = $body.Replace(
  'OLD_marker_retry) echo "200 -|429 cooldown|429 cooldown|429 cooldown"',
  'OLD_marker_retry) echo "503 -|429 model_cooldown|429 model_cooldown|200 -"'
)

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile $Profile -Command $body `
  -IdleTimeoutSeconds $IdleTimeoutSeconds -HardTimeoutSeconds $HardTimeoutSeconds
Write-Host "ACCEPT_CURRENT_MIXED=DONE"

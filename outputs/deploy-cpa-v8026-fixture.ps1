#requires -Version 7
# Runs the v8.0.26 disposable fixture acceptance only through the bwg profile.
# Step 1 uploads the acceptance scripts (base64 + sha check + py_compile);
# steps 2-3 run outputs/cpa-v8026-fixture.sh on the remote.
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

function Get-NormalizedText([string]$Path) {
  (Get-Content -LiteralPath $Path -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
}

function Send-NormalizedScript([string]$Path, [string]$Label) {
  $source = (Get-Content -LiteralPath $Path -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
  $command = "VPS_SSH_LAUNCHER_PROFILE='bwg'`n" + $source
  Write-Host "Invoking the $Label through the bwg profile."
  Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
    -Profile "bwg" -Command $command -IdleTimeoutSeconds 900 -HardTimeoutSeconds 2700
}

function Upload-AcceptanceScript([string]$Path, [string]$RemotePath) {
  $text = Get-NormalizedText $Path
  $bytes = [Text.Encoding]::UTF8.GetBytes($text)
  $expected = ([System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes) |
    ForEach-Object { $_.ToString("x2") }) -join ""
  $b64 = [Convert]::ToBase64String($bytes)
  $command = "umask 077; printf %s '$b64' | base64 -d > '$RemotePath' && " +
    "echo '$expected  $RemotePath' | sha256sum -c - --quiet && " +
    "python3 -m py_compile '$RemotePath' && echo UPLOAD_COMPILE=ok"
  Write-Host "Uploading $(Split-Path -Leaf $Path) as $RemotePath."
  Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
    -Profile "bwg" -Command $command -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300
}

Upload-AcceptanceScript (Join-Path $repoRoot "scripts\remote\cpa-acceptance.py") "/tmp/cpa-acceptance-v8026.py"
Upload-AcceptanceScript (Join-Path $repoRoot "scripts\remote\cpa-update-acceptance.py") "/tmp/cpa-update-acceptance-v8026.py"
Send-NormalizedScript (Join-Path $PSScriptRoot "cpa-v8026-fixture.sh") "v8.0.26 fixture acceptance"

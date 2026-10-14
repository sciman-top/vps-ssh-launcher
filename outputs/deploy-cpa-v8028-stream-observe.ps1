#requires -Version 7
# Runs the v8.0.28 streaming cadence observation on the bwg host (loopback
# only; reads the client key from the live config and never prints it).
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$scriptPath = Join-Path $PSScriptRoot "cpa-v8028-stream-observe.py"
$text = (Get-Content -LiteralPath $scriptPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$bytes = [Text.Encoding]::UTF8.GetBytes($text)
$expected = ([System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes) |
  ForEach-Object { $_.ToString("x2") }) -join ""
$b64 = [Convert]::ToBase64String($bytes)
$command = "umask 077; printf %s '$b64' | base64 -d > /tmp/cpa-v8028-stream-observe.py && " +
  "echo '$expected  /tmp/cpa-v8028-stream-observe.py' | sha256sum -c - --quiet && " +
  "python3 /tmp/cpa-v8028-stream-observe.py; rc=`$?; rm -f /tmp/cpa-v8028-stream-observe.py; exit `$rc"

Write-Host "Invoking the v8.0.28 streaming observation through the bwg profile."
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile "bwg" -Command $command -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900

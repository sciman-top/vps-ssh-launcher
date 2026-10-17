#requires -Version 7
param([switch]$Execute)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$transactionPath = Join-Path $PSScriptRoot 'deploy-cpa-oauth-risk-20261017.py'
$transaction = [IO.File]::ReadAllText($transactionPath).Replace("`r`n", "`n")
$option = if ($Execute) { '--execute' } else { '' }
$command = "python3 - $option <<'CPA_OAUTH_RISK_TRANSACTION'`n" + $transaction + "`nCPA_OAUTH_RISK_TRANSACTION`n"
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile 'bwg' -Command $command -IdleTimeoutSeconds 60 -HardTimeoutSeconds 180
if ($LASTEXITCODE -ne 0) { throw "OAuth risk transaction failed: $LASTEXITCODE" }

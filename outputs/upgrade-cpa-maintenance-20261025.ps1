#requires -Version 7
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'scripts/lib/project_environment.ps1')
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$command = Get-Content (Join-Path $PSScriptRoot 'upgrade-cpa-maintenance-20261025.sh') -Raw
foreach ($entry in @{
    '__CPA_HOST_SCRIPT_BASE64__' = 'deploy-cpa-v8042-canary-20261025.sh'
    '__CPA_PLUGIN_SCRIPT_BASE64__' = 'install-workbuddy-0969-20261025.sh'
}.GetEnumerator()) {
    $source = (Get-Content (Join-Path $PSScriptRoot $entry.Value) -Raw).Replace("`r`n", "`n")
    $command = $command.Replace($entry.Key, [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($source)))
}
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile bwg -Command $command -IdleTimeoutSeconds 400 -HardTimeoutSeconds 900

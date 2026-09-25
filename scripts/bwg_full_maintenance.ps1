#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [string]$TargetConfig,
  [ValidateSet("Observe", "RunNow")]
  [string]$Mode = "Observe",
  [string]$OutputDirectory,
  [switch]$RunIntegration
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

if (-not $RunIntegration) {
  throw "Fresh BWG maintenance runs require -RunIntegration."
}

Initialize-WindowsProcessEnvironment
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$launcherConfig = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $launcherConfig -PathType Leaf)) {
  throw "Launcher config not found: $launcherConfig"
}
if (-not $TargetConfig) {
  if (-not $env:APPDATA) {
    throw "APPDATA is unavailable; pass -TargetConfig explicitly."
  }
  $TargetConfig = Join-Path $env:APPDATA "vps-ssh-launcher\target.json"
}
$maintenanceConfig = if ($env:APPDATA) {
  Join-Path $env:APPDATA "vps-ssh-launcher\maintenance.toml"
} else {
  throw "APPDATA is unavailable; the BWG control-plane policy path cannot be resolved."
}
$TargetConfig = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($TargetConfig)
if (-not (Test-Path -LiteralPath $TargetConfig -PathType Leaf)) {
  throw "Target config not found: $TargetConfig"
}

$runsRoot = if ($OutputDirectory) {
  $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
} elseif ($env:LOCALAPPDATA) {
  Join-Path $env:LOCALAPPDATA "vps-ssh-launcher\full-maintenance-runs"
} else {
  Join-Path $repoRoot ".full-maintenance-runs"
}
New-Item -ItemType Directory -Path $runsRoot -Force | Out-Null
$runId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$logPath = Join-Path $runsRoot "$runId-bwg-$($Mode.ToLowerInvariant()).log"
$summaryPath = Join-Path $runsRoot "$runId-bwg-$($Mode.ToLowerInvariant()).json"
$script:steps = [System.Collections.Generic.List[object]]::new()
$script:failed = $false

function Write-RunLine {
  param([AllowEmptyString()][string]$Line)

  Add-Content -LiteralPath $logPath -Value $Line -Encoding utf8NoBOM
  Write-Output $Line
}

function Invoke-LocalStep {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Path,
    [string[]]$Arguments = @()
  )

  $started = [DateTime]::UtcNow
  Write-RunLine "STEP_START name=$Name"
  $output = @()
  $code = 0
  try {
    # Invoke through pwsh so an array of named arguments is parsed as script
    # parameters. Calling a .ps1 directly with array splatting treats the
    # strings as positional arguments on PowerShell 7.
    $output = @(& pwsh -NoProfile -ExecutionPolicy Bypass -File $Path @Arguments 2>&1)
    $code = [int]$LASTEXITCODE
  }
  catch {
    $output += $_.Exception.Message
    $code = 1
  }
  foreach ($line in $output) {
    Write-RunLine ([string]$line)
  }
  $status = if ($code -eq 0) { "PASS" } else { "FAIL" }
  Write-RunLine "STEP_END name=$Name status=$status exit=$code"
  $script:steps.Add([pscustomobject]@{
      name = $Name
      status = $status
      exit_code = $code
      started_at = $started.ToString("o")
      finished_at = [DateTime]::UtcNow.ToString("o")
  })
  if ($code -ne 0) {
    $script:failed = $true
    throw "Step $Name failed with exit code $code."
  }
}

function Invoke-RemoteStep {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Command,
    [int]$IdleTimeoutSeconds = 180,
    [int]$HardTimeoutSeconds = 360
  )

  $started = [DateTime]::UtcNow
  Write-RunLine "STEP_START name=$Name"
  $code = 0
  try {
    $sshTool = Join-Path $repoRoot "ssh_tool.py"
    $launcherArgs = @(
      "--config", $launcherConfig,
      "--profile", "bwg",
      "--strict-host-key-checking",
      "run",
      "--command-timeout", "$IdleTimeoutSeconds",
      "--command-hard-timeout", "$HardTimeoutSeconds",
      "--command", $Command
    )
    $output = @(& $script:Python.Exe @($script:Python.Args + @($sshTool) + $launcherArgs) 2>&1)
    Write-RunLine "REMOTE_OUTPUT_LINES=$($output.Count)"
    foreach ($line in $output) {
      Write-RunLine ([string]$line)
    }
    $code = [int]$LASTEXITCODE
  }
  catch {
    Write-RunLine $_.Exception.Message
    $code = 1
  }
  $status = if ($code -eq 0) { "PASS" } else { "FAIL" }
  Write-RunLine "STEP_END name=$Name status=$status exit=$code"
  $script:steps.Add([pscustomobject]@{
      name = $Name
      status = $status
      exit_code = $code
      started_at = $started.ToString("o")
      finished_at = [DateTime]::UtcNow.ToString("o")
  })
  if ($code -ne 0) {
    $script:failed = $true
    throw "Step $Name failed with exit code $code."
  }
}

$oldIntegration = [Environment]::GetEnvironmentVariable(
  "VPS_SSH_LAUNCHER_RUN_INTEGRATION",
  "Process"
)
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
$script:exitCode = 0
try {
  $maintenanceScript = Join-Path $repoRoot "scripts\vps_maintenance.ps1"
  $cpaScript = Join-Path $repoRoot "scripts\cpa_bwg_guardrails.ps1"
  $v2rayScript = Join-Path $repoRoot "scripts\v2ray_agent_script_update_cron.ps1"
  $renewTlsScript = Join-Path $repoRoot "scripts\v2ray_agent_renewtls_cron.ps1"
  $vasmaScript = Join-Path $repoRoot "scripts\vasma_kernel_update_cron.ps1"
  $systemScript = Join-Path $repoRoot "scripts\system_maintenance_cron.ps1"
  $googleScript = Join-Path $repoRoot "scripts\google_ipv4_routing.ps1"

  $maintenanceArgs = @(
    "-Profile", "bwg",
    "-RunIntegration",
    "-Config", $maintenanceConfig,
    "-TargetConfig", $TargetConfig
  )
  Invoke-LocalStep -Name "control-plane-plan" -Path $maintenanceScript -Arguments $maintenanceArgs
  Invoke-LocalStep -Name "cpa-doctor-pre" -Path $cpaScript -Arguments @("-Profile", "bwg")
  Invoke-LocalStep -Name "v2ray-agent-read" -Path $v2rayScript -Arguments @("-Profile", "bwg")
  Invoke-LocalStep -Name "renewtls-read" -Path $renewTlsScript -Arguments @("-Profile", "bwg")
  Invoke-LocalStep -Name "vasma-xray-read" -Path $vasmaScript -Arguments @("-Profile", "bwg", "-Kernel", "xray")
  Invoke-LocalStep -Name "system-maintenance-read" -Path $systemScript -Arguments @("-Profile", "bwg")
  Invoke-LocalStep -Name "google-ipv4-read" -Path $googleScript -Arguments @("-Profile", "bwg")

  if ($Mode -eq "RunNow") {
    Invoke-RemoteStep -Name "system-maintenance-run-now" `
      -Command "bash /usr/local/sbin/monthly-maintenance.sh" `
      -IdleTimeoutSeconds 1200 -HardTimeoutSeconds 1800
    Invoke-RemoteStep -Name "v2ray-agent-update-run-now" `
      -Command "bash /usr/local/sbin/vps-launcher-v2ray-agent-update.sh --apply" `
      -IdleTimeoutSeconds 180 -HardTimeoutSeconds 360
  }

  Invoke-LocalStep -Name "cpa-doctor-post" -Path $cpaScript -Arguments @("-Profile", "bwg")
  Invoke-LocalStep -Name "control-plane-final-inventory" -Path $maintenanceScript -Arguments $maintenanceArgs
}
catch {
  $script:exitCode = 1
  Write-RunLine "FULL_CHAIN_ERROR $($_.Exception.Message)"
}
finally {
  if ($null -eq $oldIntegration) {
    Remove-Item Env:VPS_SSH_LAUNCHER_RUN_INTEGRATION -ErrorAction SilentlyContinue
  }
  else {
    $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = $oldIntegration
  }
}

$summary = [ordered]@{
  run_id = $runId
  profile = "bwg"
  mode = $Mode
  status = if ($script:exitCode -eq 0) { "PASS" } else { "FAIL" }
  log = $logPath
  steps = @($script:steps)
}
$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryPath -Encoding utf8NoBOM
Write-Output "FULL_CHAIN_RUN_ID=$runId"
Write-Output "FULL_CHAIN_SUMMARY=$summaryPath"
Write-Output "FULL_CHAIN_LOG=$logPath"
if ($script:exitCode -ne 0) {
  exit $script:exitCode
}
Write-Output "FULL_CHAIN_OK mode=$Mode profile=bwg"

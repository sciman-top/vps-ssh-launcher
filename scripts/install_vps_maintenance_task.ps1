[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
  [string]$TaskName = "VPS-SshLauncher-BWG-Observe",
  [ValidatePattern('^[A-Za-z0-9_.-]+$')]
  [string]$Profile = "bwg",
  [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')]
  [string]$At = "12:30",
  [switch]$Replace,
  [switch]$Remove
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runScript = (Resolve-Path (Join-Path $PSScriptRoot "vps_maintenance.ps1")).Path

function Quote-TaskArgument {
  param([Parameter(Mandatory = $true)][string]$Value)

  return '"' + $Value.Replace('"', '\"') + '"'
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Remove) {
  if ($null -eq $existing) {
    Write-Output "TASK_ABSENT name=$TaskName"
    exit 0
  }
  if ($PSCmdlet.ShouldProcess($TaskName, "Unregister scheduled task")) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "TASK_REMOVED name=$TaskName"
  }
  exit 0
}

if ($null -ne $existing -and -not $Replace) {
  throw "Scheduled task already exists: $TaskName. Pass -Replace to update it."
}

$pwsh = (Get-Command pwsh -ErrorAction Stop).Source
$arguments = @(
  "-NoLogo",
  "-NoProfile",
  "-NonInteractive",
  "-File", (Quote-TaskArgument -Value $runScript),
  "-Profile", (Quote-TaskArgument -Value $Profile),
  "-RunIntegration"
) -join " "
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments
$triggerTime = [DateTime]::ParseExact(
  $At,
  "HH:mm",
  [Globalization.CultureInfo]::InvariantCulture
)
$trigger = New-ScheduledTaskTrigger -Daily -At $triggerTime
$principal = New-ScheduledTaskPrincipal `
  -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
  -LogonType Interactive `
  -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
  -MultipleInstances IgnoreNew `
  -StartWhenAvailable

if ($PSCmdlet.ShouldProcess($TaskName, "Register daily read-only VPS maintenance task")) {
  if ($null -ne $existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  }
  Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Fresh inventory and dry-run plan for the scoped VPS maintenance control plane." | Out-Null
  Write-Output "TASK_REGISTERED name=$TaskName profile=$Profile at=$At mode=observe-only"
}

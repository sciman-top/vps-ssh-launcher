#requires -Version 7
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
  [string]$TaskName = "VPS-SshLauncher-BWG-Observe",
  [ValidatePattern('^[A-Za-z0-9_.-]+$')]
  [string]$Profile = "bwg",
  [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')]
  [string]$At = "20:00",
  [switch]$AutoApply,
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
  "-WindowStyle", "Hidden",
  "-File", (Quote-TaskArgument -Value $runScript),
  "-Profile", (Quote-TaskArgument -Value $Profile),
  "-RunIntegration"
) -join " "
if ($AutoApply) {
  $arguments += ' -AutoApply'
}
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments
$triggerTime = [DateTime]::ParseExact(
  $At,
  "HH:mm",
  [Globalization.CultureInfo]::InvariantCulture
)
$trigger = New-ScheduledTaskTrigger -Daily -At $triggerTime
# S4U fires the task whether or not the user has an interactive session;
# an Interactive principal silently skips runs while logged off, which for
# -AutoApply would silently drop the only unattended maintenance window.
$principal = New-ScheduledTaskPrincipal `
  -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
  -LogonType S4U `
  -RunLevel Limited
# Two hours, not the default-shaped 20 minutes: a mid-transaction hard kill
# orphans the remote adapter work (it keeps running under its own lock) while
# the local receipt and unattended lock state are lost.
$settings = New-ScheduledTaskSettingsSet `
  -Hidden `
  -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
  -MultipleInstances IgnoreNew `
  -StartWhenAvailable

$operation = if ($AutoApply) {
  "Register policy-gated unattended BWG maintenance task"
} else {
  "Register daily read-only VPS maintenance task"
}
if ($PSCmdlet.ShouldProcess($TaskName, $operation)) {
  if ($null -ne $existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  }
  $description = if ($AutoApply) {
    "Policy-gated single-BWG unattended backup/apply/verify/rollback maintenance task."
  } else {
    "Fresh inventory and dry-run plan for the scoped VPS maintenance control plane."
  }
  Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description $description | Out-Null
  if ($AutoApply) {
    Write-Output "TASK_REGISTERED name=$TaskName profile=$Profile at=$At mode=unattended-apply silent=true"
  } else {
    Write-Output "TASK_REGISTERED name=$TaskName profile=$Profile at=$At mode=observe-only silent=true"
  }
}

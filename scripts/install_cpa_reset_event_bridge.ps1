#requires -Version 7
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
  [ValidatePattern('^[A-Za-z0-9_.-]+$')]
  [string]$TaskName = "VPS-Cpa-Reset-Bridge",
  [switch]$Replace,
  [switch]$Remove,
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$bridge = (Resolve-Path (Join-Path $PSScriptRoot "cpa_reset_event_bridge.ps1")).Path
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($Remove) {
  if ($null -eq $existing) {
    Write-Output "TASK_ABSENT name=$TaskName"
    exit 0
  }
  if ($PSCmdlet.ShouldProcess($TaskName, "Unregister CPA reset event bridge")) {
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
  "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
  "-ExecutionPolicy", "Bypass", "-File", ('"' + $bridge.Replace('"', '\"') + '"'),
  "-Run"
) -join " "
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal = New-ScheduledTaskPrincipal `
  -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
  -LogonType Interactive `
  -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -Hidden `
  -ExecutionTimeLimit (New-TimeSpan -Hours 12) `
  -MultipleInstances IgnoreNew `
  -StartWhenAvailable

if ($PSCmdlet.ShouldProcess($TaskName, "Register CPA reset event bridge")) {
  if ($null -ne $existing) {
    $backupDir = Join-Path $env:LOCALAPPDATA "vps-ssh-launcher\task-backups"
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backup = Join-Path $backupDir ("{0}-{1}.xml" -f $TaskName, (Get-Date -Format "yyyyMMdd-HHmmss"))
    Export-ScheduledTask -TaskName $TaskName | Set-Content -LiteralPath $backup -Encoding UTF8
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "TASK_BACKUP=$backup"
  }
  try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
      -Principal $principal -Settings $settings `
      -Description "Bridges Cockpit quota or CPAMC cooldown reset audit success to one fail-closed BWG CPA recovery probe." | Out-Null
  }
  catch {
    if ($null -ne $existing -and (Test-Path -LiteralPath $backup -PathType Leaf)) {
      Register-ScheduledTask -TaskName $TaskName -Xml (Get-Content -LiteralPath $backup -Raw) | Out-Null
    }
    throw
  }
  Write-Output "TASK_REGISTERED name=$TaskName mode=log-event-bridge hidden=true"
  if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Milliseconds 500
    $current = Get-ScheduledTask -TaskName $TaskName
    Write-Output "TASK_STARTED name=$TaskName state=$($current.State)"
  }
}

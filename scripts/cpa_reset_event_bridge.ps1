#requires -Version 7
<##
.SYNOPSIS
  Bridges a successful Cockpit quota-reset receipt to one CPA recovery probe.

.DESCRIPTION
  This is a local, fail-closed watcher. It reads the exact successful
  `/rate-limit-reset-credits/consume` line from Cockpit and the successful
  `/v8/management/routing/cooldown/reset` audit line from BWG. It never
  clears a remote cooldown by itself: the CPAMC UI owns that management action;
  cpa_recovery_workflow.ps1 performs one zero-retry generation request and CPA
  clears the admission lane only after a complete expected response. Each log
  event is persisted before dispatch so a restart cannot replay a reset into
  another upstream request.
##>
[CmdletBinding()]
param(
  [switch]$Run,
  [switch]$InitializeOnly,
  [string]$LogDirectory = (Join-Path $env:USERPROFILE ".antigravity_cockpit\logs"),
  [string]$StatePath = (Join-Path $env:LOCALAPPDATA "vps-ssh-launcher\cpa-reset-bridge-state.json"),
  [string]$BridgeLogPath = (Join-Path $env:LOCALAPPDATA "vps-ssh-launcher\cpa-reset-bridge.log"),
  [ValidateRange(2, 60)]
  [int]$PollSeconds = 5,
  [ValidateRange(10, 120)]
  [int]$RemotePollSeconds = 30,
  [ValidateSet("gpt-6.1-sol", "gpt-6-luna", "gpt-5.6-luna")]
  [string]$RecoveryModel = "gpt-6.1-sol"
)

$ErrorActionPreference = "Stop"
$workflow = Join-Path $PSScriptRoot "cpa_recovery_workflow.ps1"
$eventPattern = 'Codex 主动重置响应: url=https://chatgpt\.com/backend-api/wham/rate-limit-reset-credits/consume, status=200 OK'
$mutex = [Threading.Mutex]::new($false, "Local\VpsCpaResetEventBridge")

function Write-BridgeLog {
  param([Parameter(Mandatory = $true)][string]$Message)
  $parent = Split-Path -Parent $BridgeLogPath
  if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  $line = "{0} {1}" -f ([DateTimeOffset]::Now.ToString("o")), $Message
  Add-Content -LiteralPath $BridgeLogPath -Value $line -Encoding UTF8
}

function Save-State {
  param([Parameter(Mandatory = $true)][hashtable]$State)
  $parent = Split-Path -Parent $StatePath
  if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  $tmp = "$StatePath.tmp.$PID"
  try {
    $State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $StatePath -Force | Out-Null
  }
  finally {
    if (Test-Path -LiteralPath $tmp) {
      Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
  }
}

function Load-State {
  if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
    return $null
  }
  try {
    $raw = Get-Content -LiteralPath $StatePath -Raw -ErrorAction Stop | ConvertFrom-Json
    if ($raw.schema -ne 1 -or
        [string]::IsNullOrWhiteSpace([string]$raw.last_event_id) -or
        [string]::IsNullOrWhiteSpace([string]$raw.last_event_time) -or
        [string]::IsNullOrWhiteSpace([string]$raw.last_event_log)) {
      throw "invalid state schema"
    }
    [void][DateTimeOffset]::Parse([string]$raw.last_event_time)
    return @{
      schema = 1
      last_event_id = [string]$raw.last_event_id
      last_event_time = [string]$raw.last_event_time
      last_event_log = [string]$raw.last_event_log
      last_result = [string]$raw.last_result
      last_processed_at = [string]$raw.last_processed_at
    }
  }
  catch {
    Write-BridgeLog "STATE_INVALID action=fail_closed class=$($_.Exception.GetType().Name)"
    return $null
  }
}

function Get-ActiveLogs {
  # Scan the two newest logs: a reset line written to the previous file just
  # before Cockpit rotates it (restart or date change) must still dispatch.
  # Two files bound the per-poll IO while closing that one-rotation window;
  # content-hash event ids and Is-NewerEvent suppress double dispatch.
  if (-not (Test-Path -LiteralPath $LogDirectory -PathType Container)) {
    return @()
  }
  return @(Get-ChildItem -LiteralPath $LogDirectory -File -Filter "app.log.*" |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 2)
}

function Get-ResetEvents {
  param([Parameter(Mandatory = $true)][System.IO.FileInfo]$LogFile)
  $events = @()
  foreach ($line in (Get-Content -LiteralPath $LogFile.FullName -Tail 20000 -ErrorAction Stop)) {
    if ($line -notmatch $eventPattern) {
      continue
    }
    if ($line -notmatch '^(?<time>\d{4}-\d{2}-\d{2}T[^ ]+)\s+') {
      continue
    }
    try {
      $time = [DateTimeOffset]::Parse([string]$Matches.time)
    }
    catch {
      continue
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes($line.Trim())
    $hash = [Security.Cryptography.SHA256]::HashData($bytes)
    $eventId = ([BitConverter]::ToString($hash)).Replace("-", "").ToLowerInvariant()
    $events += [pscustomobject]@{
      Id = $eventId
      Time = $time
      Log = $LogFile.Name
    }
  }
  return $events | Sort-Object Time, Id
}

function Get-EventId {
  param([Parameter(Mandatory = $true)][string]$Line)
  $bytes = [Text.Encoding]::UTF8.GetBytes($Line.Trim())
  $hash = [Security.Cryptography.SHA256]::HashData($bytes)
  return ([BitConverter]::ToString($hash)).Replace("-", "").ToLowerInvariant()
}

function Get-ManagementResetEvents {
  # The CPAMC management page runs through the existing BWG SSH forward. The
  # CPA container's access log records status and route, so no management key
  # or request body needs to be copied into the bridge.
  $remoteCommand = @"
docker logs --since 10m --tail 300 cli-proxy-api 2>&1 | grep -E '200[[:space:]]+\|.*POST[[:space:]]+"/v8/management/routing/cooldown/reset"' || true
"@
  $oldIntegration = [Environment]::GetEnvironmentVariable(
    "VPS_SSH_LAUNCHER_RUN_INTEGRATION", "Process"
  )
  try {
    $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
    $lines = @(
      & pwsh -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File (Join-Path $PSScriptRoot "..\connect.ps1") -Profile bwg `
        -StrictHostKeyChecking -Command $remoteCommand `
        -CommandTimeout 25 -CommandHardTimeout 35 2>&1
    )
    if ($LASTEXITCODE -ne 0) {
      throw "remote log read failed with exit code $LASTEXITCODE"
    }
  }
  finally {
    if ($null -eq $oldIntegration) {
      Remove-Item Env:VPS_SSH_LAUNCHER_RUN_INTEGRATION -ErrorAction SilentlyContinue
    }
    else {
      $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = $oldIntegration
    }
  }
  $events = @()
  foreach ($rawLine in $lines) {
    $line = [string]$rawLine
    if ($line -notmatch '200\s+\|.*POST\s+"/v8/management/routing/cooldown/reset"') {
      continue
    }
    if ($line -notmatch '^\[(?<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]') {
      continue
    }
    try {
      $time = [DateTimeOffset]::Parse([string]$Matches.time)
    }
    catch {
      continue
    }
    $events += [pscustomobject]@{
      Id = Get-EventId -Line $line
      Time = $time
      Log = "remote:cli-proxy-api"
    }
  }
  return $events | Sort-Object Time, Id
}

function Initialize-StateFromLogs {
  param(
    [Parameter(Mandatory = $true)][System.IO.FileInfo[]]$LogFiles
  )
  $events = @(foreach ($logFile in $LogFiles) { Get-ResetEvents -LogFile $logFile }) |
    Sort-Object Time, Id
  $baseline = $events | Select-Object -Last 1
  if ($null -eq $baseline) {
    $baseline = [pscustomobject]@{
      Id = "none"
      Time = [DateTimeOffset]::MinValue
      Log = $LogFiles[0].Name
    }
  }
  $newState = @{
    schema = 1
    last_event_id = $baseline.Id
    last_event_time = $baseline.Time.ToString("o")
    last_event_log = $baseline.Log
    last_result = "BASELINE_INITIALIZED"
    last_processed_at = [DateTimeOffset]::Now.ToString("o")
  }
  Save-State -State $newState
  Write-BridgeLog "BASELINE_INITIALIZED log=$($baseline.Log) event=$($baseline.Id)"
  return $newState
}

function Add-ManagementStateFields {
  param(
    [Parameter(Mandatory = $true)][hashtable]$State,
    [Parameter(Mandatory = $true)][DateTimeOffset]$BaselineAt
  )
  if (-not $State.ContainsKey("last_management_event_id")) {
    $State.last_management_event_id = "none"
  }
  if (-not $State.ContainsKey("last_management_event_time") -or
      [string]::IsNullOrWhiteSpace([string]$State.last_management_event_time)) {
    $State.last_management_event_time = $BaselineAt.ToString("o")
  }
  if (-not $State.ContainsKey("last_management_event_log")) {
    $State.last_management_event_log = "uninitialized"
  }
  return $State
}

function Is-NewerManagementEvent {
  param(
    [Parameter(Mandatory = $true)]$Event,
    [Parameter(Mandatory = $true)][hashtable]$State
  )
  if ($Event.Id -eq $State.last_management_event_id) {
    return $false
  }
  try {
    $last = [DateTimeOffset]::Parse([string]$State.last_management_event_time)
    if ($Event.Time -lt $last) { return $false }
    if ($Event.Time -eq $last -and
        $Event.Id -lt [string]$State.last_management_event_id) { return $false }
  }
  catch {
    return $false
  }
  return $true
}

function Is-NewerEvent {
  param(
    [Parameter(Mandatory = $true)]$Event,
    [Parameter(Mandatory = $true)][hashtable]$State
  )
  if ($Event.Id -eq $State.last_event_id) {
    return $false
  }
  try {
    $last = [DateTimeOffset]::Parse($State.last_event_time)
    if ($Event.Time -lt $last) { return $false }
    if ($Event.Time -eq $last -and $Event.Id -lt $State.last_event_id) { return $false }
  }
  catch {
    return $false
  }
  return $true
}

function Dispatch-Recovery {
  param(
    [ValidateSet("quota_reset", "cooldown_reset")]
    [string]$Reason = "quota_reset"
  )
  # The event is authenticated by an exact Cockpit or BWG management success
  # line. The remote workflow performs the real upstream check and keeps
  # fail-closed semantics when the line was stale or the account remains limited.
  $oldIntegration = [Environment]::GetEnvironmentVariable(
    "VPS_SSH_LAUNCHER_RUN_INTEGRATION", "Process"
  )
  try {
    # Scheduled tasks do not inherit the interactive shell's opt-in. Set it
    # only for the child workflow and restore the bridge process afterwards.
    $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
    $confirmation = if ($Reason -eq "cooldown_reset") {
      "-CooldownResetConfirmed"
    }
    else {
      "-QuotaResetConfirmed"
    }
    & pwsh -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
      -File $workflow -Mode RecoverAfterReset $confirmation `
      -RecoveryModel $RecoveryModel *> $null
    return $LASTEXITCODE
  }
  finally {
    if ($null -eq $oldIntegration) {
      Remove-Item Env:VPS_SSH_LAUNCHER_RUN_INTEGRATION -ErrorAction SilentlyContinue
    }
    else {
      $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = $oldIntegration
    }
  }
}

try {
  if (-not ($Run -or $InitializeOnly)) {
    throw "Pass -Run for the persistent bridge or -InitializeOnly to create a baseline."
  }
  if (-not (Test-Path -LiteralPath $workflow -PathType Leaf)) {
    throw "Recovery workflow missing: $workflow"
  }
  if (-not $mutex.WaitOne(0)) {
    Write-BridgeLog "BRIDGE_ALREADY_RUNNING action=exit"
    exit 0
  }

  $bridgeStartedAt = [DateTimeOffset]::Now
  $state = Load-State
  $logFiles = Get-ActiveLogs
  if ($logFiles.Count -eq 0) {
    Write-BridgeLog "LOG_UNAVAILABLE action=wait"
    if ($InitializeOnly) { exit 0 }
  }
  if ($null -eq $state -and $logFiles.Count -gt 0) {
    $state = Initialize-StateFromLogs -LogFiles $logFiles
  }

  if ($null -eq $state) {
    $state = @{
      schema = 1
      last_event_id = "none"
      last_event_time = $bridgeStartedAt.ToString("o")
      last_event_log = "unavailable"
      last_result = "BASELINE_PENDING"
      last_processed_at = $bridgeStartedAt.ToString("o")
    }
    Write-BridgeLog "BASELINE_PENDING log=unavailable event=none"
  }
  $state = Add-ManagementStateFields -State $state -BaselineAt $bridgeStartedAt
  $remoteBaselineNeeded = $state.last_management_event_log -eq "uninitialized"
  if ($remoteBaselineNeeded) {
    try {
      $managementEvents = @(Get-ManagementResetEvents)
      $managementBaseline = $managementEvents | Select-Object -Last 1
      if ($null -ne $managementBaseline) {
        $state.last_management_event_id = $managementBaseline.Id
        $state.last_management_event_time = $managementBaseline.Time.ToString("o")
        $state.last_management_event_log = $managementBaseline.Log
        Write-BridgeLog "REMOTE_BASELINE_INITIALIZED event=$($managementBaseline.Id)"
      }
      else {
        $state.last_management_event_log = "remote:baseline-empty"
        Write-BridgeLog "REMOTE_BASELINE_INITIALIZED event=none"
      }
    }
    catch {
      # A failed baseline must not turn pre-start log history into a recovery
      # trigger. The start timestamp remains the lower bound for later events.
      $state.last_management_event_log = "remote:baseline-unavailable"
      Write-BridgeLog "REMOTE_BASELINE_UNAVAILABLE action=fail_closed class=$($_.Exception.GetType().Name)"
    }
  }
  Save-State -State $state

  if ($InitializeOnly) { exit 0 }
  $nextRemotePoll = [DateTimeOffset]::Now.AddSeconds($RemotePollSeconds)
  while ($true) {
    try {
      $logFiles = Get-ActiveLogs
      if ($logFiles.Count -gt 0 -and $state.last_event_log -eq "unavailable") {
        # A task can start before Cockpit creates its first log. Establish a
        # fresh baseline once the log appears; never replay pre-start history.
        $managementId = $state.last_management_event_id
        $managementTime = $state.last_management_event_time
        $managementLog = $state.last_management_event_log
        $state = Initialize-StateFromLogs -LogFiles $logFiles
        $state.last_management_event_id = $managementId
        $state.last_management_event_time = $managementTime
        $state.last_management_event_log = $managementLog
        Save-State -State $state
      }
      if ($logFiles.Count -gt 0) {
        $resetEvents = @(
          foreach ($logFile in $logFiles) { Get-ResetEvents -LogFile $logFile }
        ) | Sort-Object Time, Id
        foreach ($resetEvent in $resetEvents) {
          if (-not (Is-NewerEvent -Event $resetEvent -State $state)) { continue }
          # Persist before dispatch: one Cockpit reset produces at most one
          # remote probe even if the bridge or SSH process is interrupted.
          $state.last_event_id = $resetEvent.Id
          $state.last_event_time = $resetEvent.Time.ToString("o")
          $state.last_event_log = $resetEvent.Log
          $state.last_processed_at = [DateTimeOffset]::Now.ToString("o")
          $state.last_result = "DISPATCHING"
          Save-State -State $state
          $exitCode = Dispatch-Recovery -Reason "quota_reset"
          $state.last_result = if ($exitCode -eq 0) { "DISPATCHED_OK" } else { "DISPATCHED_FAIL" }
          $state.last_processed_at = [DateTimeOffset]::Now.ToString("o")
          Save-State -State $state
          Write-BridgeLog "RESET_EVENT_DISPATCHED event=$($resetEvent.Id) model=$RecoveryModel exit=$exitCode result=$($state.last_result)"
        }
      }
    }
    catch {
      Write-BridgeLog "POLL_ERROR action=wait class=$($_.Exception.GetType().Name)"
    }
    if ([DateTimeOffset]::Now -ge $nextRemotePoll) {
      try {
        foreach ($managementEvent in @(Get-ManagementResetEvents)) {
          if (-not (Is-NewerManagementEvent -Event $managementEvent -State $state)) { continue }
          # Persist before dispatch. A successful CPAMC UI reset is already
          # applied remotely; the remaining action is one bounded admission
          # recovery probe, never a management-key replay.
          $state.last_management_event_id = $managementEvent.Id
          $state.last_management_event_time = $managementEvent.Time.ToString("o")
          $state.last_management_event_log = $managementEvent.Log
          $state.last_processed_at = [DateTimeOffset]::Now.ToString("o")
          $state.last_result = "DISPATCHING"
          Save-State -State $state
          $exitCode = Dispatch-Recovery -Reason "cooldown_reset"
          $state.last_result = if ($exitCode -eq 0) { "DISPATCHED_OK" } else { "DISPATCHED_FAIL" }
          $state.last_processed_at = [DateTimeOffset]::Now.ToString("o")
          Save-State -State $state
          Write-BridgeLog "MANAGEMENT_RESET_DISPATCHED event=$($managementEvent.Id) model=$RecoveryModel exit=$exitCode result=$($state.last_result)"
        }
      }
      catch {
        Write-BridgeLog "REMOTE_POLL_ERROR action=wait class=$($_.Exception.GetType().Name)"
      }
      $nextRemotePoll = [DateTimeOffset]::Now.AddSeconds($RemotePollSeconds)
    }
    Start-Sleep -Seconds $PollSeconds
  }
}
finally {
  if ($null -ne $mutex) { $mutex.Dispose() }
}

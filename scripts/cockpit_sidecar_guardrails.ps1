#requires -Version 7
<##
.SYNOPSIS
  Audit and re-project the version-pinned Cockpit sidecar guardrails.

.DESCRIPTION
  This is the project entrypoint for the local Direct API repair. It keeps the
  sidecar binary, persistent collection settings, generated config drift, and
  running process state as separate evidence layers.

  Audit and Verify are read-only. Project creates timestamped backups and uses
  staging files plus atomic replacement. It never stops Cockpit or a sidecar;
  a subsequent official Cockpit reload is required before a newly projected
  binary becomes the running image.
#>
param(
  [ValidateSet("Audit", "Project", "Verify")]
  [string]$Mode = "Audit",
  [string]$CandidatePath = "",
  [switch]$SkipPersistentSettings
)

$ErrorActionPreference = "Stop"
$policyPath = Join-Path $PSScriptRoot "cockpit_sidecar_policy.json"
$policy = Get-Content -LiteralPath $policyPath -Raw | ConvertFrom-Json
$repoRoot = Split-Path -Parent $PSScriptRoot

function Resolve-UserPath([string]$Path) {
  [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($Path))
}

function Get-Sha256([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Get-FileVersion([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  try {
    return (Get-Item -LiteralPath $Path).VersionInfo.FileVersion
  } catch {
    return $null
  }
}

function Write-JsonAtomic([string]$Path, [object]$Value, [string]$BackupTag) {
  $parent = Split-Path -Parent $Path
  $name = Split-Path -Leaf $Path
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $backup = "$Path.bak-$BackupTag-$stamp"
  Copy-Item -LiteralPath $Path -Destination $backup -Force
  $stage = Join-Path $parent (".$name.stage-$PID-$stamp")
  $json = $Value | ConvertTo-Json -Depth 50
  [IO.File]::WriteAllText($stage, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
  Move-Item -LiteralPath $stage -Destination $Path -Force
  $backup
}

function Get-SidecarProcesses {
  @(Get-CimInstance Win32_Process -Filter "Name = 'cockpit-cliproxy.exe'" | ForEach-Object {
    [pscustomobject]@{
      pid = $_.ProcessId
      parentPid = $_.ParentProcessId
      started = $_.CreationDate
      executable = $_.ExecutablePath
      sha256 = Get-Sha256 $_.ExecutablePath
    }
  })
}

function Get-Listeners {
  @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @($policy.requiredListeners) } |
    Select-Object LocalAddress, LocalPort, OwningProcess)
}

function Get-ConfigSummary([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  $j = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
  [pscustomobject]@{
    path = $Path
    requestRetry = $j.'request-retry'
    streamBootstrapBuffering = $j.codex.'stream-bootstrap-buffering'
    port = $j.port
    host = $j.host
  }
}

function Get-ManifestSummary([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  $j = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
  [pscustomobject]@{
    path = $Path
    maxAccountConcurrency = $j.maxAccountConcurrency
    accountConcurrencyWaitMs = $j.accountConcurrencyWaitMs
    accounts = @($j.accounts).Count
  }
}

function Get-PersistentSettings {
  @(
    (Join-Path $env:USERPROFILE ".antigravity_cockpit\codex_local_access.json"),
    (Join-Path $env:USERPROFILE ".cockpit_tools\codex_local_access.json")
  ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | ForEach-Object {
    $j = Get-Content -LiteralPath $_ -Raw | ConvertFrom-Json
    [pscustomobject]@{
      path = $_
      maxAccountConcurrency = $j.maxAccountConcurrency
      accountConcurrencyWaitMs = $j.accountConcurrencyWaitMs
      maxRetryCredentials = $j.maxRetryCredentials
      maxRetryIntervalMs = $j.maxRetryIntervalMs
    }
  }
}

function Get-ProfileFiles {
  $root = Join-Path $env:USERPROFILE ".cockpit_tools\codex_provider_gateway_sidecars"
  if (-not (Test-Path -LiteralPath $root -PathType Container)) { return @() }
  @(Get-ChildItem -LiteralPath $root -Directory | ForEach-Object {
    [pscustomobject]@{
      config = Get-ConfigSummary (Join-Path $_.FullName "config.json")
      manifest = Get-ManifestSummary (Join-Path $_.FullName "manifest.json")
    }
  })
}

function Get-RequestLogAudit {
  $db = Join-Path $env:USERPROFILE ".antigravity_cockpit\codex_local_access_logs.sqlite"
  $auditScript = Join-Path $PSScriptRoot "cockpit_request_log_audit.py"
  $root = Split-Path -Parent $PSScriptRoot
  $py = Join-Path $root ".venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $py -PathType Leaf)) { $py = (Get-Command python).Source }
  if (-not (Test-Path -LiteralPath $auditScript -PathType Leaf) -or
      -not (Test-Path -LiteralPath $db -PathType Leaf)) {
    return [pscustomobject]@{ status = "unavailable" }
  }
  try {
    $raw = & $py $auditScript --db $db --since-minutes 180 2>$null
    if ($LASTEXITCODE -ne 0) { return [pscustomobject]@{ status = "unavailable" } }
    $json = ($raw -join "`n") | ConvertFrom-Json
    return $json
  } catch {
    return [pscustomobject]@{ status = "unavailable" }
  }
}

function Get-Report {
  $exe = Join-Path $env:LOCALAPPDATA $policy.sidecarRelativePath
  $processes = Get-SidecarProcesses
  $listeners = Get-Listeners
  $listenerOwners = @($listeners | ForEach-Object {
    $listener = $_
    $owner = $processes | Where-Object { $_.pid -eq $listener.OwningProcess }
    [pscustomobject]@{
      port = $listener.LocalPort
      owningPid = $listener.OwningProcess
      ownerFound = $null -ne $owner
      ownerHash = if ($owner) { $owner.sha256 } else { $null }
      ownerStarted = if ($owner) { $owner.started } else { $null }
    }
  })
  [pscustomobject]@{
    mode = $Mode
    policySha256 = $policy.sidecarSha256
    installed = [pscustomobject]@{ path = $exe; sha256 = Get-Sha256 $exe; version = Get-FileVersion $exe; lastWriteUtc = (Get-Item -LiteralPath $exe -ErrorAction SilentlyContinue).LastWriteTimeUtc }
    processes = $processes | Select-Object pid,parentPid,started,executable,sha256
    listeners = $listeners
    listenerOwners = $listenerOwners
    persistentSettings = Get-PersistentSettings
    apiConfig = Get-ConfigSummary (Join-Path $env:USERPROFILE ".cockpit_tools\codex_local_access_sidecar\config.json")
    apiManifest = Get-ManifestSummary (Join-Path $env:USERPROFILE ".cockpit_tools\codex_local_access_sidecar\manifest.json")
    providerProfiles = Get-ProfileFiles
    requestLogAudit = Get-RequestLogAudit
    knownProviderManifestDrift = $policy.knownGeneratedProviderManifest
  }
}

if ($Mode -eq "Project") {
  if ([string]::IsNullOrWhiteSpace($CandidatePath)) {
    throw "Project requires -CandidatePath pointing to the version-matched r3 sidecar executable."
  }
  $candidate = Resolve-UserPath $CandidatePath
  $candidateVersion = Get-FileVersion $candidate
  if ($candidateVersion -and $candidateVersion -notlike "$($policy.cockpitVersion).*" -and $candidateVersion -ne $policy.cockpitVersion) {
    throw "Candidate file version mismatch. expected=$($policy.cockpitVersion) actual=$candidateVersion"
  }
  $candidateHash = Get-Sha256 $candidate
  if ($candidateHash -ne $policy.sidecarSha256) {
    throw "Candidate SHA-256 mismatch. expected=$($policy.sidecarSha256) actual=$candidateHash"
  }

  $target = Join-Path $env:LOCALAPPDATA $policy.sidecarRelativePath
  $targetHash = Get-Sha256 $target
  if ($targetHash -eq $policy.sidecarSha256) {
    Write-Output "SIDECAR_ALREADY_PROJECTED=1"
  } else {
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $backup = "$target.before-project-$stamp.bak"
  Copy-Item -LiteralPath $target -Destination $backup -Force
  $stage = "$target.stage-$PID-$stamp"
  Copy-Item -LiteralPath $candidate -Destination $stage -Force
  if ((Get-Sha256 $stage) -ne $policy.sidecarSha256) {
    Remove-Item -LiteralPath $stage -Force -ErrorAction SilentlyContinue
    throw "Staged sidecar hash changed before replacement."
  }
  $old = "$target.pre-project-$stamp.old"
  try {
    Move-Item -LiteralPath $target -Destination $old -Force
    Move-Item -LiteralPath $stage -Destination $target -Force
    Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue
  } catch {
    Remove-Item -LiteralPath $stage -Force -ErrorAction SilentlyContinue
    if ((-not (Test-Path -LiteralPath $target -PathType Leaf)) -and (Test-Path -LiteralPath $old -PathType Leaf)) {
      Move-Item -LiteralPath $old -Destination $target -Force -ErrorAction SilentlyContinue
    }
    throw "Sidecar replacement failed; rollback attempted. $($_.Exception.Message)"
  }
  Write-Output "SIDECAR_PROJECTED=1"
  Write-Output "SIDECAR_BACKUP=$backup"
  }

  if (-not $SkipPersistentSettings) {
    foreach ($path in @(
      (Join-Path $env:USERPROFILE ".antigravity_cockpit\codex_local_access.json"),
      (Join-Path $env:USERPROFILE ".cockpit_tools\codex_local_access.json")
    )) {
      if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
      $j = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
      $unchanged = $j.maxAccountConcurrency -eq $policy.persistentCollection.maxAccountConcurrency -and
        $j.accountConcurrencyWaitMs -eq $policy.persistentCollection.accountConcurrencyWaitMs -and
        $j.maxRetryCredentials -eq $policy.persistentCollection.maxRetryCredentials -and
        $j.maxRetryIntervalMs -eq $policy.persistentCollection.maxRetryIntervalMs
      if ($unchanged) {
        Write-Output "COLLECTION_ALREADY_PROJECTED=$path"
        continue
      }
      $j.maxAccountConcurrency = [int]$policy.persistentCollection.maxAccountConcurrency
      $j.accountConcurrencyWaitMs = [int]$policy.persistentCollection.accountConcurrencyWaitMs
      $j.maxRetryCredentials = [int]$policy.persistentCollection.maxRetryCredentials
      $j.maxRetryIntervalMs = [int]$policy.persistentCollection.maxRetryIntervalMs
      $b = Write-JsonAtomic $path $j "cockpit-sidecar-guardrails"
      Write-Output "COLLECTION_PROJECTED=$path"
      Write-Output "COLLECTION_BACKUP=$b"
    }
  }
  Write-Output "RELOAD_REQUIRED=1"
  Write-Output "RELOAD_RULE=Use Cockpit formal reload/start path; do not taskkill or stop API-bearing processes."
}

$report = Get-Report
if ($Mode -eq "Verify") {
  $installedOk = $report.installed.sha256 -eq $policy.sidecarSha256
  $runningOk = @($report.processes | Where-Object { $_.sha256 -eq $policy.sidecarSha256 }).Count -ge 2
  $ports = @($report.listeners.LocalPort | Sort-Object -Unique)
  $portsOk = $ports -contains 10909 -and $ports -contains 14185
  $ownersOk = @($report.listenerOwners | Where-Object {
    $_.port -in @(10909,14185) -and $_.ownerFound -and $_.ownerHash -eq $policy.sidecarSha256
  }).Count -eq 2
  $settingsOk = @($report.persistentSettings | Where-Object {
    $_.maxAccountConcurrency -eq $policy.persistentCollection.maxAccountConcurrency -and
    $_.accountConcurrencyWaitMs -eq $policy.persistentCollection.accountConcurrencyWaitMs -and
    $_.maxRetryCredentials -eq $policy.persistentCollection.maxRetryCredentials -and
    $_.maxRetryIntervalMs -eq $policy.persistentCollection.maxRetryIntervalMs
  }).Count -eq @($report.persistentSettings).Count -and @($report.persistentSettings).Count -gt 0
  Write-Output "INSTALLED_HASH=$installedOk"
  Write-Output "RUNNING_R3_DISK_PATH=$runningOk"
  Write-Output "LISTENER_OWNERS_R3=$ownersOk"
  Write-Output "HOST_LOADED=INFERRED_FROM_LISTENER_OWNERS"
  Write-Output "LISTENERS_10909_14185=$portsOk"
  Write-Output "PERSISTENT_SETTINGS=$settingsOk"
  if (-not ($installedOk -and $runningOk -and $ownersOk -and $portsOk -and $settingsOk)) {
    Write-Output "COCKPIT_SIDECAR_VERIFY=FAIL"
    exit 1
  }
  Write-Output "COCKPIT_SIDECAR_VERIFY=PASS"
} else {
  $report | ConvertTo-Json -Depth 30
}

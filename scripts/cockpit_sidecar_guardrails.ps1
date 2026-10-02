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

function Resolve-UserPath([string]$Path) {
  [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($Path))
}

function Get-Sha256([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
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

function Get-Report {
  $exe = Join-Path $env:LOCALAPPDATA $policy.sidecarRelativePath
  [pscustomobject]@{
    mode = $Mode
    policySha256 = $policy.sidecarSha256
    installed = [pscustomobject]@{ path = $exe; sha256 = Get-Sha256 $exe }
    processes = Get-SidecarProcesses | Select-Object pid,parentPid,started,executable,sha256
    listeners = Get-Listeners
    persistentSettings = Get-PersistentSettings
    apiConfig = Get-ConfigSummary (Join-Path $env:USERPROFILE ".cockpit_tools\codex_local_access_sidecar\config.json")
    apiManifest = Get-ManifestSummary (Join-Path $env:USERPROFILE ".cockpit_tools\codex_local_access_sidecar\manifest.json")
    providerProfiles = Get-ProfileFiles
    knownProviderManifestDrift = $policy.knownGeneratedProviderManifest
  }
}

if ($Mode -eq "Project") {
  if ([string]::IsNullOrWhiteSpace($CandidatePath)) {
    throw "Project requires -CandidatePath pointing to the version-matched r3 sidecar executable."
  }
  $candidate = Resolve-UserPath $CandidatePath
  $candidateHash = Get-Sha256 $candidate
  if ($candidateHash -ne $policy.sidecarSha256) {
    throw "Candidate SHA-256 mismatch. expected=$($policy.sidecarSha256) actual=$candidateHash"
  }

  $target = Join-Path $env:LOCALAPPDATA $policy.sidecarRelativePath
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $backup = "$target.before-project-$stamp.bak"
  Copy-Item -LiteralPath $target -Destination $backup -Force
  $stage = "$target.stage-$PID-$stamp"
  Copy-Item -LiteralPath $candidate -Destination $stage -Force
  if ((Get-Sha256 $stage) -ne $policy.sidecarSha256) {
    Remove-Item -LiteralPath $stage -Force -ErrorAction SilentlyContinue
    throw "Staged sidecar hash changed before replacement."
  }
  Move-Item -LiteralPath $stage -Destination $target -Force
  Write-Output "SIDECAR_PROJECTED=1"
  Write-Output "SIDECAR_BACKUP=$backup"

  if (-not $SkipPersistentSettings) {
    foreach ($path in @(
      (Join-Path $env:USERPROFILE ".antigravity_cockpit\codex_local_access.json"),
      (Join-Path $env:USERPROFILE ".cockpit_tools\codex_local_access.json")
    )) {
      if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
      $j = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
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
  $settingsOk = @($report.persistentSettings | Where-Object {
    $_.maxAccountConcurrency -eq $policy.persistentCollection.maxAccountConcurrency -and
    $_.accountConcurrencyWaitMs -eq $policy.persistentCollection.accountConcurrencyWaitMs
  }).Count -ge 1
  Write-Output "INSTALLED_HASH=$installedOk"
  Write-Output "RUNNING_R3=$runningOk"
  Write-Output "LISTENERS_10909_14185=$portsOk"
  Write-Output "PERSISTENT_SETTINGS=$settingsOk"
  if (-not ($installedOk -and $runningOk -and $portsOk -and $settingsOk)) {
    Write-Output "COCKPIT_SIDECAR_VERIFY=FAIL"
    exit 1
  }
  Write-Output "COCKPIT_SIDECAR_VERIFY=PASS"
} else {
  $report | ConvertTo-Json -Depth 30
}

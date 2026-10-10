#requires -Version 7
<##
.SYNOPSIS
  Audit and verify the Cockpit provider-gateway sidecar guardrails.

.DESCRIPTION
  Generated provider-gateway manifests in Cockpit 1.3.66 carry the persistent
  collection's maxAccountConcurrency / accountConcurrencyWaitMs, and the
  official binary honours the wait cap.  Behavioural evidence (2026-10-10):
  two manifests regenerated on 2026-10-09 both read 3 / 45000 while the
  2026-10-04 directories still read 0 / 120000, and a controlled replay
  against SHA-256 6E7CA54E... returned HTTP 429 at 45.002 s after three
  holders on a never-answering loopback stub.

  The 1.3.65-era sideloaded binary patch (SHA 72860FD9...) is therefore
  retired: the official build carries the fix, so there is no binary to
  reinstall.  Only the two persistent collection files remain writable here.

  Evidence layers stay separate:
    * installed file hash and app version -> filesystem_projected
    * running process executable path     -> host_loaded (inferred)
    * gate wait cap behaviour             -> controlled_live_replay, owned by
      scripts/cockpit_gate_wait_cap_check.py

  No mode stops Cockpit or a sidecar process.
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

function Get-CommandLineArgument([string]$CommandLine, [string]$Name) {
  if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $null }
  $pattern = '(?:^|\s)' + [regex]::Escape($Name) + '\s+(?:"([^"]+)"|(\S+))'
  $match = [regex]::Match($CommandLine, $pattern)
  if (-not $match.Success) { return $null }
  if ($match.Groups[1].Success) { return $match.Groups[1].Value }
  return $match.Groups[2].Value
}

function Get-SidecarProcesses {
  @(Get-CimInstance Win32_Process -Filter "Name = 'cockpit-cliproxy.exe'" | ForEach-Object {
    [pscustomobject]@{
      pid = $_.ProcessId
      parentPid = $_.ParentProcessId
      started = $_.CreationDate
      executable = $_.ExecutablePath
      sha256 = Get-Sha256 $_.ExecutablePath
      commandLine = $_.CommandLine
      configPath = Get-CommandLineArgument $_.CommandLine "--config"
      manifestPath = Get-CommandLineArgument $_.CommandLine "--manifest"
    }
  })
}

function Get-SidecarListeners([object[]]$Processes) {
  $pids = @(@($Processes) | ForEach-Object { $_.pid })
  if ($pids.Count -eq 0) { return @() }
  @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -in $pids } |
    Select-Object LocalAddress, LocalPort, OwningProcess)
}

function Get-ConfigSummary([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  $j = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
  [pscustomobject]@{
    path = $Path
    requestRetry = $j.'request-retry'
    maxRetryCredentials = $j.'max-retry-credentials'
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
    $manifestPath = Join-Path $_.FullName "manifest.json"
    [pscustomobject]@{
      dir = $_.Name
      config = Get-ConfigSummary (Join-Path $_.FullName "config.json")
      manifest = Get-ManifestSummary $manifestPath
      manifestUpdatedUtc = if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        (Get-Item -LiteralPath $manifestPath).LastWriteTimeUtc
      } else { $null }
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

function Get-GateEvidence {
  param(
    [object[]]$Processes,
    [object[]]$Profiles,
    [object]$InstalledUtc
  )
  # Prefer the manifests the running sidecars were actually launched with.
  # Only when nothing is running fall back to manifests that the current app
  # install regenerated since it landed on disk.
  $rows = @()
  $seen = @{}
  foreach ($process in @($Processes)) {
    if ([string]::IsNullOrWhiteSpace($process.manifestPath)) { continue }
    $key = $process.manifestPath.ToLowerInvariant()
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true
    $summary = Get-ManifestSummary $process.manifestPath
    if ($summary) {
      $rows += [pscustomobject]@{
        source = "running"
        path = $summary.path
        maxAccountConcurrency = $summary.maxAccountConcurrency
        accountConcurrencyWaitMs = $summary.accountConcurrencyWaitMs
      }
    }
  }
  if ($rows.Count -eq 0 -and $null -ne $InstalledUtc) {
    foreach ($profile in @($Profiles)) {
      if (-not $profile.manifest -or $null -eq $profile.manifestUpdatedUtc) { continue }
      if ($profile.manifestUpdatedUtc -lt $InstalledUtc) { continue }
      $rows += [pscustomobject]@{
        source = "regenerated"
        path = $profile.manifest.path
        maxAccountConcurrency = $profile.manifest.maxAccountConcurrency
        accountConcurrencyWaitMs = $profile.manifest.accountConcurrencyWaitMs
      }
    }
  }
  return $rows
}

function Get-Report {
  $exe = Join-Path $env:LOCALAPPDATA $policy.sidecarRelativePath
  $appExe = Join-Path (Split-Path -Parent $exe) "cockpit-tools.exe"
  $appItem = Get-Item -LiteralPath $appExe -ErrorAction SilentlyContinue
  $processes = Get-SidecarProcesses
  $listeners = Get-SidecarListeners $processes
  $profiles = @(Get-ProfileFiles)
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
  $installedUtc = $null
  if ($appItem) { $installedUtc = $appItem.LastWriteTimeUtc }
  [pscustomobject]@{
    mode = $Mode
    policySha256 = $policy.sidecarSha256
    cockpit = [pscustomobject]@{
      installedVersion = Get-FileVersion $appExe
      policyVersion = $policy.cockpitVersion
      patchRetiredFrom = $policy.generatorFixedFrom
    }
    installed = [pscustomobject]@{
      path = $exe
      sha256 = Get-Sha256 $exe
      version = Get-FileVersion $exe
      lastWriteUtc = if (Test-Path -LiteralPath $exe -PathType Leaf) { (Get-Item -LiteralPath $exe).LastWriteTimeUtc } else { $null }
    }
    processes = $processes | Select-Object pid,parentPid,started,executable,sha256,configPath,manifestPath
    listeners = $listeners
    listenerOwners = $listenerOwners
    persistentSettings = Get-PersistentSettings
    apiConfig = Get-ConfigSummary (Join-Path $env:USERPROFILE ".cockpit_tools\codex_local_access_sidecar\config.json")
    apiManifest = Get-ManifestSummary (Join-Path $env:USERPROFILE ".cockpit_tools\codex_local_access_sidecar\manifest.json")
    providerProfiles = $profiles
    gateEvidence = Get-GateEvidence -Processes $processes -Profiles $profiles -InstalledUtc $installedUtc
    expectedProviderManifest = $policy.expectedProviderManifest
    upstreamDefaults = $policy.knownUpstreamDefaults
    requestLogAudit = if ($Mode -eq "Audit") { Get-RequestLogAudit } else { [pscustomobject]@{ status = "skipped" } }
  }
}

if ($Mode -eq "Project") {
  $appExe = Join-Path (Split-Path -Parent (Join-Path $env:LOCALAPPDATA $policy.sidecarRelativePath)) 'cockpit-tools.exe'
  $installedAppVersion = Get-FileVersion $appExe
  if (-not $installedAppVersion -or ($installedAppVersion -ne $policy.cockpitVersion -and $installedAppVersion -notlike "$($policy.cockpitVersion).*")) {
    throw "Installed Cockpit version mismatch; no files changed. expected=$($policy.cockpitVersion) actual=$installedAppVersion"
  }
  if (-not [string]::IsNullOrWhiteSpace($CandidatePath)) {
    $candidate = Resolve-UserPath $CandidatePath
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
      throw "CandidatePath does not exist; no files changed. path=$candidate"
    }
    $candidateHash = Get-Sha256 $candidate
    if ($candidateHash -ne $policy.sidecarSha256) {
      throw "Candidate SHA-256 mismatch. expected=$($policy.sidecarSha256) actual=$candidateHash"
    }
  }
  Write-Output "SIDECAR_PATCH_RETIRED=1"
  Write-Output "SIDECAR_PATCH_RETIRED_FROM=$($policy.generatorFixedFrom)"
  Write-Output "SIDECAR_PATCH_RETIRED_REASON=Upstream generator writes maxAccountConcurrency/accountConcurrencyWaitMs into provider gateway manifests; no sideloaded binary is required."
  $target = Join-Path $env:LOCALAPPDATA $policy.sidecarRelativePath
  $targetHash = Get-Sha256 $target
  if ($targetHash -eq $policy.sidecarSha256) {
    Write-Output "SIDECAR_ALREADY_OFFICIAL=1"
  } else {
    throw "Installed sidecar SHA-256 mismatch; no files changed. expected=$($policy.sidecarSha256) actual=$targetHash. Repair path is an official Cockpit update or reinstall, not a sideloaded patch."
  }

  $changed = $false
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
      $changed = $true
    }
  }
  # A no-op projection must not ask for a reload: a permanent RELOAD_REQUIRED=1
  # trains the operator to ignore the signal.
  if ($changed) {
    Write-Output "RELOAD_REQUIRED=1"
    Write-Output "RELOAD_RULE=Use Cockpit formal reload/start path; do not taskkill or stop API-bearing processes."
  } else {
    Write-Output "RELOAD_REQUIRED=0"
    Write-Output "RELOAD_RULE=No file changed; nothing to reload."
  }
}

$report = Get-Report
if ($Mode -eq "Verify") {
  $expected = $policy.expectedProviderManifest
  $installedOk = $report.installed.sha256 -eq $policy.sidecarSha256
  $versionOk = $null -ne $report.cockpit.installedVersion -and (
    $report.cockpit.installedVersion -eq $policy.cockpitVersion -or
    $report.cockpit.installedVersion -like "$($policy.cockpitVersion).*"
  )
  # At least one running sidecar must be the pinned official image. The old
  # fixed listener pair is retired with the 1.3.65 patch: which ports exist now
  # follows whatever the desktop provider actually binds, so the listener set
  # is reported but no longer asserted.
  $runningOk = @($report.processes | Where-Object {
    $_.sha256 -eq $policy.sidecarSha256
  }).Count -ge 1
  $settingsOk = @($report.persistentSettings | Where-Object {
    $_.maxAccountConcurrency -eq $policy.persistentCollection.maxAccountConcurrency -and
    $_.accountConcurrencyWaitMs -eq $policy.persistentCollection.accountConcurrencyWaitMs -and
    $_.maxRetryCredentials -eq $policy.persistentCollection.maxRetryCredentials -and
    $_.maxRetryIntervalMs -eq $policy.persistentCollection.maxRetryIntervalMs
  }).Count -eq @($report.persistentSettings).Count -and @($report.persistentSettings).Count -gt 0
  # The gate only exists if the manifests the sidecars were launched with carry
  # the pinned concurrency pair. Hash, version and process identity alone would
  # pass on a build whose generator still writes 0 / 120000.
  $evidence = @($report.gateEvidence)
  $gateFieldsOk = $evidence.Count -gt 0 -and @($evidence | Where-Object {
    $_.maxAccountConcurrency -ne $expected.maxAccountConcurrency -or
    $_.accountConcurrencyWaitMs -ne $expected.accountConcurrencyWaitMs
  }).Count -eq 0
  Write-Output "INSTALLED_HASH=$installedOk"
  Write-Output "INSTALLED_VERSION=$versionOk"
  Write-Output "RUNNING_OFFICIAL_IMAGE=$runningOk"
  Write-Output "HOST_LOADED=INFERRED_FROM_EXECUTABLE_PATH"
  Write-Output "PERSISTENT_SETTINGS=$settingsOk"
  Write-Output "GATE_FIELDS=$gateFieldsOk"
  Write-Output "GATE_EVIDENCE_SOURCE=$(if ($evidence.Count -gt 0) { ($evidence | Select-Object -First 1).source } else { 'none' })"
  Write-Output "GATE_MANIFEST_MAXCONC=$(if ($evidence.Count -gt 0) { ($evidence | Select-Object -First 1).maxAccountConcurrency } else { 'n/a' })"
  Write-Output "GATE_MANIFEST_WAITMS=$(if ($evidence.Count -gt 0) { ($evidence | Select-Object -First 1).accountConcurrencyWaitMs } else { 'n/a' })"
  if (-not ($installedOk -and $versionOk -and $runningOk -and $settingsOk -and $gateFieldsOk)) {
    Write-Output "COCKPIT_SIDECAR_VERIFY=FAIL"
    exit 1
  }
  Write-Output "COCKPIT_SIDECAR_VERIFY=PASS"
} else {
  $report | ConvertTo-Json -Depth 30
}

#requires -Version 7.0
[CmdletBinding()]
param(
  [string]$CodexHome = "",
  [string]$CatalogFile = "",
  [switch]$Apply
)

$ErrorActionPreference = "Stop"

# Verifier (and with -Apply, repairer) for the Codex desktop model catalog
# context pins.
#
# The catalog itself is owned by Cockpit Tools (managed catalog rebuilds on
# account/provider switches; OAuth lanes fall back to Codex built-ins). The
# pins describe the Cockpit-managed catalog shape (max_context_window ==
# window, auto_compact = 90%), so they are enforced only when config.toml
# points at the Cockpit-managed catalog; built-in lanes report builtin and
# pass. Cockpit 1.3.75 exposes no per-model window UI, and its rebuilds emit
# fallback values, so -Apply writes the manifest values straight into the
# managed catalog file; re-run it after any Cockpit rebuild event.

if ([string]::IsNullOrWhiteSpace($CodexHome)) {
  if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
    $CodexHome = $env:CODEX_HOME
  }
  else {
    $CodexHome = Join-Path $env:USERPROFILE ".codex"
  }
}
$CodexHome = [System.IO.Path]::GetFullPath($CodexHome)
$manifestPath = Join-Path $PSScriptRoot "..\config\codex-model-context-overrides.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
  throw "Model context manifest not found: $manifestPath"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or @($manifest.models).Count -eq 0) {
  throw "Unsupported or empty model context manifest: $manifestPath"
}

$configPath = Join-Path $CodexHome "config.toml"
$failures = New-Object System.Collections.Generic.List[string]
$managedCatalogFile = "cockpit-model-catalog.json"
$catalogPointer = $null
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
  $configText = Get-Content -LiteralPath $configPath -Raw
  $pointerMatch = [regex]::Match($configText, '(?m)^\s*model_catalog_json\s*=\s*"([^"]+)"')
  if ($pointerMatch.Success) {
    $catalogPointer = $pointerMatch.Groups[1].Value.Trim()
  }
  if ($catalogPointer -eq "fq-model-context-catalog.json") {
    $failures.Add("config.toml pins the retired static catalog fq-model-context-catalog.json; lane-scoped catalogs require Cockpit-managed model_catalog_json")
  }
  elseif ($null -ne $catalogPointer -and $catalogPointer -ne $managedCatalogFile) {
    $failures.Add("config.toml model_catalog_json points at '$catalogPointer'; expected the Cockpit-managed catalog ($managedCatalogFile) or no pointer on built-in lanes")
  }
}
$enforcePins = $catalogPointer -eq $managedCatalogFile

if (-not [string]::IsNullOrWhiteSpace($CatalogFile)) {
  # Test seam: read a catalog file directly instead of probing the codex CLI,
  # so fixture homes without credentials can exercise the pointer/pin logic.
  if (-not (Test-Path -LiteralPath $CatalogFile -PathType Leaf)) {
    throw "Catalog file not found: $CatalogFile"
  }
  $raw = Get-Content -LiteralPath $CatalogFile
}
else {
  $codexCommand = Get-Command codex -ErrorAction Stop
  $previousCodexHome = $env:CODEX_HOME
  try {
    $env:CODEX_HOME = $CodexHome
    $raw = & $codexCommand.Source debug models 2>$null
    if ($LASTEXITCODE -ne 0) {
      throw "codex debug models failed with exit code $LASTEXITCODE"
    }
  }
  finally {
    $env:CODEX_HOME = $previousCodexHome
  }
}
$catalog = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
if ($null -eq $catalog.models) {
  throw "codex debug models returned no models array"
}

$matched = New-Object System.Collections.Generic.List[string]
$skipped = New-Object System.Collections.Generic.List[string]
if (-not $enforcePins) {
  $skipped.AddRange([string[]]($manifest.models | ForEach-Object { $_.slug }))
}

$applyTarget = $null
if ($Apply -and $enforcePins) {
  $applyTarget = if (-not [string]::IsNullOrWhiteSpace($CatalogFile)) {
    $CatalogFile
  }
  else {
    Join-Path $CodexHome $managedCatalogFile
  }
}
if ($applyTarget -and (Test-Path -LiteralPath $applyTarget -PathType Leaf)) {
  $rawCatalog = Get-Content -LiteralPath $applyTarget -Raw | ConvertFrom-Json
  $repaired = New-Object System.Collections.Generic.List[string]
  foreach ($override in $manifest.models) {
    $entries = @($rawCatalog.models | Where-Object { $_.slug -ceq $override.slug })
    if ($entries.Count -ne 1) {
      continue
    }
    $model = $entries[0]
    $needsRepair = $false
    foreach ($field in "context_window", "max_context_window", "auto_compact_token_limit") {
      if ($null -eq $model.$field -or [long]$model.$field -ne [long]$override.$field) {
        $needsRepair = $true
      }
    }
    if ($needsRepair) {
      $model.context_window = [long]$override.context_window
      $model.max_context_window = [long]$override.max_context_window
      $model.auto_compact_token_limit = [long]$override.auto_compact_token_limit
      $repaired.Add($override.slug)
    }
  }
  if ($repaired.Count -gt 0) {
    $json = ConvertTo-Json -InputObject $rawCatalog -Depth 100
    [System.IO.File]::WriteAllText($applyTarget, $json, [System.Text.UTF8Encoding]::new($false))
    "REPAIRED=$($repaired -join ',')"
    # Re-probe the effective catalog so the report reflects the repair.
    if ([string]::IsNullOrWhiteSpace($CatalogFile)) {
      $raw = $null
      $previousCodexHome2 = $env:CODEX_HOME
      try {
        $env:CODEX_HOME = $CodexHome
        $raw = & $codexCommand.Source debug models 2>$null
        if ($LASTEXITCODE -ne 0) {
          throw "codex debug models failed with exit code $LASTEXITCODE"
        }
      }
      finally {
        $env:CODEX_HOME = $previousCodexHome2
      }
      $catalog = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
    }
    else {
      $catalog = Get-Content -LiteralPath $CatalogFile -Raw | ConvertFrom-Json
    }
    $failures = New-Object System.Collections.Generic.List[string]
    $matched.Clear()
  }
}

foreach ($override in $manifest.models) {
  if (-not $enforcePins) {
    continue
  }
  $entries = @($catalog.models | Where-Object { $_.slug -ceq $override.slug })
  if ($entries.Count -eq 0) {
    $skipped.Add($override.slug)
    continue
  }
  if ($entries.Count -gt 1) {
    $failures.Add("$($override.slug): found $($entries.Count) catalog entries, expected 1")
    continue
  }
  $model = $entries[0]
  $modelOk = $true
  foreach ($field in "context_window", "max_context_window", "auto_compact_token_limit") {
    if ($null -eq $model.$field -or [long]$model.$field -ne [long]$override.$field) {
      $failures.Add("$($override.slug): $field catalog=$($model.$field) expected=$($override.$field)")
      $modelOk = $false
    }
  }
  if ($modelOk) {
    $matched.Add($override.slug)
  }
}

"LANE_CATALOG=$(if ($enforcePins) { 'cockpit-managed' } else { 'builtin' })"
"LANE_MODEL_COUNT=$(@($catalog.models).Count)"
"CONTEXT_MATCHED=$($matched -join ',')"
"CONTEXT_SKIPPED=$($skipped -join ',')"
foreach ($failure in $failures) {
  "CONTEXT_MISMATCH=$failure"
}
if ($failures.Count -gt 0) {
  "CONTEXT_VERIFY=FAIL"
  exit 1
}
"CONTEXT_VERIFY=PASS"

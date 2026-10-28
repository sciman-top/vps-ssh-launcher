#requires -Version 7.0
[CmdletBinding()]
param(
  [string]$CodexHome = "",
  [string]$CatalogFile = ""
)

$ErrorActionPreference = "Stop"

# Read-only verifier for the Codex desktop model catalog context pins.
#
# Pin enforcement is lane-conditional: it only applies when config.toml
# points at the Cockpit-managed catalog (cockpit-model-catalog.json). The
# native fast lane (cockpit-native-speed-models.json) ships the CLI bundled
# catalog verbatim and reports NOT_APPLICABLE; no pointer = builtin lane,
# pins skipped. This script only detects drift against
# config/codex-model-context-overrides.json. The former -Apply repair mode
  # was retired when experimental model management took over (16e547f added
  # it, 7a91b7d+ retired it; recover from git history if ever needed).

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
# cockpit 原生极速 (native fast) ships the CLI's bundled catalog as
# cockpit-native-speed-models.json. It is a speed-lane artifact, not a rename
# of the managed catalog: the two lanes are mutually exclusive by design.
$nativeSpeedCatalogFile = "cockpit-native-speed-models.json"
$catalogPointer = $null
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
  $configText = Get-Content -LiteralPath $configPath -Raw
  $pointerMatch = [regex]::Match($configText, '(?m)^\s*model_catalog_json\s*=\s*"([^"]+)"')
  if ($pointerMatch.Success) {
    $catalogPointer = $pointerMatch.Groups[1].Value.Trim()
  }
  if ($catalogPointer -eq "fq-model-context-catalog.json") {
    $failures.Add("config.toml pins the retired static catalog fq-model-context-catalog.json; lane-scoped catalogs require a Cockpit-managed model_catalog_json")
  }
  elseif ($null -ne $catalogPointer -and $catalogPointer -ne $managedCatalogFile -and $catalogPointer -ne $nativeSpeedCatalogFile) {
    $failures.Add("config.toml model_catalog_json points at '$catalogPointer'; expected the Cockpit-managed catalog ($managedCatalogFile), the native fast catalog ($nativeSpeedCatalogFile), or no pointer on built-in lanes")
  }
}
$nativeFastLane = $catalogPointer -eq $nativeSpeedCatalogFile
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
  $previousOutputEncoding = [Console]::OutputEncoding
  try {
    $env:CODEX_HOME = $CodexHome
    # codex emits UTF-8; decoding via the console codepage (e.g. GBK) eats
    # backslash escapes next to multi-byte sequences and corrupts the JSON.
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $raw = & $codexCommand.Source debug models 2>$null
    if ($LASTEXITCODE -ne 0) {
      throw "codex debug models failed with exit code $LASTEXITCODE"
    }
  }
  finally {
    $env:CODEX_HOME = $previousCodexHome
    [Console]::OutputEncoding = $previousOutputEncoding
  }
}
$catalog = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
if ($null -eq $catalog.models) {
  throw "codex debug models returned no models array"
}

if ($nativeFastLane) {
  # The native fast lane loads the CLI bundled catalog verbatim; per-model
  # context pins are structurally inapplicable on this lane, so pin drift
  # here is lane behavior, not machine drift.
  "LANE_CATALOG=native-speed-bundled"
  "LANE_MODEL_COUNT=$(@($catalog.models).Count)"
  "CONTEXT_SKIPPED=$(([string[]]($manifest.models | ForEach-Object { $_.slug })) -join ',')"
  "CONTEXT_NOTE=native fast lane: bundled catalog is authoritative; switch off ultrafast in Cockpit and relaunch via Cockpit to rebuild the managed catalog with pins"
  "CONTEXT_VERIFY=NOT_APPLICABLE"
  exit 0
}

$matched = New-Object System.Collections.Generic.List[string]
$skipped = New-Object System.Collections.Generic.List[string]
if (-not $enforcePins) {
  $skipped.AddRange([string[]]($manifest.models | ForEach-Object { $_.slug }))
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

#requires -Version 7.0
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$CodexHome = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($CodexHome)) {
  if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
    $CodexHome = $env:CODEX_HOME
  }
  else {
    $CodexHome = Join-Path $env:USERPROFILE ".codex"
  }
}
$CodexHome = [System.IO.Path]::GetFullPath($CodexHome)
$configPath = Join-Path $CodexHome "config.toml"
$catalogPath = Join-Path $CodexHome "fq-model-context-catalog.json"
$manifestPath = Join-Path $PSScriptRoot "..\config\codex-model-context-overrides.json"

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
  throw "Codex config not found: $configPath"
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
  throw "Model context manifest not found: $manifestPath"
}
$codexCommand = Get-Command codex -ErrorAction Stop
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or @($manifest.models).Count -eq 0) {
  throw "Unsupported or empty model context manifest: $manifestPath"
}

function Get-CodexCatalog {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$CodexHomePath
  )

  $previousCodexHome = $env:CODEX_HOME
  try {
    $env:CODEX_HOME = $CodexHomePath
    $raw = & $Executable debug models 2>$null
    if ($LASTEXITCODE -ne 0) {
      throw "codex debug models failed with exit code $LASTEXITCODE"
    }
    $document = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
    if ($null -eq $document.models) {
      throw "codex debug models returned no models array"
    }
    return $document
  }
  finally {
    $env:CODEX_HOME = $previousCodexHome
  }
}

function Get-TopLevelSettingLines {
  param([Parameter(Mandatory = $true)][string]$Text)

  $firstTable = [regex]::Match($Text, "(?m)^\s*\[[^\r\n]+\]\s*(?:#.*)?$")
  if ($firstTable.Success) {
    $topLevel = $Text.Substring(0, $firstTable.Index)
    $sections = $Text.Substring($firstTable.Index)
  }
  else {
    $topLevel = $Text
    $sections = ""
  }

  $pattern = "(?m)^\s*model_catalog_json\s*=.*$"
  $topLevelMatches = [regex]::Matches($topLevel, $pattern)
  $sectionMatches = [regex]::Matches($sections, $pattern)
  if ($sectionMatches.Count -gt 0) {
    throw "model_catalog_json appears under a TOML table; refusing ambiguous config"
  }
  if ($topLevelMatches.Count -gt 1) {
    throw "config.toml has duplicate top-level model_catalog_json settings"
  }

  return [pscustomobject]@{
    TopLevel = $topLevel
    Sections = $sections
    Matches = $topLevelMatches
  }
}

function Get-ActiveCatalogPointer {
  param([Parameter(Mandatory = $true)][string]$Text)

  $parts = Get-TopLevelSettingLines -Text $Text
  if ($parts.Matches.Count -eq 0) {
    return $null
  }
  $value = [regex]::Match($parts.Matches[0].Value, '=\s*"(?<path>[^"]+)"\s*(?:#.*)?$')
  if (-not $value.Success) {
    throw "model_catalog_json must be a quoted TOML path"
  }
  return $value.Groups["path"].Value
}

function Set-ActiveCatalogPointer {
  param([Parameter(Mandatory = $true)][string]$Text)

  $parts = Get-TopLevelSettingLines -Text $Text
  $setting = 'model_catalog_json = "fq-model-context-catalog.json"'
  if ($parts.Matches.Count -eq 1) {
    $oldLine = $parts.Matches[0].Value
    $comment = [regex]::Match($oldLine, '\s+#.*$').Value
    $replacement = $setting + $comment
    $updatedTop = [regex]::Replace($parts.TopLevel, "(?m)^\s*model_catalog_json\s*=.*$", $replacement, 1)
    return $updatedTop + $parts.Sections
  }

  $newline = if ($Text.Contains("`r`n")) { "`r`n" } else { "`n" }
  return $setting + $newline + $Text
}

function Test-ContextValues {
  param(
    [Parameter(Mandatory = $true)]$Catalog,
    [Parameter(Mandatory = $true)]$Overrides
  )

  foreach ($override in $Overrides) {
    $matches = @($Catalog.models | Where-Object { $_.slug -ceq $override.slug })
    if ($matches.Count -ne 1) {
      return $false
    }
    $model = $matches[0]
    foreach ($field in "context_window", "max_context_window", "auto_compact_token_limit") {
      if ($null -eq $model.$field -or [long]$model.$field -ne [long]$override.$field) {
        return $false
      }
    }
  }
  return $true
}

$catalog = Get-CodexCatalog -Executable $codexCommand.Source -CodexHomePath $CodexHome
$targetValuesAlreadyPresent = Test-ContextValues -Catalog $catalog -Overrides $manifest.models
foreach ($override in $manifest.models) {
  $matches = @($catalog.models | Where-Object { $_.slug -ceq $override.slug })
  if ($matches.Count -ne 1) {
    throw "Expected exactly one model '$($override.slug)' in the current Codex catalog; found $($matches.Count)"
  }
  $model = $matches[0]
  $model.context_window = [long]$override.context_window
  $model.max_context_window = [long]$override.max_context_window
  $model.auto_compact_token_limit = [long]$override.auto_compact_token_limit
}

$configText = Get-Content -LiteralPath $configPath -Raw
$activePointer = Get-ActiveCatalogPointer -Text $configText
$pointerResolvesToTarget = $false
if (-not [string]::IsNullOrWhiteSpace($activePointer)) {
  $activePath = if ([System.IO.Path]::IsPathRooted($activePointer)) {
    [System.IO.Path]::GetFullPath($activePointer)
  }
  else {
    [System.IO.Path]::GetFullPath((Join-Path $CodexHome $activePointer))
  }
  $pointerResolvesToTarget = $activePath -eq $catalogPath
}

$configNeedsWrite = -not $pointerResolvesToTarget
$catalogExists = Test-Path -LiteralPath $catalogPath -PathType Leaf
$catalogNeedsWrite = -not $pointerResolvesToTarget -or -not $catalogExists -or -not $targetValuesAlreadyPresent
$modelSummary = @($manifest.models | ForEach-Object { "{0}:{1}" -f $_.slug, $_.context_window }) -join ","
if (-not $configNeedsWrite -and -not $catalogNeedsWrite) {
  "SYNC_RESULT=ALREADY_CURRENT"
  "MODEL_CONTEXTS=$modelSummary"
  return
}

$updatedConfig = Set-ActiveCatalogPointer -Text $configText
$catalogJson = ConvertTo-Json -InputObject $catalog -Depth 100
$stamp = Get-Date -Format "yyyyMMddTHHmmssfff"
$configBackup = "$configPath.before-codex-model-context-$stamp.bak"
$catalogBackup = "$catalogPath.before-codex-model-context-$stamp.bak"

if (-not $PSCmdlet.ShouldProcess($CodexHome, "install model context catalog and pointer")) {
  "SYNC_RESULT=WHATIF"
  "WOULD_BACKUP_CONFIG=$configBackup"
  "WOULD_BACKUP_CATALOG=$catalogBackup"
  "MODEL_CONTEXTS=$modelSummary"
  return
}

$hadCatalog = Test-Path -LiteralPath $catalogPath -PathType Leaf
Copy-Item -LiteralPath $configPath -Destination $configBackup
if ($hadCatalog) {
  Copy-Item -LiteralPath $catalogPath -Destination $catalogBackup
}

try {
  [System.IO.File]::WriteAllText($catalogPath, $catalogJson, [System.Text.UTF8Encoding]::new($false))
  [System.IO.File]::WriteAllText($configPath, $updatedConfig, [System.Text.UTF8Encoding]::new($false))

  $readbackConfig = Get-Content -LiteralPath $configPath -Raw
  $readbackPointer = Get-ActiveCatalogPointer -Text $readbackConfig
  if ($readbackPointer -ne "fq-model-context-catalog.json") {
    throw "config.toml readback does not point to fq-model-context-catalog.json"
  }
  $verifiedCatalog = Get-CodexCatalog -Executable $codexCommand.Source -CodexHomePath $CodexHome
  if (-not (Test-ContextValues -Catalog $verifiedCatalog -Overrides $manifest.models)) {
    throw "Runtime catalog readback does not match the model context manifest"
  }
}
catch {
  [System.IO.File]::WriteAllBytes($configPath, [System.IO.File]::ReadAllBytes($configBackup))
  if ($hadCatalog) {
    [System.IO.File]::WriteAllBytes($catalogPath, [System.IO.File]::ReadAllBytes($catalogBackup))
  }
  elseif (Test-Path -LiteralPath $catalogPath -PathType Leaf) {
    Remove-Item -LiteralPath $catalogPath
  }
  throw
}

"SYNC_RESULT=PASS"
"CONFIG_BACKUP=$configBackup"
if ($hadCatalog) {
  "CATALOG_BACKUP=$catalogBackup"
}
"MODEL_CONTEXTS=$modelSummary"
"NEXT_STEP=restart ChatGPT Desktop to reload the catalog"

#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [string]$MasqueradeUrl,
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

Initialize-WindowsProcessEnvironment
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}
if (-not $MasqueradeUrl) {
  $MasqueradeUrl = @{
    "bwg" = "https://fq.sciman.top:33387/"
    "zz"  = "https://zz.sciman.top:12378/"
  }[$Profile]
}
if ($MasqueradeUrl -notmatch '^https://[A-Za-z0-9.:/_-]+$') {
  throw "-MasqueradeUrl must be an https:// URL literal without shell syntax."
}

# This is not a second implementation of the hardening lanes. It runs the
# same gate and delegates each reported lane to its existing, locked, backup-
# first entrypoint. Recovery is opt-in and currently scoped to ZZ because its
# existing scheduled task is the one that had a drift-only, no-recovery gap.
$gateOutput = @(& pwsh -NoProfile -ExecutionPolicy Bypass `
    -File (Join-Path $PSScriptRoot "proxy_hardening_drift.ps1") `
    -Profile $Profile -Config $Config -MasqueradeUrl $MasqueradeUrl 2>&1)
$gateExit = $LASTEXITCODE
$gateOutput | ForEach-Object { [Console]::WriteLine([string]$_) }
$verdict = @($gateOutput | Where-Object { ([string]$_).StartsWith("HARDENING_DRIFT=") })
$verdictLine = if ($verdict.Count) { [string]$verdict[-1] } else { "HARDENING_DRIFT=unknown" }
if ($gateExit -eq 0 -and $verdictLine -eq "HARDENING_DRIFT=NONE") {
  Write-Output "RECOVERY_RESULT=NONE"
  exit 0
}
if ($gateExit -ne 3 -or -not $verdictLine.StartsWith("HARDENING_DRIFT=")) {
  throw "Hardening gate could not produce an actionable drift list: $verdictLine"
}
if (-not $Apply) {
  Write-Output "RECOVERY_RESULT=PLAN drift=$($verdictLine.Substring(16))"
  Write-Output "RECOVERY_APPLY_REQUIRED"
  exit 0
}
if ($Profile -ne "zz") {
  throw "Automatic hardening recovery is currently restricted to zz."
}

$driftNames = @($verdictLine.Substring(16).Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$laneScripts = [ordered]@{
  "camouflage"           = @("nginx_camouflage_site.ps1")
  "reality-shortid"      = @("reality_shortid_harden.ps1")
  "reality-target"       = @()
  "reality-alt-sni"      = @("reality_alt_sni_lane.ps1")
  "nginx-stealth"        = @("nginx_tls_stealth.ps1")
  "xhttp-front"          = @("xhttp_nginx_front.ps1")
  "xray-port-collision"  = @()
  "hysteria2-obfs"       = @("hysteria2_obfs_lane.ps1")
  "hysteria2-masquerade" = @("hysteria2_masquerade_lane.ps1")
  "cdn-fallback"         = @()
  "merged-sub"           = @("merged_subscription_lane.ps1", "zz_subscription_republish.ps1")
  "merged-sub-fetch"     = @("merged_subscription_lane.ps1")
  "vasma-sub"            = @("zz_subscription_republish.ps1")
  "tls-expiry"           = @()
  "stale-firewall"       = @("stale_firewall_hygiene.ps1")
}
$unknown = @($driftNames | Where-Object { -not $laneScripts.Contains($_) })
if ($unknown.Count -gt 0) {
  throw "Unknown hardening drift lane(s): $($unknown -join ','). No recovery was attempted."
}
$unmapped = @($driftNames | Where-Object { $laneScripts[$_].Count -eq 0 })
if ($unmapped.Count -gt 0) {
  throw "Drift lane(s) require operator decisions, not blind lane replay: $($unmapped -join ','). No recovery was attempted."
}

$executed = @()
foreach ($name in $driftNames) {
  foreach ($scriptName in $laneScripts[$name]) {
    if ($executed -contains $scriptName) { continue }
    $scriptPath = Join-Path $PSScriptRoot $scriptName
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
      throw "Recovery lane script missing: $scriptName"
    }
    $arguments = @(
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $scriptPath,
      "-Profile", $Profile, "-Config", $Config, "-Apply"
    )
    if ($scriptName -eq "hysteria2_obfs_lane.ps1") {
      $passwordFile = Join-Path (Split-Path -Parent $Config) "$Profile-hysteria2-obfs-password.txt"
      if (-not (Test-Path -LiteralPath $passwordFile -PathType Leaf)) {
        throw "Obfs password file missing for jitter-safe replay: $passwordFile"
      }
      $arguments += @("-ObfsPassword", (Get-Content -LiteralPath $passwordFile -Raw).Trim())
    }
    if ($scriptName -in @("hysteria2_masquerade_lane.ps1")) {
      $arguments += @("-MasqueradeUrl", $MasqueradeUrl)
    }
    Write-Output "RECOVERY_LANE=$name SCRIPT=$scriptName"
    & pwsh @arguments
    if ($LASTEXITCODE -ne 0) {
      throw "Recovery lane failed: $scriptName"
    }
    $executed += $scriptName
  }
}

$postOutput = @(& pwsh -NoProfile -ExecutionPolicy Bypass `
    -File (Join-Path $PSScriptRoot "proxy_hardening_drift.ps1") `
    -Profile $Profile -Config $Config -MasqueradeUrl $MasqueradeUrl 2>&1)
$postExit = $LASTEXITCODE
$postOutput | ForEach-Object { [Console]::WriteLine([string]$_) }
$postVerdict = @($postOutput | Where-Object { ([string]$_).StartsWith("HARDENING_DRIFT=") })
$postLine = if ($postVerdict.Count) { [string]$postVerdict[-1] } else { "HARDENING_DRIFT=unknown" }
if ($postExit -ne 0 -or $postLine -ne "HARDENING_DRIFT=NONE") {
  throw "Recovery did not restore a clean hardening gate: $postLine"
}
Write-Output "RECOVERY_RESULT=RECOVERED lanes=$($driftNames -join ',')"

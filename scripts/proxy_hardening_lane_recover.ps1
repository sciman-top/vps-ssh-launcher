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
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

Initialize-LaneScript -ScriptRoot $PSScriptRoot -Config $Config -AllowPyLauncher
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
$verdictLine = Get-LauncherVerdictLine -Output $gateOutput -Prefix "HARDENING_DRIFT=" -Fallback "HARDENING_DRIFT=unknown"
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
  # Vision flow is a per-user vless field vasma rewrites from its menu answer;
  # no lane owns that field, so restoring it is a controlled vasma/JSON edit
  # plus `xray run -test`, never a blind replay.
  "reality-flow"         = @()
  "reality-alt-sni"      = @("reality_alt_sni_lane.ps1")
  "nginx-stealth"        = @("nginx_tls_stealth.ps1")
  # zz-only recover: the zz XHTTP front has its own projector; replaying the
  # bwg one here would overwrite zz's front with the bwg template and
  # permanently bypass the zz variant's applied checks.
  "xhttp-front"          = @("zz_xhttp_nginx_front.ps1")
  "xray-port-collision"  = @()
  "hysteria2-obfs"       = @("hysteria2_obfs_lane.ps1")
  "hysteria2-masquerade" = @("hysteria2_masquerade_lane.ps1")
  "cdn-fallback"         = @()
  "merged-sub"           = @("merged_subscription_lane.ps1", "zz_subscription_republish.ps1")
  "merged-sub-fetch"     = @("merged_subscription_lane.ps1")
  "vasma-sub"            = @("zz_subscription_republish.ps1")
  "tls-expiry"           = @()
  "stale-firewall"       = @("stale_firewall_hygiene.ps1")
  # zz's xray lane is manually managed (no projector); domain-list
  # convergence is a controlled edit, never a blind lane replay.
  "google-ipv4"          = @()
  # Recovery needs the pin-bearing projector -Apply (fresh InstallSha256),
  # which this dispatcher cannot supply; treat it like an operator decision.
  "v2ray-updater"        = @()
  "firewall-baseline"    = @("firewall_baseline.ps1")
  # CVE-2026-98432 still has no fixed noble kernel (2026-11-10): the accepted
  # posture is the MPTCP controls plus the guard firewall's IP-options drop,
  # which firewall-baseline already replays. A kernel update remains an
  # operator decision with a controlled reboot, never a blind lane replay.
  "host-security"        = @()
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
      # Replay stays fail-closed on the separated v2 password file; the v1
      # key-reuse fallback is a read-path convenience, never a replay input.
      $passwordFile = Resolve-LaneObfsPasswordFile -ConfigPath $Config -Profile $Profile -SeparatedOnly
      if ($null -eq $passwordFile) {
        throw "Separated obfs password file missing for replay. Run hysteria2_obfs_lane.ps1 -Apply to migrate key reuse."
      }
      $arguments += @("-ObfsPasswordFile", $passwordFile)
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
$postLine = Get-LauncherVerdictLine -Output $postOutput -Prefix "HARDENING_DRIFT=" -Fallback "HARDENING_DRIFT=unknown"
if ($postExit -ne 0 -or $postLine -ne "HARDENING_DRIFT=NONE") {
  throw "Recovery did not restore a clean hardening gate: $postLine"
}
Write-Output "RECOVERY_RESULT=RECOVERED lanes=$($driftNames -join ',')"

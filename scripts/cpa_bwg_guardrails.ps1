#requires -Version 7
param(
  [string]$Profile = "bwg",
  [string]$Config = "",
  [switch]$Apply,
  [switch]$Observe,
  [switch]$RotatePath,
  [switch]$DeactivateOAuthLuna,
  [switch]$QuarantineOAuthLuna,
  [switch]$RestoreOAuthLuna,
  [string]$ProviderEnvPath = ""
)

$ErrorActionPreference = "Stop"

if ($Profile -ne "bwg") {
  throw "This guardrail workflow is intentionally limited to the bwg profile."
}
if (@($Apply, $Observe, $RotatePath, $DeactivateOAuthLuna, $QuarantineOAuthLuna, $RestoreOAuthLuna | Where-Object { $_ }).Count -gt 1) {
  throw "Choose exactly one of the default strict doctor, -Observe, -Apply, -RotatePath, -DeactivateOAuthLuna, -QuarantineOAuthLuna, or -RestoreOAuthLuna."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$connectScript = Join-Path $repoRoot "connect.ps1"
$updaterPath = Join-Path $scriptDir "remote\cpa-auto-update.sh"
$healthPath = Join-Path $scriptDir "remote\cpa-health.py"
$policyPath = Join-Path $scriptDir "remote\cpa_policy.py"
$providerRoutesPath = Join-Path $scriptDir "remote\cpa_provider_routes.json"
$admissionPath = Join-Path $scriptDir "remote\cpa-admission.py"
$admissionConfigPath = Join-Path $scriptDir "remote\cpa-admission.json"
$admissionUnitPath = Join-Path $scriptDir "remote\cpa-admission.service"
$admissionIntegrityCheckPath = Join-Path $scriptDir "remote\cpa-admission-integrity-check.sh"
$admissionIntegrityDropinPath = Join-Path $scriptDir "remote\cpa-admission-integrity.conf"
$admissionIntegrityPinPath = Join-Path $scriptDir "remote\cpa-admission-integrity-pin.txt"
$fail2banFilterPath = Join-Path $scriptDir "remote\cpa-fail2ban-filter.conf"
$fail2banJailPath = Join-Path $scriptDir "remote\cpa-fail2ban-jail.conf"

if (-not (Test-Path -LiteralPath $connectScript -PathType Leaf)) {
  throw "connect.ps1 was not found at $connectScript"
}
if (-not (Test-Path -LiteralPath $updaterPath -PathType Leaf)) {
  throw "CPA updater source was not found at $updaterPath"
}
if (-not (Test-Path -LiteralPath $healthPath -PathType Leaf)) {
  throw "CPA health source was not found at $healthPath"
}
if (-not (Test-Path -LiteralPath $policyPath -PathType Leaf)) {
  throw "CPA policy source was not found at $policyPath"
}
if (-not (Test-Path -LiteralPath $providerRoutesPath -PathType Leaf)) {
  throw "CPA provider route source was not found at $providerRoutesPath"
}
if (-not (Test-Path -LiteralPath $admissionPath -PathType Leaf)) {
  throw "CPA admission source was not found at $admissionPath"
}
if (-not (Test-Path -LiteralPath $admissionConfigPath -PathType Leaf)) {
  throw "CPA admission config was not found at $admissionConfigPath"
}
if (-not (Test-Path -LiteralPath $admissionUnitPath -PathType Leaf)) {
  throw "CPA admission unit was not found at $admissionUnitPath"
}
if (-not (Test-Path -LiteralPath $admissionIntegrityCheckPath -PathType Leaf)) {
  throw "CPA admission integrity checker was not found at $admissionIntegrityCheckPath"
}
if (-not (Test-Path -LiteralPath $admissionIntegrityDropinPath -PathType Leaf)) {
  throw "CPA admission integrity drop-in was not found at $admissionIntegrityDropinPath"
}
if (-not (Test-Path -LiteralPath $admissionIntegrityPinPath -PathType Leaf)) {
  throw "CPA admission integrity pin was not found at $admissionIntegrityPinPath"
}
if (-not (Test-Path -LiteralPath $fail2banFilterPath -PathType Leaf)) {
  throw "CPA fail2ban filter source was not found at $fail2banFilterPath"
}
if (-not (Test-Path -LiteralPath $fail2banJailPath -PathType Leaf)) {
  throw "CPA fail2ban jail source was not found at $fail2banJailPath"
}

function Get-LfNormalizedSha256 {
  param([Parameter(Mandatory = $true)][string]$Text)

  # Hashes must cover exactly the bytes that reach the remote file: the
  # LF-normalized UTF-8 payload, not the CRLF working-copy text.
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
    return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  }
  finally {
    $sha.Dispose()
  }
}

# Capture one generation of each payload before computing drift hashes.
$guardrailTemplates = [ordered]@{
  "doctor" = "scripts/remote/cpa-guardrail-doctor.sh"
  "rotate-path" = "scripts/remote/cpa-guardrail-rotate-path.sh"
  "deactivate-oauth-luna" = "scripts/remote/cpa-guardrail-deactivate-oauth-luna.sh"
  "quarantine-oauth-luna" = "scripts/remote/cpa-guardrail-quarantine-oauth-luna.sh"
  "apply" = "scripts/remote/cpa-guardrail-apply.sh"
}
$guardrailTemplateTexts = @{}
foreach ($entry in $guardrailTemplates.GetEnumerator()) {
  $path = Join-Path $repoRoot $entry.Value
  $text = (Get-Content -LiteralPath $path -Raw -Encoding utf8).Replace("`r`n", "`n").Replace("`r", "`n")
  if ([string]::IsNullOrWhiteSpace($text)) {
    throw "CPA guardrail template is empty: $($entry.Value)"
  }
  $guardrailTemplateTexts[$entry.Key] = $text
}

function Get-CpaGuardrailTemplate {
  param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("doctor", "rotate-path", "deactivate-oauth-luna", "quarantine-oauth-luna", "apply")]
    [string]$Name
  )
  # Files have a final LF; the former literal here-string did not. Preserve
  # the exact command text so this extraction has no remote behavior change.
  $text = $guardrailTemplateTexts[$Name]
  if ($text.EndsWith("`n")) {
    return $text.Substring(0, $text.Length - 1)
  }
  return $text
}

$fail2banFilterText = (Get-Content -LiteralPath $fail2banFilterPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$fail2banFilterBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($fail2banFilterText))
$fail2banFilterSha256 = Get-LfNormalizedSha256 -Text $fail2banFilterText
$fail2banJailText = (Get-Content -LiteralPath $fail2banJailPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
# Fail closed on the source of truth, not just on the deployed copy: nginx
# reaches CPA over 127.0.0.1:8317, so a projected jail without the loopback
# exemption would let the doctor's own 401 probes accumulate to maxretry and ban
# 127.0.0.1, cutting the gateway off from its upstream.
if ($fail2banJailText -notmatch '(?m)^ignoreip = 127\.0\.0\.1/8 ::1$') {
  throw "cpa-fail2ban-jail.conf must keep 'ignoreip = 127.0.0.1/8 ::1' so loopback can never be self-banned."
}
$fail2banJailBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($fail2banJailText))
$fail2banJailSha256 = Get-LfNormalizedSha256 -Text $fail2banJailText
$updaterText = (Get-Content -LiteralPath $updaterPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$updaterBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($updaterText))
$updaterSha256 = Get-LfNormalizedSha256 -Text $updaterText
$healthText = (Get-Content -LiteralPath $healthPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$healthBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($healthText))
$healthSha256 = Get-LfNormalizedSha256 -Text $healthText
$policyText = (Get-Content -LiteralPath $policyPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$policyBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($policyText))
$policySha256 = Get-LfNormalizedSha256 -Text $policyText
$providerRoutesText = (Get-Content -LiteralPath $providerRoutesPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$providerRoutes = $providerRoutesText | ConvertFrom-Json
$providerRoutesBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($providerRoutesText)
)
$providerRoutesSha256 = Get-LfNormalizedSha256 -Text $providerRoutesText
$admissionText = (Get-Content -LiteralPath $admissionPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$admissionBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionText))
$admissionSha256 = Get-LfNormalizedSha256 -Text $admissionText
$admissionConfigText = (Get-Content -LiteralPath $admissionConfigPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$admissionConfigBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionConfigText))
$admissionConfigSha256 = Get-LfNormalizedSha256 -Text $admissionConfigText
$admissionUnitText = (Get-Content -LiteralPath $admissionUnitPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$admissionUnitBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionUnitText))
$admissionUnitSha256 = Get-LfNormalizedSha256 -Text $admissionUnitText
$admissionIntegrityCheckText = (Get-Content -LiteralPath $admissionIntegrityCheckPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$admissionIntegrityCheckBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionIntegrityCheckText))
$admissionIntegrityCheckSha256 = Get-LfNormalizedSha256 -Text $admissionIntegrityCheckText
$admissionIntegrityDropinText = (Get-Content -LiteralPath $admissionIntegrityDropinPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$admissionIntegrityDropinBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionIntegrityDropinText))
$admissionIntegrityDropinSha256 = Get-LfNormalizedSha256 -Text $admissionIntegrityDropinText
$admissionIntegrityPinText = (Get-Content -LiteralPath $admissionIntegrityPinPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$admissionIntegrityPinBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionIntegrityPinText))
$admissionIntegrityPinSha256 = Get-LfNormalizedSha256 -Text $admissionIntegrityPinText

$projectionSourcePaths = @(
  "connect.ps1",
  "scripts/cpa_bwg_guardrails.ps1",
  "scripts/remote/cpa-auto-update.sh",
  "scripts/remote/cpa-health.py",
  "scripts/remote/cpa_policy.py",
  "scripts/remote/cpa_provider_routes.json",
  "scripts/remote/cpa-admission.py",
  "scripts/remote/cpa-admission.json",
  "scripts/remote/cpa-admission.service",
  "scripts/remote/cpa-admission-integrity-check.sh",
  "scripts/remote/cpa-admission-integrity.conf",
  "scripts/remote/cpa-admission-integrity-pin.txt",
  "scripts/remote/cpa-fail2ban-filter.conf",
  "scripts/remote/cpa-fail2ban-jail.conf"
)
$projectionSourceHashes = [ordered]@{
  "connect.ps1" = Get-LfNormalizedSha256 -Text ((Get-Content -LiteralPath $connectScript -Raw).Replace("`r`n", "`n").Replace("`r", "`n"))
  "scripts/cpa_bwg_guardrails.ps1" = Get-LfNormalizedSha256 -Text ((Get-Content -LiteralPath $PSCommandPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n"))
  "scripts/remote/cpa-auto-update.sh" = $updaterSha256
  "scripts/remote/cpa-health.py" = $healthSha256
  "scripts/remote/cpa_policy.py" = $policySha256
  "scripts/remote/cpa_provider_routes.json" = $providerRoutesSha256
  "scripts/remote/cpa-admission.py" = $admissionSha256
  "scripts/remote/cpa-admission.json" = $admissionConfigSha256
  "scripts/remote/cpa-admission.service" = $admissionUnitSha256
  "scripts/remote/cpa-admission-integrity-check.sh" = $admissionIntegrityCheckSha256
  "scripts/remote/cpa-admission-integrity.conf" = $admissionIntegrityDropinSha256
  "scripts/remote/cpa-admission-integrity-pin.txt" = $admissionIntegrityPinSha256
  "scripts/remote/cpa-fail2ban-filter.conf" = $fail2banFilterSha256
  "scripts/remote/cpa-fail2ban-jail.conf" = $fail2banJailSha256
}

foreach ($entry in $guardrailTemplates.GetEnumerator()) {
  $projectionSourcePaths += $entry.Value
  $projectionSourceHashes[$entry.Value] = Get-LfNormalizedSha256 -Text $guardrailTemplateTexts[$entry.Key]
}

function Assert-ProjectionSourcesUnchanged {
  $status = @(git -C $repoRoot status --porcelain=v1 --untracked-files=all -- $projectionSourcePaths 2>&1)
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to verify CPA projection source status."
  }
  if ($status.Count -gt 0) {
    throw "CPA Apply requires a clean projection source set; review git status before projecting."
  }
  foreach ($entry in $projectionSourceHashes.GetEnumerator()) {
    $path = Join-Path $repoRoot ($entry.Key -replace '/', '\')
    $text = (Get-Content -LiteralPath $path -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
    $actual = Get-LfNormalizedSha256 -Text $text
    if ($actual -ne $entry.Value) {
      throw "CPA projection source changed during preparation: $($entry.Key)"
    }
  }
}

if ($Apply) {
  Assert-ProjectionSourcesUnchanged
}

function Get-HeadBlobSha256 {
  param([Parameter(Mandatory = $true)][string]$RepoRelativePath)

  # Doctor compares the committed source of truth (HEAD blob, LF as stored in
  # git) against the deployed files. Anchoring to HEAD instead of the working
  # tree keeps uncommitted parallel-session edits from turning into doctor
  # failures; an -Apply projection still verifies the exact working-tree bytes
  # it writes via write_base64_file.
  $tmp = [IO.Path]::GetTempFileName()
  try {
    $gitPath = "HEAD:$($RepoRelativePath -replace '\\', '/')"
    $proc = Start-Process -FilePath "git" `
      -ArgumentList @("-C", $repoRoot, "show", $gitPath) `
      -RedirectStandardOutput $tmp -NoNewWindow -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
      throw "git show $gitPath failed with exit code $($proc.ExitCode)."
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
      return ([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($tmp)))).Replace("-", "").ToLowerInvariant()
    }
    finally {
      $sha.Dispose()
    }
  }
  finally {
    Remove-Item -LiteralPath $tmp -Force
  }
}

$updaterHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-auto-update.sh"
$healthHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-health.py"
$policyHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa_policy.py"
$providerRoutesHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa_provider_routes.json"
$admissionHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission.py"
$admissionConfigHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission.json"
$admissionUnitHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission.service"
$admissionIntegrityCheckHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission-integrity-check.sh"
$admissionIntegrityDropinHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission-integrity.conf"
$admissionIntegrityPinHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission-integrity-pin.txt"
$fail2banFilterHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-fail2ban-filter.conf"
$fail2banJailHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-fail2ban-jail.conf"

$projectionHashPairs = @(
  "/opt/cliproxyapi/auto-update.sh=$updaterHeadSha256",
  "/opt/cliproxyapi/cpa-health.py=$healthHeadSha256",
  "/opt/cliproxyapi/cpa_policy.py=$policyHeadSha256",
  "/opt/cliproxyapi/cpa_provider_routes.json=$providerRoutesHeadSha256",
  "/opt/cliproxyapi/cpa-admission.py=$admissionHeadSha256",
  "/opt/cliproxyapi/cpa-admission.json=$admissionConfigHeadSha256",
  "/etc/systemd/system/cpa-admission.service=$admissionUnitHeadSha256",
  "/usr/local/libexec/cpa-admission-integrity-check=$admissionIntegrityCheckHeadSha256",
  "/etc/systemd/system/cpa-admission.service.d/10-integrity.conf=$admissionIntegrityDropinHeadSha256",
  "/etc/vps-ssh-launcher/cpa-admission.sha256=$admissionIntegrityPinHeadSha256",
  "/etc/fail2ban/filter.d/cpa-gateway.conf=$fail2banFilterHeadSha256",
  "/etc/fail2ban/jail.d/cpa-gateway.conf=$fail2banJailHeadSha256"
) -join " "
if (($projectionHashPairs -split ' ') | Where-Object { $_ -notmatch '^/[A-Za-z0-9._/-]+=[0-9a-f]{64}$' }) {
  throw "Projection hash pair list failed its injection format check."
}

function Invoke-BwgRemoteScript {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Script,
    [int]$CommandTimeout = 180
  )

  # gitattributes checks *.ps1 out as CRLF; real Linux bash rejects CR in the
  # payload (e.g. "func() {<CR>" is a syntax error), so embedded scripts must
  # be projected LF-only.
  $Script = $Script.Replace("`r`n", "`n").Replace("`r", "`n")
  $payload = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($Script)
  )
  $invoke = {
    param([string]$RemoteCommand)
    & $connectScript `
      -Config $Config `
      -Profile $Profile `
      -StrictHostKeyChecking `
      -Command $RemoteCommand `
      -CommandTimeout $CommandTimeout
    if ($LASTEXITCODE -ne 0) {
      throw "Remote BWG command failed with exit code $LASTEXITCODE."
    }
  }

  # Passing a full Base64 script as one Win32 command-line argument eventually
  # exceeds CreateProcess's limit. Keep normal doctor calls single-shot, while
  # projecting larger guarded scripts through a mode-600 remote temp file.
  if ($Apply) {
    # Re-read the exact source set immediately before the SSH call. The
    # payload and its HEAD-anchored doctor hashes must describe one stable
    # generation, even if another local process edits the worktree while this
    # script is preparing a long command.
    Assert-ProjectionSourcesUnchanged
  }
  $chunkSize = 12000
  if ($payload.Length -le $chunkSize) {
    & $invoke "printf %s $payload | base64 -d | bash"
    return
  }

  $remoteTemp = "/tmp/cpa-guardrails-$([guid]::NewGuid().ToString('N')).b64"
  $tempCreated = $false
  try {
    & $invoke ("umask 077; : > '$remoteTemp'; chmod 600 '$remoteTemp'")
    $tempCreated = $true
    for ($offset = 0; $offset -lt $payload.Length; $offset += $chunkSize) {
      $length = [Math]::Min($chunkSize, $payload.Length - $offset)
      $chunk = $payload.Substring($offset, $length)
      & $invoke ("printf %s '$chunk' >> '$remoteTemp'")
      # Pace the chunks so a burst of SSH channels cannot trip server-side
      # rate limits or crowd out interactive sessions mid-transfer.
      Start-Sleep -Milliseconds 100
    }
    # pipefail's $? is the rightmost nonzero pipeline status, so a corrupted
    # payload fails the decoder check AND a failing remote script keeps its
    # exit code. PIPESTATUS cannot be read across two assignments here: each
    # assignment resets the array.
    & $invoke (
      "set -o pipefail; base64 -d -- '$remoteTemp' | bash; " +
      "rc=`$?; rm -f -- '$remoteTemp'; exit `$rc"
    )
    $tempCreated = $false
  }
  finally {
    if ($tempCreated) {
      try {
        & $invoke "rm -f -- '$remoteTemp'"
      }
      catch {
        Write-Warning "Failed to remove remote CPA guardrail temp file."
      }
    }
  }
}

$doctorScript = Get-CpaGuardrailTemplate -Name "doctor"

if ($Observe) {
  $doctorScript = $doctorScript.Replace("STRICT=1", "STRICT=0")
}

if (-not $Apply -and -not $RotatePath -and -not $DeactivateOAuthLuna -and
    -not $QuarantineOAuthLuna -and -not $RestoreOAuthLuna) {
  $doctorScript = $doctorScript.Replace("__CPA_PROJECTION_HASH_PAIRS__", $projectionHashPairs).Replace(
    "__CPA_ADMISSION_PIN_VALUE__",
    $admissionIntegrityPinText.Trim()
  )
  Invoke-BwgRemoteScript -Script $doctorScript
  exit 0
}

$rotateScript = Get-CpaGuardrailTemplate -Name "rotate-path"

if ($RotatePath) {
  Invoke-BwgRemoteScript -Script $rotateScript -CommandTimeout 180
  exit 0
}

$deactivateOAuthLunaScript = Get-CpaGuardrailTemplate -Name "deactivate-oauth-luna"

if ($DeactivateOAuthLuna) {
  $retireScript = $deactivateOAuthLunaScript.Replace(
    "__CPA_OAUTH_RETIRE_MANIFEST_B64__",
    $providerRoutesBase64
  )
  Invoke-BwgRemoteScript -Script $retireScript -CommandTimeout 240
  exit 0
}

$quarantineScript = Get-CpaGuardrailTemplate -Name "quarantine-oauth-luna"

if ($QuarantineOAuthLuna -or $RestoreOAuthLuna) {
  $quarantineMode = if ($QuarantineOAuthLuna) { "quarantine" } else { "restore" }
  $quarantineScript = $quarantineScript.Replace(
    "__CPA_OAUTH_QUARANTINE_MODE__",
    $quarantineMode
  ).Replace(
    "__CPA_OAUTH_QUARANTINE_MANIFEST_B64__",
    $providerRoutesBase64
  )
  Invoke-BwgRemoteScript -Script $quarantineScript -CommandTimeout 240
  exit 0
}

if ([string]::IsNullOrWhiteSpace($ProviderEnvPath)) {
  # Provider credentials live in the user profile, mirroring target.json;
  # the repo root holds no private env file.
  $ProviderEnvPath = Join-Path $env:APPDATA "vps-ssh-launcher\providers.env"
}
if (-not (Test-Path -LiteralPath $ProviderEnvPath -PathType Leaf)) {
  throw "Provider env source was not found at $ProviderEnvPath"
}
$providerSlots = @($providerRoutes.providers | ForEach-Object { [int]$_.slot })
$providerSlotSet = @{}
foreach ($slot in $providerSlots) {
  $providerSlotSet[[string]$slot] = $true
}
$providerEnvLines = [System.Collections.Generic.List[string]]::new()
foreach ($line in Get-Content -LiteralPath $ProviderEnvPath) {
  if ($line -match '^\s*(?:export\s+)?(?:BASE_URL|API_KEY)_(\d+)\s*=') {
    if ($providerSlotSet.ContainsKey($matches[1])) {
      $providerEnvLines.Add($line.Trim())
    }
  }
}
$providerEnvText = $providerEnvLines -join "`n"
$providerEnvBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($providerEnvText)
)
$applyScript = Get-CpaGuardrailTemplate -Name "apply"

$applyScript = $applyScript.Replace(
  "__CPA_PROVIDER_ENV_B64__",
  $providerEnvBase64
).Replace(
  "__CPA_PROVIDER_ROUTES_B64__",
  $providerRoutesBase64
).Replace(
  "__CPA_PROVIDER_ROUTES_SHA256__",
  $providerRoutesSha256
).Replace(
  "__CPA_FAIL2BAN_FILTER_B64__",
  $fail2banFilterBase64
).Replace(
  "__CPA_FAIL2BAN_FILTER_SHA256__",
  $fail2banFilterSha256
).Replace(
  "__CPA_FAIL2BAN_JAIL_B64__",
  $fail2banJailBase64
).Replace(
  "__CPA_FAIL2BAN_JAIL_SHA256__",
  $fail2banJailSha256
).Replace(
  "__CPA_UPDATER_B64__",
  $updaterBase64
).Replace(
  "__CPA_UPDATER_SHA256__",
  $updaterSha256
).Replace(
  "__CPA_HEALTH_B64__",
  $healthBase64
).Replace(
  "__CPA_HEALTH_SHA256__",
  $healthSha256
).Replace(
  "__CPA_POLICY_B64__",
  $policyBase64
).Replace(
  "__CPA_POLICY_SHA256__",
  $policySha256
).Replace(
  "__CPA_ADMISSION_B64__",
  $admissionBase64
).Replace(
  "__CPA_ADMISSION_SHA256__",
  $admissionSha256
).Replace(
  "__CPA_ADMISSION_CONFIG_B64__",
  $admissionConfigBase64
).Replace(
  "__CPA_ADMISSION_CONFIG_SHA256__",
  $admissionConfigSha256
).Replace(
  "__CPA_ADMISSION_UNIT_B64__",
  $admissionUnitBase64
).Replace(
  "__CPA_ADMISSION_UNIT_SHA256__",
  $admissionUnitSha256
).Replace(
  "__CPA_ADMISSION_INTEGRITY_CHECK_B64__",
  $admissionIntegrityCheckBase64
).Replace(
  "__CPA_ADMISSION_INTEGRITY_CHECK_SHA256__",
  $admissionIntegrityCheckSha256
).Replace(
  "__CPA_ADMISSION_INTEGRITY_DROPIN_B64__",
  $admissionIntegrityDropinBase64
).Replace(
  "__CPA_ADMISSION_INTEGRITY_DROPIN_SHA256__",
  $admissionIntegrityDropinSha256
).Replace(
  "__CPA_ADMISSION_INTEGRITY_PIN_B64__",
  $admissionIntegrityPinBase64
).Replace(
  "__CPA_ADMISSION_INTEGRITY_PIN_SHA256__",
  $admissionIntegrityPinSha256
)
Invoke-BwgRemoteScript -Script $applyScript -CommandTimeout 240

#requires -Version 7
param(
  [string]$Profile = "bwg",
  [string]$Config = "",
  [switch]$Apply,
  [switch]$Observe,
  [switch]$AuditProjection,
  [switch]$RotatePath,
  [switch]$DeactivateOAuthLuna,
  [switch]$QuarantineOAuthLuna,
  [switch]$RestoreOAuthLuna,
  [switch]$QuarantineAntigravity,
  [switch]$RestoreAntigravity,
  [switch]$QuarantineClaudeOAuth,
  # Exercise the shared remote transport without SSH. The caller supplies a
  # mock only for the launcher process boundary; no endpoint is inferred.
  [string]$LauncherEntryPointOverride = "",
  [string]$QuarantineUserDirective = "",
  [switch]$RestoreClaudeOAuth,
  [string]$ProviderEnvPath = ""
)

$ErrorActionPreference = "Stop"

if ($Profile -ne "bwg") {
  throw "This guardrail workflow is intentionally limited to the bwg profile."
}
if ($AuditProjection -and @($Apply, $Observe, $RotatePath, $DeactivateOAuthLuna, $QuarantineOAuthLuna, $RestoreOAuthLuna, $QuarantineAntigravity, $RestoreAntigravity, $QuarantineClaudeOAuth, $RestoreClaudeOAuth | Where-Object { $_ }).Count -gt 0) {
  throw "REFUSE -AuditProjection must be used alone."
}
if (@($Apply, $Observe, $AuditProjection, $RotatePath, $DeactivateOAuthLuna, $QuarantineOAuthLuna, $RestoreOAuthLuna, $QuarantineAntigravity, $RestoreAntigravity, $QuarantineClaudeOAuth, $RestoreClaudeOAuth | Where-Object { $_ }).Count -gt 1) {
  throw "Choose exactly one action: strict doctor, -Observe, -AuditProjection, -Apply, -RotatePath, -DeactivateOAuthLuna, -QuarantineOAuthLuna, -RestoreOAuthLuna, -QuarantineAntigravity, -RestoreAntigravity, -QuarantineClaudeOAuth, or -RestoreClaudeOAuth."
}
# 2026-10-17 an agent session quarantined the claude lane on its own ToS
# reading. The lane switch is a user decision: agents may quarantine only on
# incident-runbook enforcement/account signals and must carry the user's
# instruction text as the receipt.
if ($QuarantineClaudeOAuth -and [string]::IsNullOrWhiteSpace($QuarantineUserDirective)) {
  throw "REFUSE -QuarantineClaudeOAuth without -QuarantineUserDirective: the Claude lane switch is a user decision (docs/runbooks/cpa-claude-account-risk.md); quarantine only on incident-runbook enforcement/account signals and carry the user's instruction text."
}

$guardrailScriptPath = $PSCommandPath
$scriptDir = Split-Path -Parent $guardrailScriptPath
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "lib\project_environment.ps1")

Initialize-WindowsProcessEnvironment
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$updaterPath = Join-Path $scriptDir "remote\cpa-auto-update.sh"
$healthPath = Join-Path $scriptDir "remote\cpa-health.py"
$healthRuntimePath = Join-Path $scriptDir "remote\cpa_health_runtime.py"
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
$workBuddyVersionCheckPath = Join-Path $scriptDir "remote\cpa-guardrail-workbuddy-version-check.sh"

function Read-ProjLeafText {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Description
  )
  # Test-Path first so a missing leaf becomes a clear contract error. Resolve
  # via DirectoryInfo so a reparse-point parent cannot redirect the read.
  $leafName = [IO.Path]::GetFileName($Path)
  if ([string]::IsNullOrEmpty($leafName)) {
    throw "$Description has no file name: $Path"
  }
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Description was not found at $Path"
  }
  $parentPath = [IO.Path]::GetDirectoryName($Path)
  if ([string]::IsNullOrEmpty($parentPath)) {
    $parentPath = (Get-Location -PSProvider FileSystem).ProviderPath
  }
  $parent = [IO.DirectoryInfo]::new($parentPath)
  $item = $parent.GetFileSystemInfos() |
    Where-Object { $_.Name -ceq $leafName } |
    Select-Object -First 1
  if ($null -eq $item) {
    throw "$Description was not found as a regular file at $Path"
  }
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "$Description must be a regular file, not a symlink or reparse point: $Path"
  }
  if ($item -is [IO.DirectoryInfo]) {
    throw "$Description must be a regular file, not a directory: $Path"
  }
  if ($item.Length -gt 8MB) {
    throw "$Description exceeds its 8 MiB projection-source limit: $Path"
  }
  # Decode in strict mode so a mislabeled source cannot become U+FFFD and be
  # projected as plausible text.
  $text = [IO.File]::ReadAllText(
    $item.FullName,
    [Text.UTF8Encoding]::new($false, $true)
  ).Replace("`r`n", "`n").Replace("`r", "`n")
  if ($text.Contains([char]0)) {
    throw "$Description contains a NUL byte and cannot be projected as UTF-8 text: $Path"
  }
  if ([string]::IsNullOrWhiteSpace($text)) {
    throw "$Description is empty: $Path"
  }
  return $text
}

$connectText = Read-ProjLeafText -Path (Join-Path $repoRoot "connect.ps1") -Description "connect.ps1"
$updaterText = Read-ProjLeafText -Path $updaterPath -Description "CPA updater source"
$healthText = Read-ProjLeafText -Path $healthPath -Description "CPA health source"
$healthRuntimeText = Read-ProjLeafText -Path $healthRuntimePath -Description "CPA health runtime source"
$policyText = Read-ProjLeafText -Path $policyPath -Description "CPA policy source"
$providerRoutesText = Read-ProjLeafText -Path $providerRoutesPath -Description "CPA provider route source"
$admissionText = Read-ProjLeafText -Path $admissionPath -Description "CPA admission source"
$admissionConfigText = Read-ProjLeafText -Path $admissionConfigPath -Description "CPA admission config"
$admissionUnitText = Read-ProjLeafText -Path $admissionUnitPath -Description "CPA admission unit"
$admissionIntegrityCheckText = Read-ProjLeafText -Path $admissionIntegrityCheckPath -Description "CPA admission integrity checker"
$admissionIntegrityDropinText = Read-ProjLeafText -Path $admissionIntegrityDropinPath -Description "CPA admission integrity drop-in"
$admissionIntegrityPinText = Read-ProjLeafText -Path $admissionIntegrityPinPath -Description "CPA admission integrity pin"
$fail2banFilterText = Read-ProjLeafText -Path $fail2banFilterPath -Description "CPA fail2ban filter source"
$fail2banJailText = Read-ProjLeafText -Path $fail2banJailPath -Description "CPA fail2ban jail source"
$workBuddyVersionCheckText = Read-ProjLeafText -Path $workBuddyVersionCheckPath -Description "CPA WorkBuddy version check source"

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
  $text = Read-ProjLeafText -Path $path -Description "CPA guardrail template $($entry.Key)"
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

$fail2banFilterBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($fail2banFilterText))
$workBuddyVersionCheckBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($workBuddyVersionCheckText))
$workBuddyVersionCheckSha256 = Get-LfNormalizedSha256 -Text $workBuddyVersionCheckText
$fail2banFilterSha256 = Get-LfNormalizedSha256 -Text $fail2banFilterText
# Fail closed on the source of truth, not just on the deployed copy: nginx
# reaches CPA over 127.0.0.1:8317, so a projected jail without the loopback
# exemption would let the doctor's own 401 probes accumulate to maxretry and ban
# 127.0.0.1, cutting the gateway off from its upstream.
if ($fail2banJailText -notmatch '(?m)^ignoreip = 127\.0\.0\.1/8 ::1$') {
  throw "cpa-fail2ban-jail.conf must keep 'ignoreip = 127.0.0.1/8 ::1' so loopback can never be self-banned."
}
$fail2banJailBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($fail2banJailText))
$fail2banJailSha256 = Get-LfNormalizedSha256 -Text $fail2banJailText
$updaterBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($updaterText))
$updaterSha256 = Get-LfNormalizedSha256 -Text $updaterText
$healthBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($healthText))
$healthSha256 = Get-LfNormalizedSha256 -Text $healthText
$healthRuntimeBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($healthRuntimeText))
$healthRuntimeSha256 = Get-LfNormalizedSha256 -Text $healthRuntimeText
$policyBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($policyText))
$policySha256 = Get-LfNormalizedSha256 -Text $policyText
$providerRoutes = $providerRoutesText | ConvertFrom-Json
$providerRoutesBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($providerRoutesText)
)
$providerRoutesSha256 = Get-LfNormalizedSha256 -Text $providerRoutesText
$admissionBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionText))
$admissionSha256 = Get-LfNormalizedSha256 -Text $admissionText
$admissionConfigBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionConfigText))
$admissionConfigSha256 = Get-LfNormalizedSha256 -Text $admissionConfigText
$admissionUnitBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionUnitText))
$admissionUnitSha256 = Get-LfNormalizedSha256 -Text $admissionUnitText
$admissionIntegrityCheckBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionIntegrityCheckText))
$admissionIntegrityCheckSha256 = Get-LfNormalizedSha256 -Text $admissionIntegrityCheckText
$admissionIntegrityDropinBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionIntegrityDropinText))
$admissionIntegrityDropinSha256 = Get-LfNormalizedSha256 -Text $admissionIntegrityDropinText
$admissionIntegrityPinBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($admissionIntegrityPinText))
$admissionIntegrityPinSha256 = Get-LfNormalizedSha256 -Text $admissionIntegrityPinText
$updaterTimeoutConfPath = Join-Path $scriptDir "remote\cpa-update-timeout.conf"
$updaterTimeoutConfText = Read-ProjLeafText -Path $updaterTimeoutConfPath -Description "CPA updater timeout drop-in"
if ($AuditProjection) {
  $auditRoot = $null
  try {
    $pythonCandidates = @(
      (Join-Path $repoRoot ".venv/Scripts/python.exe"),
      (Join-Path $repoRoot ".venv/bin/python")
    )
    foreach ($command in @("python.exe", "python3.exe", "python", "python3")) {
      $pythonCandidates += (Get-Command $command -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '[\\/]WindowsApps[\\/]' } |
        ForEach-Object { $_.Source })
    }
    foreach ($candidate in $pythonCandidates) {
      if ($null -eq $candidate -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        continue
      }
      $probe = & $candidate -c "import sys; print(sys.version_info[:2])" 2>$null
      if ($LASTEXITCODE -eq 0 -and $probe -match '^\(3,') {
        $python = $candidate
        break
      }
    }
    if ($null -eq $python) {
      throw "The projection audit requires a local Python interpreter."
    }
    $auditRoot = Join-Path ([IO.Path]::GetTempPath()) (
      "cpa-projection-" + [guid]::NewGuid().ToString("N")
    )
    $createdAuditRoot = [IO.Directory]::CreateDirectory($auditRoot)
    if ($IsWindows) {
      $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
      $isAdministrator = [Security.Principal.WindowsPrincipal]::new(
        $identity
      ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
      # The ACL grants access only to this user. icacls modifies the DACL
      # without requiring the SACL privilege that Set-Acl can round-trip.
      $auditSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
      if ([string]::IsNullOrEmpty($auditSid)) {
        throw "Unable to resolve the current Windows user for the projection audit."
      }
      $aclGrant = "*${auditSid}:(OI)(CI)F"
      # icacls prints a friendly identity even for a numeric SID. Normalize
      # the listing to numeric SIDs so names need not resolve on hosted hosts.
      $aclOutput = @(& icacls $auditRoot /inheritance:r /Q /grant:r $aclGrant 2>&1)
      if ($LASTEXITCODE -ne 0) {
        throw "Unable to create a private CPA projection directory: $aclOutput"
      }
      if ($IsWindows) {
        $auditAclFile = Join-Path ([IO.Path]::GetTempPath()) (
          "cpa-projection-acl-" + [guid]::NewGuid().ToString("N") + ".sddl"
        )
        try {
          $sddlOutput = @(& icacls $auditRoot /save $auditAclFile /L /Q 2>&1)
          if ($LASTEXITCODE -ne 0 -or
              -not (Test-Path -LiteralPath $auditAclFile -PathType Leaf)) {
            throw "Unable to export the private CPA projection directory ACL: $sddlOutput"
          }
          $sddl = [IO.File]::ReadAllText(
            $auditAclFile,
            [Text.Encoding]::Unicode
          )
          if ($sddl -notmatch 'D:(?:AI|PAI)') {
            throw "The CPA projection directory ACL export has no DACL."
          }
        } finally {
          if (Test-Path -LiteralPath $auditAclFile -PathType Leaf) {
            [IO.File]::Delete($auditAclFile)
          }
        }
      } else {
        $sddl = [Security.AccessControl.FileSystemSecurity]::new()
        $sddl.SetAccessRuleProtection($true, $false)
        $sddl.AddAccessRule(
          [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($auditSid),
            "FullControl",
            "ContainerInherit, ObjectInherit",
            "None",
            "Allow"
          )
        )
        $sddl = $sddl.GetSecurityDescriptorSddlForm("Access")
      }
      # An explicit grant:r is not enough on a hosted or domain-managed host:
      # inherited SYSTEM/Administrators grants remain, so requiring exactly one
      # allow rule made the local audit unusable. Require instead that every
      # non-administrative allow rule belongs to this user, and reject unknown
      # identity kinds (Users/Everyone/Authenticated Users).
      $allowedSystemSids = @(
        "S-1-5-18",                         # LocalSystem
        "S-1-5-32-544"                      # Administrators
      )
      # Parse the exported SDDL numerically instead of resolving a friendly
      # identity. Broad ACEs can be inherited on hosted hosts, so only this
      # user and, for an administrator, SYSTEM/Administrators may receive
      # access. Read/control masks followed by an explicit deny are harmless.
      # icacls /save stores the DACL but deliberately omits object ownership.
      # The directory was created by this process, so ownership is not inferred
      # from an unavailable value.
      $auditOwner = $auditSid
      $auditOtherGrants = @(
        [regex]::Matches(
          $sddl,
          '\((?:A|OA|XA|ZA);[^;]*;([^;]*);[^;]*;([^;]*);([^;]*;[^;]*)\)'
        ) |
          ForEach-Object {
            $grantSid = $_.Groups[1].Value
            $auditFlags = $_.Groups[2].Value
            $auditMask = $_.Groups[3].Value
            if ($auditFlags -eq 'ID' -and
                $auditMask -match '^(?:0x001200a9|0x00120089|0x00020089)$') {
              # Read and execute on an inherited directory entry is not a read
              # grant to this directory itself. A write/delete mask remains a
              # foreign grant and must fail closed.
              return
            }
            $grantSid = switch ($grantSid) {
              'SY' { 'S-1-5-18'; break }
              'BA' { 'S-1-5-32-544'; break }
              default { $grantSid }
            }
            if ($grantSid -ne $auditSid -and
                (-not $isAdministrator -or $grantSid -notin $allowedSystemSids)) {
              $grantSid -replace '^[A-Z]{2}$', 'nonsystem'
            }
          }
      )
      if ($auditOtherGrants.Count -ne 0) {
        throw "Unable to verify the private CPA projection directory ACL: valid=no"
      }
    }
    $pythonPath = Join-Path $auditRoot "projection-check.py"
    $doctorForAudit = $guardrailTemplateTexts["doctor"].Replace(
      "__CPA_WORKBUDDY_VERSION_CHECK_B64__",
      $workBuddyVersionCheckBase64
    )
    $applyForAudit = Get-CpaGuardrailTemplate -Name "apply"
    [IO.File]::WriteAllText(
      $pythonPath,
      @'
from pathlib import Path
import ast
import json
import sys
root = Path(sys.argv[1])
for name in ("cpa-health.py", "cpa_health_runtime.py", "cpa_policy.py", "cpa-admission.py"):
    ast.parse((root / name).read_text(encoding="utf-8"), filename=name)
json.loads((root / "admission.json").read_text(encoding="utf-8"))
'@,
      [Text.UTF8Encoding]::new($false)
    )
    $expectedBytes = [Text.Encoding]::UTF8.GetBytes([IO.File]::ReadAllText($pythonPath, [Text.UTF8Encoding]::new($false, $true)))
    if (-not [Linq.Enumerable]::SequenceEqual(
        [byte[]][IO.File]::ReadAllBytes($pythonPath), [byte[]]$expectedBytes
    )) {
      throw "Projected source byte round-trip failed."
    }
    foreach ($entry in @(
      @("apply.sh", $applyForAudit),
      @(
        "doctor.sh",
        $doctorForAudit
      ),
      @("cpa-auto-update.sh", $updaterText),
      @("cpa-health.py", $healthText),
      @("cpa_health_runtime.py", $healthRuntimeText),
      @("cpa_policy.py", $policyText),
      @("cpa-admission.py", $admissionText),
      @("cpa-admission-integrity-check.sh", $admissionIntegrityCheckText),
      @("provider-routes.json", $providerRoutesText),
      @("admission.json", $admissionConfigText),
      @("systemd.conf", $admissionUnitText)
    )) {
      $name = [string]$entry[0]
      $text = [string]$entry[1]
      $text = [regex]::Replace(
        $text,
        '__CPA_[A-Za-z0-9_]+__',
        'local-audit-value'
      )
      if ($text -match '__CPA_[A-Za-z0-9_]+__') {
        throw "Unsubstituted projection placeholder in $name."
      }
      if ($text.Contains([char]0)) {
        throw "NUL byte in projected $name."
      }
      [IO.File]::WriteAllText(
        (Join-Path $auditRoot $name),
        $text,
        [Text.UTF8Encoding]::new($false)
      )
      $writtenBytes = [IO.File]::ReadAllBytes((Join-Path $auditRoot $name))
      if (-not [Linq.Enumerable]::SequenceEqual(
          [byte[]]$writtenBytes, [byte[]][Text.Encoding]::UTF8.GetBytes($text)
      )) {
        throw "Projected source byte round-trip failed for $name."
      }
    }
    [IO.File]::WriteAllText(
      (Join-Path $auditRoot "provider-env.txt"),
      [string]::Join("`n", @(
        "BASE_URL_1=http://local-audit.invalid",
        "API_KEY_1=local-audit-value"
      )),
      [Text.UTF8Encoding]::new($false)
    )
    foreach ($source in @(
      "cpa-auto-update.sh", "cpa-health.py", "cpa_health_runtime.py",
      "cpa_policy.py", "cpa-admission.py"
    )) {
      $path = Join-Path $auditRoot $source
      $text = Get-Content -LiteralPath $path -Raw
      if ([Text.Encoding]::UTF8.GetByteCount($text) -eq 0) {
        throw "Empty projected source: $source."
      }
      Write-Output "CPA_PROJECTION_SOURCE=$($source):READY"
    }
    $bashError = $null
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    try {
      $files = @(Get-ChildItem -LiteralPath $auditRoot -File | Sort-Object Name)
      foreach ($file in $files) {
        $bytes = [IO.File]::ReadAllBytes($file.FullName)
        $chars = [char[]]::new($bytes.Length)
        $decoder = [Text.UTF8Encoding]::new($false, $true).GetDecoder()
        $decoder.GetChars($bytes, 0, $bytes.Length, $chars, 0)
        [void]$utf8.GetString($bytes)
      }
    } catch [Text.DecoderFallbackException] {
      throw "Projected source is not valid UTF-8: $($_.Exception.Message)"
    }
    $auditRootText = [IO.Path]::GetFullPath($auditRoot).TrimEnd(
      [IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar
    )
    $authFiles = @(Get-ChildItem -LiteralPath $auditRoot -File -Recurse)
    for ($index = 0; $index -lt $authFiles.Count; $index++) {
      if ($authFiles.Count -gt 16 -or
          $authFiles[$index].DirectoryName -ne $auditRootText -or
          (($authFiles[$index].Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "Projected source tree contains a nested, linked, or oversized entry."
      }
    }
    $bashCandidates = @("/usr/bin/bash", "/bin/bash")
    if ($null -ne $env:ProgramFiles) {
      $bashCandidates += @(
        (Join-Path $env:ProgramFiles "Git/bin/bash.exe"),
        (Join-Path $env:ProgramFiles "Git/usr/bin/bash.exe")
      )
    }
    $bash = $null
    $bashVersion = $null
    foreach ($candidate in $bashCandidates) {
      $item = Get-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
      if ($null -ne $item -and -not $item.PSIsContainer -and
          ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
        $versionOutput = @(& $candidate --version 2>&1)
        $versionText = $versionOutput -join "`n"
        if ($LASTEXITCODE -ne 0 -or
            $versionText -match '(?i)(microsoft|windows|GNU bash, version [0-9.]+\([^)]*-(linux|gnu))') {
          $bashError = "Projected shell syntax requires a native Bash runtime; " +
            "requested parser rejected: $candidate ($versionText)"
          continue
        }
        $bash = $candidate
        $bashVersion = $versionText
        break
      }
    }
    $bashAuditFiles = @(
      "apply.sh", "doctor.sh", "cpa-auto-update.sh",
      "cpa-admission-integrity-check.sh"
    ) | ForEach-Object { Join-Path $auditRoot $_ }
    if ($null -ne $bash) {
      Write-Output "CPA_PROJECTION_BASH=$bash"
      foreach ($script in $bashAuditFiles) {
        $syntax = @(& $bash -n $script 2>&1)
        if ($LASTEXITCODE -ne 0) {
          throw "Bash syntax validation failed for $(Split-Path -Leaf $script): $syntax"
        }
      }
    } else {
      throw "Bash is unavailable; projected shell syntax was not independently checked. $bashError"
    }
    & $python -s $pythonPath $auditRoot
    if ($LASTEXITCODE -ne 0) {
      throw "Projected Python/JSON syntax validation failed with exit $LASTEXITCODE."
    }
    Write-Output "CPA_PROJECTION_TEMPLATE=apply:READY"
    Write-Output "CPA_PROJECTION_TEMPLATE=doctor:READY"
  }
  finally {
    if ($null -ne $auditRoot) {
      try {
        [IO.Directory]::Delete($auditRoot, $true)
      } catch {
        Write-Warning "Failed to remove the local CPA projection directory."
      }
    }
  }
  Write-Output "CPA_PROJECTION_AUDIT=PASS evidence=filesystem_projection"
  exit 0
}
$updaterTimeoutConfBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($updaterTimeoutConfText))
$updaterTimeoutConfSha256 = Get-LfNormalizedSha256 -Text $updaterTimeoutConfText

$projectionSourcePaths = @(
  "connect.ps1",
  "scripts/cpa_bwg_guardrails.ps1",
  "scripts/remote/cpa-auto-update.sh",
  "scripts/remote/cpa-health.py",
  "scripts/remote/cpa_health_runtime.py",
  "scripts/remote/cpa_policy.py",
  "scripts/remote/cpa_provider_routes.json",
  "scripts/remote/cpa-admission.py",
  "scripts/remote/cpa-admission.json",
  "scripts/remote/cpa-admission.service",
  "scripts/remote/cpa-admission-integrity-check.sh",
  "scripts/remote/cpa-admission-integrity.conf",
  "scripts/remote/cpa-admission-integrity-pin.txt",
  "scripts/remote/cpa-update-timeout.conf",
  "scripts/remote/cpa-fail2ban-filter.conf",
  "scripts/remote/cpa-fail2ban-jail.conf",
  "scripts/remote/cpa-guardrail-workbuddy-version-check.sh"
)
$projectionSourceHashes = [ordered]@{
  "connect.ps1" = Get-LfNormalizedSha256 -Text $connectText
  "scripts/cpa_bwg_guardrails.ps1" = Get-LfNormalizedSha256 -Text ((Get-Content -LiteralPath $PSCommandPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n"))
  "scripts/remote/cpa-auto-update.sh" = $updaterSha256
  "scripts/remote/cpa-health.py" = $healthSha256
  "scripts/remote/cpa_health_runtime.py" = $healthRuntimeSha256
  "scripts/remote/cpa_policy.py" = $policySha256
  "scripts/remote/cpa_provider_routes.json" = $providerRoutesSha256
  "scripts/remote/cpa-admission.py" = $admissionSha256
  "scripts/remote/cpa-admission.json" = $admissionConfigSha256
  "scripts/remote/cpa-admission.service" = $admissionUnitSha256
  "scripts/remote/cpa-admission-integrity-check.sh" = $admissionIntegrityCheckSha256
  "scripts/remote/cpa-admission-integrity.conf" = $admissionIntegrityDropinSha256
  "scripts/remote/cpa-admission-integrity-pin.txt" = $admissionIntegrityPinSha256
  "scripts/remote/cpa-update-timeout.conf" = $updaterTimeoutConfSha256
  "scripts/remote/cpa-fail2ban-filter.conf" = $fail2banFilterSha256
  "scripts/remote/cpa-fail2ban-jail.conf" = $fail2banJailSha256
  "scripts/remote/cpa-guardrail-workbuddy-version-check.sh" = $workBuddyVersionCheckSha256
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

# Every action that embeds repo-derived payload bytes (route manifest, policy,
# projections) must run from a clean tree: a dirty worktree would drive remote
# exclusion/retire decisions with unreviewed bytes even though the post-hoc
# HEAD-anchored doctor would flag the drift only after the fact.
$payloadEmbeddingActions = @(
  $Apply, $DeactivateOAuthLuna, $QuarantineOAuthLuna, $RestoreOAuthLuna,
  $QuarantineAntigravity, $RestoreAntigravity, $QuarantineClaudeOAuth,
  $RestoreClaudeOAuth
)
if (@($payloadEmbeddingActions | Where-Object { $_ }).Count -gt 0) {
  Assert-ProjectionSourcesUnchanged
}

function Get-HeadBlobBytes {
  param([Parameter(Mandatory = $true)][string]$RepoRelativePath)
  $gitPath = "HEAD:$($RepoRelativePath -replace '\\', '/')"
  $startInfo = [Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = "git"
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  foreach ($argument in @("-C", $repoRoot, "show", $gitPath)) {
    [void]$startInfo.ArgumentList.Add($argument)
  }
  $proc = [Diagnostics.Process]::Start($startInfo)
  try {
    $buffer = [IO.MemoryStream]::new()
    try {
      # Drain stderr concurrently so a chatty git cannot fill that pipe and
      # deadlock the blocking stdout copy below.
      $stderr = $proc.StandardError.ReadToEndAsync()
      $proc.StandardOutput.BaseStream.CopyTo($buffer)
      $proc.WaitForExit()
      $errorText = $stderr.GetAwaiter().GetResult()
      if ($proc.ExitCode -ne 0) {
        throw "git show $gitPath failed with exit code $($proc.ExitCode). $errorText"
      }
      return ,$buffer.ToArray()
    }
    finally {
      $buffer.Dispose()
    }
  }
  finally {
    $proc.Dispose()
  }
}

function Get-HeadBlobSha256 {
  param([Parameter(Mandatory = $true)][string]$RepoRelativePath)

  # Doctor compares the committed source of truth (HEAD blob, LF as stored in
  # git) against the deployed files. Anchoring to HEAD instead of the working
  # tree keeps uncommitted parallel-session edits from turning into doctor
  # failures; an -Apply projection still verifies the exact working-tree bytes
  # it writes via write_base64_file.
  $bytes = Get-HeadBlobBytes -RepoRelativePath $RepoRelativePath
  try {
    return ([Convert]::ToHexString(
      [Security.Cryptography.SHA256]::HashData($bytes)
    )).ToLowerInvariant()
  }
  finally {
    [Array]::Clear($bytes, 0, $bytes.Length)
  }
}

function Get-HeadBlobText {
  param([Parameter(Mandatory = $true)][string]$RepoRelativePath)

  $bytes = Get-HeadBlobBytes -RepoRelativePath $RepoRelativePath
  try {
    return [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
  }
  finally {
    [Array]::Clear($bytes, 0, $bytes.Length)
  }
}

$admissionIntegrityPinHeadValue = Get-HeadBlobText "scripts/remote/cpa-admission-integrity-pin.txt"

$updaterHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-auto-update.sh"
$healthHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-health.py"
$healthRuntimeHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa_health_runtime.py"
$policyHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa_policy.py"
$providerRoutesHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa_provider_routes.json"
$admissionHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission.py"
$admissionConfigHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission.json"
$admissionUnitHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission.service"
$admissionIntegrityCheckHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission-integrity-check.sh"
$admissionIntegrityDropinHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission-integrity.conf"
$admissionIntegrityPinHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-admission-integrity-pin.txt"
$updaterTimeoutConfHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-update-timeout.conf"
$fail2banFilterHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-fail2ban-filter.conf"
$fail2banJailHeadSha256 = Get-HeadBlobSha256 "scripts/remote/cpa-fail2ban-jail.conf"
# Doctor reads the deployed admission pin as content and compares it with this
# exact committed value, never with the SHA-256 of the pin file itself.
$admissionIntegrityPinHeadValue = [string]$admissionIntegrityPinHeadValue
$admissionIntegrityPinHeadValue = $admissionIntegrityPinHeadValue.Trim()
if ($admissionIntegrityPinHeadValue -notmatch '^[0-9a-f]{64}$') {
  throw "CPA admission integrity pin must be one bare lowercase sha256 value."
}
if ($admissionIntegrityPinHeadValue -ne $admissionHeadSha256) {
  throw "CPA admission integrity pin $admissionIntegrityPinHeadValue does not match the committed cpa-admission.py $admissionHeadSha256; update scripts/remote/cpa-admission-integrity-pin.txt in the same commit."
}

if ($Apply -and $updaterSha256 -ne $updaterHeadSha256) {
  # -Apply anchors the remote doctor's drift gate to HEAD. Source-only bytes
  # that differ from HEAD would therefore be replaced by an earlier generation
  # at the next projection. Refuse: repository metadata claims one generation,
  # while the fleet hash describes a different payload intentionally.
  throw "CPA Apply requires the source-only updater to match HEAD: working=$updaterSha256 head=$updaterHeadSha256."
}

# The fork-binary ledger is the single committed source for the hash of the
# binary the gateway actually executes (bind-mounted or image-embedded). It
# cannot be cross-checked against a repo script like the admission pin, so it
# is format-checked here and asserted against the live container by the
# doctor's runtime-binary section on every run.
$forkBinaryHeadValue = (Get-HeadBlobText "scripts/remote/fork-binary.sha256").Trim()
if ($forkBinaryHeadValue -notmatch '^[0-9a-f]{64}$') {
  throw "fork-binary.sha256 must contain a bare sha256 hex digest; got '$forkBinaryHeadValue'. Update scripts/remote/fork-binary.sha256 in the same commit as the fork binary swap."
}

$projectionHashPairs = @(
  "/opt/cliproxyapi/auto-update.sh=$updaterHeadSha256",
  "/opt/cliproxyapi/cpa-health.py=$healthHeadSha256",
  "/opt/cliproxyapi/cpa_health_runtime.py=$healthRuntimeHeadSha256",
  "/opt/cliproxyapi/cpa_policy.py=$policyHeadSha256",
  "/opt/cliproxyapi/cpa_provider_routes.json=$providerRoutesHeadSha256",
  "/opt/cliproxyapi/cpa-admission.py=$admissionHeadSha256",
  "/opt/cliproxyapi/cpa-admission.json=$admissionConfigHeadSha256",
  "/etc/systemd/system/cpa-admission.service=$admissionUnitHeadSha256",
  "/usr/local/libexec/cpa-admission-integrity-check=$admissionIntegrityCheckHeadSha256",
  "/etc/systemd/system/cpa-admission.service.d/10-integrity.conf=$admissionIntegrityDropinHeadSha256",
  "/etc/vps-ssh-launcher/cpa-admission.sha256=$admissionIntegrityPinHeadSha256",
  "/etc/systemd/system/cliproxyapi-update.service.d/cpa-update-timeout.conf=$updaterTimeoutConfHeadSha256",
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

  # Re-read the exact source set immediately before the SSH call. The
  # payload and its HEAD-anchored doctor hashes must describe one stable
  # generation, even if another local process edits the worktree while
  # this script is preparing a long command.
  if (@($payloadEmbeddingActions | Where-Object { $_ }).Count -gt 0) {
    Assert-ProjectionSourcesUnchanged
  }
  # One shared transport for the whole fleet (base64/CRLF normalization,
  # framed single-shot, mode-600 staged fallback). The remote temp prefix
  # keeps the historical cpa-guardrails namespace; -CommandTimeout maps
  # onto the same idle-timeout flag the connect.ps1 path used.
  $transport = @{
    Python = $script:Python
    ProjectRoot = $repoRoot
    Profile = $Profile
    Command = $Script
    IdleTimeoutSeconds = $CommandTimeout
    RemoteTempPrefix = "cpa-guardrails"
  }
  if (-not [string]::IsNullOrWhiteSpace($Config)) {
    $transport.Config = $Config
  }
  if (-not [string]::IsNullOrWhiteSpace($LauncherEntryPointOverride)) {
    $transport.EntryPointPath = $LauncherEntryPointOverride
  }
  Invoke-LauncherRemoteCommand @transport
}

$doctorScript = Get-CpaGuardrailTemplate -Name "doctor"

if ($Observe) {
  $doctorScript = $doctorScript.Replace("STRICT=1", "STRICT=0")
}
$doctorScript = $doctorScript.Replace(
  "__CPA_WORKBUDDY_VERSION_CHECK_B64__",
  $workBuddyVersionCheckBase64
)

if (-not $Apply -and -not $RotatePath -and -not $DeactivateOAuthLuna -and
    -not $QuarantineOAuthLuna -and -not $RestoreOAuthLuna -and
    -not $QuarantineAntigravity -and -not $RestoreAntigravity -and
    -not $QuarantineClaudeOAuth -and -not $RestoreClaudeOAuth) {
  $doctorScript = $doctorScript.Replace("__CPA_PROJECTION_HASH_PAIRS__", $projectionHashPairs).Replace(
    "__CPA_ADMISSION_PIN_VALUE__",
    $admissionIntegrityPinHeadValue
  ).Replace("__CPA_FORK_BINARY_SHA_VALUE__", $forkBinaryHeadValue)
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

if ($QuarantineOAuthLuna -or $RestoreOAuthLuna -or $QuarantineAntigravity -or $RestoreAntigravity -or $QuarantineClaudeOAuth -or $RestoreClaudeOAuth) {
  $quarantineMode = if ($QuarantineOAuthLuna -or $QuarantineAntigravity -or $QuarantineClaudeOAuth) { "quarantine" } else { "restore" }
  $quarantineProvider = if ($QuarantineClaudeOAuth -or $RestoreClaudeOAuth) { "claude" } elseif ($QuarantineAntigravity -or $RestoreAntigravity) { "antigravity" } else { "codex" }
  if ($quarantineProvider -eq "claude") {
    # The directive is authorization input, not an output artifact. Do not
    # copy arbitrary user text into terminal logs, scheduled-task history, or
    # evidence captures; length is enough to prove that it was supplied.
    Write-Host "QUARANTINE_USER_DIRECTIVE=present length=$($QuarantineUserDirective.Length)"
  }
  $quarantineScript = $quarantineScript.Replace("__CPA_OAUTH_QUARANTINE_PROVIDER__", $quarantineProvider)
  $quarantineScript = $quarantineScript.Replace(
    "__CPA_OAUTH_QUARANTINE_MODE__",
    $quarantineMode
  ).Replace(
    "__CPA_OAUTH_QUARANTINE_MANIFEST_B64__",
    $providerRoutesBase64
  )
  if ($quarantineProvider -eq "claude") {
    $quarantineScript = $quarantineScript.Replace(
      "__CPA_CLAUDE_POLICY_B64__",
      $policyBase64
    ).Replace(
      "__CPA_CLAUDE_POLICY_SHA256__",
      $policySha256
    )
  }
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
  "__CPA_HEALTH_RUNTIME_B64__",
  $healthRuntimeBase64
).Replace(
  "__CPA_HEALTH_RUNTIME_SHA256__",
  $healthRuntimeSha256
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
).Replace(
  "__CPA_UPDATER_TIMEOUT_CONF_B64__",
  $updaterTimeoutConfBase64
).Replace(
  "__CPA_UPDATER_TIMEOUT_CONF_SHA256__",
  $updaterTimeoutConfSha256
)
Invoke-BwgRemoteScript -Script $applyScript -CommandTimeout 240

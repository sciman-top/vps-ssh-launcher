param(
  [Parameter(Mandatory)]
  [ValidateSet('Protect', 'Unprotect')]
  [string]$Mode,
  [Parameter(Mandatory)]
  [string]$InputPath,
  [Parameter(Mandatory)]
  [string]$OutputPath
)
$ErrorActionPreference = 'Stop'

if ($Mode -eq 'Protect') {
  $plain = [IO.File]::ReadAllText($InputPath).Trim()
  if ($plain -notmatch '^[0-9a-f]{64}$') {
    throw 'The protected frp credential has an unexpected shape.'
  }
  $plainBytes = [Text.Encoding]::UTF8.GetBytes($plain)
  try {
    $protected = [Security.Cryptography.ProtectedData]::Protect(
      $plainBytes, $null,
      [Security.Cryptography.DataProtectionScope]::CurrentUser)
    [IO.File]::WriteAllBytes($OutputPath, $protected)
    [Array]::Clear($protected, 0, $protected.Length)
  } finally {
    [Array]::Clear($plainBytes, 0, $plainBytes.Length)
    $plain = $null
  }
} else {
  $protected = [IO.File]::ReadAllBytes($InputPath)
  $plainBytes = [Security.Cryptography.ProtectedData]::Unprotect(
    $protected, $null,
    [Security.Cryptography.DataProtectionScope]::CurrentUser)
  try {
    $plain = [Text.Encoding]::UTF8.GetString($plainBytes)
    if ($plain -notmatch '^[0-9a-f]{64}$') {
      throw 'The unsealed frp credential has an unexpected shape.'
    }
    [IO.File]::WriteAllText(
      $OutputPath, $plain, [Text.UTF8Encoding]::new($false))
  } finally {
    [Array]::Clear($protected, 0, $protected.Length)
    [Array]::Clear($plainBytes, 0, $plainBytes.Length)
    $plain = $null
  }
}

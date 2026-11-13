[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
  [Parameter(ParameterSetName = 'Setup')]
  [switch]$Setup,
  [Parameter(ParameterSetName = 'Status')]
  [switch]$Status,
  [Parameter(ParameterSetName = 'Acceptance')]
  [switch]$Acceptance,
  [Parameter(ParameterSetName = 'Setup')]
  [ValidateRange(1, 65535)]
  [int]$ServerPort = 5200,
  [Parameter(ParameterSetName = 'Setup')]
  [ValidateRange(1, 65535)]
  [int]$HomePort = 5200,
  [Parameter(ParameterSetName = 'Acceptance')]
  [string]$AcceptanceCommand,
  [Parameter(ParameterSetName = 'Acceptance')]
  [string]$ExpectedEgress,
  [Parameter(ParameterSetName = 'Acceptance')]
  [ValidateRange(1, 65535)]
  [int]$ProfilePort = 5200,
  [Parameter(ParameterSetName = 'Acceptance')]
  [switch]$AcceptanceDirect,
  [Parameter(ParameterSetName = 'Setup')]
  [switch]$Apply
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$invariant = [Globalization.CultureInfo]::InvariantCulture
$script:StepNumber = 0
function Enter-FrpStep {
  param([string]$Name)
  $script:StepNumber++
  Write-Output "FRP_STEP_$($script:StepNumber)=$Name"
}

function Get-FrpPublicAddress {
  param([string]$HostName)
  $addresses = @([Net.Dns]::GetHostAddresses($HostName) |
    Where-Object AddressFamily -eq ([Net.Sockets.AddressFamily]::InterNetwork) |
    ForEach-Object IPAddressToString | Sort-Object -Unique)
  if ($addresses.Count -ne 1) {
    throw "The public host must resolve to exactly one IPv4 address, got $($addresses.Count)."
  }
  return $addresses[0]
}

function Get-FrpProfile {
  param([string]$Name, [string]$ExpectedAddress)
  $configDirectory = Join-Path $env:APPDATA 'vps-ssh-launcher'
  $targetPath = Join-Path $configDirectory 'target.json'
  if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
    throw "The private launcher target file is missing: $targetPath"
  }
  $target = Get-Content -Raw -LiteralPath $targetPath | ConvertFrom-Json -AsHashtable
  if (-not $target.ContainsKey('profiles') -or
      -not $target.profiles.ContainsKey($Name)) {
    throw "The launcher profile '$Name' is missing."
  }
  $profile = $target.profiles[$Name]
  $hostValue = [string]$profile.host
  $resolvedHost = $hostValue
  $literalAddress = $null
  if (-not [IPAddress]::TryParse($hostValue, [ref]$literalAddress)) {
    $resolvedHost = Get-FrpPublicAddress -HostName $hostValue
  } elseif ($literalAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
    throw "The launcher profile '$Name' is not IPv4."
  }
  if ($resolvedHost -ne $ExpectedAddress) {
    throw "The launcher profile '$Name' did not resolve to the reviewed public address."
  }
  if ([string]::IsNullOrWhiteSpace([string]$profile.user) -or
      [string]::IsNullOrWhiteSpace([string]$profile.password)) {
    throw "The launcher profile '$Name' lacks password-authentication fields."
  }
  $profilePort = 22
  if ($profile.ContainsKey('port')) {
    $profilePort = [int]$profile.port
  }
  if ($profilePort -lt 1 -or $profilePort -gt 65535) {
    throw "The launcher profile '$Name' has an invalid SSH port."
  }
  return [pscustomobject]@{
    Name = $Name
    Host = $resolvedHost
    Port = $profilePort
    User = [string]$profile.user
    Password = [string]$profile.password
  }
}

function Invoke-FrpRemote {
  param(
    [Parameter(Mandatory)] [object]$Profile,
    [Parameter(Mandatory)] [string]$Command,
    [Parameter(Mandatory)] [string]$KnownHosts,
    [Parameter(Mandatory)] [string]$AskpassInput,
    [Parameter(Mandatory)] [string]$AskpassCommand,
    [int]$TimeoutSeconds = 30
  )
  if ((Test-Path -LiteralPath $AskpassInput) -or
      -not (Test-Path -LiteralPath $AskpassCommand -PathType Leaf)) {
    throw 'The private askpass helper state is unsafe.'
  }
  $pinnedIdentity = (Get-Content -LiteralPath $KnownHosts -Raw).Trim()
  if ($pinnedIdentity -notmatch '^\S+\s+ssh-ed25519\s+(?<key>[A-Za-z0-9+/]+={0,2})$') {
    throw 'The pinned SSH identity file is malformed.'
  }
  $pinnedKey = $Matches.key
  $passwordConfig = [IO.Path]::Combine(
    (Split-Path -Parent $KnownHosts), 'aliyun-config')
  if (-not (Test-Path -LiteralPath $passwordConfig -PathType Leaf)) {
    throw 'The private password SSH profile is missing.'
  }
  $reviewedHosts = [IO.Path]::Combine(
    (Split-Path -Parent $KnownHosts), 'known_hosts.bootstrap-reviewed')
  $pinnedLine = (Get-Content -LiteralPath $KnownHosts -Raw).Trim()
  if ($pinnedLine -notmatch '^\[') {
    $pinnedLine = $pinnedLine.Replace('8.163.41.198 ssh-ed25519',
      '[8.163.41.198]:22 ssh-ed25519')
  }
  [IO.File]::WriteAllText(
    $reviewedHosts, $pinnedLine + "`n", [Text.UTF8Encoding]::new($false))
  $reviewedText = Get-Content -Raw -LiteralPath $passwordConfig
  $expectedPasswordConfigRoot = Split-Path -Parent $KnownHosts
  try {
    $passwordBytes = [Text.Encoding]::UTF8.GetBytes([string]$Profile.Password)
    [IO.File]::WriteAllBytes($AskpassInput, $passwordBytes)
    [Array]::Clear($passwordBytes, 0, $passwordBytes.Length)
    $profile.Password = $null
    $previousAskpass = $env:SSH_ASKPASS
    $previousRequire = $env:SSH_ASKPASS_REQUIRE
    $previousDisplay = $env:DISPLAY
    $env:SSH_ASKPASS = $AskpassCommand
    $env:SSH_ASKPASS_REQUIRE = 'force'
    $env:DISPLAY = ':0'
    try {
      $arguments = @(
        '-p', ([string]$Profile.Port),
        '-o', 'StrictHostKeyChecking=yes',
        '-o', 'UpdateHostKeys=no',
        '-o', 'PubkeyAuthentication=no',
        '-o', 'PasswordAuthentication=yes',
        '-o', 'KbdInteractiveAuthentication=no',
        '-o', 'NumberOfPasswordPrompts=1',
        '-o', "ConnectTimeout=$TimeoutSeconds",
        '-T', "$($Profile.User)@$($Profile.Host)",
        'bash -s'
      )
      $sshKnownHosts = $KnownHosts.Replace('\', '/')
  $sshEmptyHosts = [IO.Path]::Combine(
    (Split-Path -Parent $KnownHosts), 'empty_known_hosts')
  $evaluationHosts = [IO.Path]::Combine(
    (Split-Path -Parent $KnownHosts), 'known_hosts.bootstrap-reviewed')
      $sshArguments = $arguments | ForEach-Object {
            $arg = ([string]$_).Replace('@FRP_PINNED_KNOWN_HOSTS@', $sshKnownHosts)
            $arg = $arg.Replace('@FRP_EMPTY_KNOWN_HOSTS@', $sshEmptyHosts.Replace('\', '/'))
            $arg
      }
      $plink = Get-Command plink.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty Source
      $hostkeyField = "ssh-ed25519 $pinnedKey"
      if ($plink) {
        $result = $Command | & $plink -ssh -batch -no-antispoof `
          -hostkey $hostkeyField -l $Profile.User `
          -pw ([string]$Profile.Password) -P $Profile.Port $Profile.Host 2>&1
      } else {
        $knownHostsValue = $evaluationHosts.Replace('\', '/')
        $keyType = $pinnedIdentity -replace '^\S+\s+', '' -replace '\s.+$', ''
        $result = $Command | & 'C:\Program Files\Git\usr\bin\ssh.exe' `
          -o "HostKeyAlias=[8.163.41.198]:22" `
          -o "UserKnownHostsFile=$knownHostsValue" `
          -o "HostKeyAlgorithms=$keyType" `
          -o 'PubkeyAuthentication=no' -o 'PasswordAuthentication=yes' `
          -o 'KbdInteractiveAuthentication=no' -o 'NumberOfPasswordPrompts=1' `
          -p ([string]$Profile.Port) "$($Profile.User)@$($Profile.Host)" 2>&1
      }
      $exitCode = $LASTEXITCODE
    } finally {
      $env:SSH_ASKPASS = $previousAskpass
      $env:SSH_ASKPASS_REQUIRE = $previousRequire
      $env:DISPLAY = $previousDisplay
      Remove-Item -LiteralPath $AskpassInput -Force -ErrorAction SilentlyContinue
    }
    if ($exitCode -ne 0) {
      $result | Out-String | Write-Output
      $result | ForEach-Object {
        Write-Output ("FRP_SSH_DIAGNOSTIC=" + (
          [string]$_ -replace '(?i)(password|authentication).{0,100}', '$1=<redacted>'))
      }
      $failureLines = @($result | Where-Object {
        $_ -match 'known|host key|authenticat|permission|connect|refused|timeout|configuration|bad '
      })
      throw "The reviewed SSH action failed with exit code $exitCode. $($failureLines -join ' ')"
    }
    return @($result | ForEach-Object { [string]$_ })
  } finally {
    Remove-Item -LiteralPath $AskpassInput -Force -ErrorAction SilentlyContinue
  }
}

function New-FrpAskpass {
  param([string]$Path)
  $contents = "@echo off`r`nmore < `"%~dp0askpass-input.txt`"`r`n"
  [IO.File]::WriteAllText($Path, $contents, [Text.UTF8Encoding]::new($false))
}

function Get-FrpIdentity {
  param([string]$Executable, [string]$Algorithm)
  $lines = @(& $Executable -l -f "$Algorithm.pub")
  if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1) {
    throw "Unable to derive the reviewed $Algorithm identity."
  }
  $parts = $lines[0].Trim() -split '\s+'
  if ($parts.Count -lt 2 -or $parts[1] -match '[\r\n]' -or
      $parts[1].Length -gt 512) {
    throw "The reviewed $Algorithm public identity has an unexpected shape."
  }
  return "$Algorithm $($parts[1])"
}

function Get-FrpVersion {
  $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/fatedier/frp/releases/latest' `
    -Headers @{ 'User-Agent' = 'vps-ssh-launcher-frp-setup' }
  $version = [string]$release.tag_name
  if ($version -notmatch '^v(?<number>[0-9]+\.[0-9]+\.[0-9]+)$') {
    throw 'The official frp release tag has an unexpected shape.'
  }
  $number = $Matches.number
  $assets = @($release.assets | Where-Object { $_.name -in @(
    "frp_${number}_linux_amd64.tar.gz",
    "frp_${number}_windows_amd64.zip",
    'frp_sha256_checksums.txt'
  ) })
  if ($assets.Count -ne 3) {
    throw 'The official frp release lacks one of the reviewed assets.'
  }
  $checksumAsset = $assets | Where-Object name -eq 'frp_sha256_checksums.txt'
  $checksumText = Get-FrpWebText -Uri $checksumAsset.browser_download_url
  return [pscustomobject]@{
    Version = $number
    Release = $release
    Assets = $assets
    ChecksumAsset = $checksumAsset
    Checksums = [string]$checksumText
  }
}

function Get-FrpWebText {
  param([string]$Uri)
  $response = Invoke-WebRequest -Uri $Uri `
    -Headers @{ 'User-Agent' = 'vps-ssh-launcher-frp-setup' } -UseBasicParsing
  if ($response.Content -is [byte[]]) {
    $responseContent = [Text.Encoding]::UTF8.GetString($response.Content)
  } else {
    $responseContent = [string]$response.Content
  }
  return $responseContent
}

function Assert-FrpChecksum {
  param(
    [string]$Directory,
    [string]$AssetName,
    [string]$Checksums
  )
  $line = @($Checksums -split "`r?`n" | Where-Object {
    [string]::Join("`n", @($_)).EndsWith(" $AssetName", [StringComparison]::Ordinal)
  })
  if ($line.Count -ne 1 -or $line[0] -notmatch '^(?<hash>[0-9a-fA-F]{64})\s') {
    throw "No single reviewed SHA-256 checksum exists for $AssetName."
  }
  $assetPath = [IO.Path]::Combine($Directory, $AssetName)
  $actual = (Get-FileHash -LiteralPath $assetPath -Algorithm SHA256).Hash
  if ($actual -ne $Matches.hash.ToLowerInvariant()) {
    throw "The downloaded frp asset failed SHA-256 verification: $AssetName"
  }
  return $actual.ToLowerInvariant()
}

function Find-FrpMarker {
  param([string]$Executable, [string]$PublicKey, [string]$Marker)
  $lines = @(& $Executable -l -f @("marker=$Marker", "ssh-ed25519=$PublicKey"))
  if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1) {
    throw 'The marker/identity lookup did not return exactly one identity block.'
  }
  $matches = @($lines[0].Trim() -split '\s+' | Where-Object { $_ -eq $Marker })
  if ($matches.Count -ne 1) {
    throw 'The marker/identity lookup returned a foreign or ambiguous block.'
  }
}

function Test-FrpControlTransport {
  param([string]$Address, [int]$Port, [byte[]]$TlsHello)
  $client = [Net.Sockets.TcpClient]::new()
  try {
    $task = $client.ConnectAsync($Address, $Port)
    if (-not $task.Wait([TimeSpan]::FromSeconds(6))) {
      throw "Public TCP $Port stayed filtered or timed out."
    }
    $client.ReceiveTimeout = 5000
    $stream = $client.GetStream()
    $stream.Write($TlsHello, 0, $TlsHello.Length)
    $stream.Flush()
    $reader = [IO.BinaryReader]::new($stream, [Text.Encoding]::ASCII, $true)
    $first = $reader.ReadByte()
    $second = $reader.ReadByte()
    $third = $reader.ReadByte()
    $length = $reader.ReadByte()
    if ($first -ne 22 -or $second -ne 3 -or $third -lt 1 -or $third -gt 4 -or
        $length -ne 2) {
      throw "Public TCP $Port did not return a TLS ServerHello."
    }
    $record = $reader.ReadBytes(2)
    $legacyVersion = [BitConverter]::ToUInt16([byte[]]@($record[1], $record[0]), 0)
    if ($legacyVersion -notin @(0x0303, 0x0304)) {
      throw "Public TCP $Port returned an unexpected TLS legacy version."
    }
    return [pscustomobject]@{
      Port = $Port
      State = 'TLS_HANDSHAKE_REACHED'
    }
  } finally {
    $client.Dispose()
  }
}

function ConvertTo-FrpTlsHello {
  param([string]$ServerName)
  $name = [Text.Encoding]::ASCII.GetBytes($ServerName)
  $random = [byte[]]::new(32)
  [Security.Cryptography.RandomNumberGenerator]::Fill($random)
  $extensions = [IO.MemoryStream]::new()
  if ($ServerName -match '^[A-Za-z0-9.-]+$' -and $ServerName.Length -le 253) {
    $serverExtension = [IO.MemoryStream]::new()
    $contentLength = 3 + $name.Length
    $extensionLength = 2 + $contentLength
    $serverExtension.Write([byte[]]@(0, 0, [byte]($extensionLength - 2), 0, [byte]($contentLength - 2), 0, [byte]$name.Length), 0, 7)
    $serverExtension.Write($name, 0, $name.Length)
    $serverExtension.Write([byte[]]@(0x00, 0x0a, 0x00, 0x08, 0x00, 0x06, 0x00, 0x1d, 0x00, 0x17, 0x00, 0x18), 0, 12)
    $extensions.Write([byte[]]@(0x00, 0x2b, 0x00, 0x03, 0x02, 0x03, 0x04), 0, 6)
    $content = $serverExtension.ToArray()
    $serverExtension.Dispose()
  }
  $content = $extensions.ToArray()
  $extensions.Dispose()
  $extensionBlock = [byte[]]::new($content.Length + 2)
  $extensionBlock[0] = [byte](($content.Length -shr 8) -band 0xff)
  $extensionBlock[1] = [byte]($content.Length -band 0xff)
  [Array]::Copy($content, 0, $extensionBlock, 2, $content.Length)
  $body = [IO.MemoryStream]::new()
  $body.Write([byte[]]@(3, 3), 0, 2)
  $body.Write($random, 0, $random.Length)
  $body.Write([byte[]]@(32), 0, 1)
  $body.Write($random, 0, $random.Length)
  $body.Write([byte[]]@(0x13, 0x01, 0x01, 0x00), 0, 4)
  $body.Write($extensionBlock, 0, $extensionBlock.Length)
  $handshake = $body.ToArray()
  $body.Dispose()
  $record = [byte[]]::new($handshake.Length + 9)
  $record[0] = 22
  $record[1] = 3
  $record[2] = 1
  $record[3] = [byte](($handshake.Length -shr 8) -band 0xff)
  $record[4] = [byte]($handshake.Length -band 0xff)
  $record[5] = 1
  $record[6] = [byte](($handshake.Length -shr 16) -band 0xff)
  $record[7] = [byte](($handshake.Length -shr 8) -band 0xff)
  $record[8] = [byte]($handshake.Length -band 0xff)
  [Array]::Copy($handshake, 0, $record, 9, $handshake.Length)
  [Array]::Clear($random, 0, $random.Length)
  [Array]::Clear($handshake, 0, $handshake.Length)
  return $record
}

function Get-FrpResolvedPublicState {
  param(
    [string]$Address,
    [int]$Port,
    [string]$KnownHosts,
    [string]$ScanCommand,
    [string]$ExpectedKey
  )
  $known = @(Get-Content -LiteralPath $KnownHosts)
  if ($known.Count -ne 1 -or
      $known[0] -notmatch ("^\[" + [Regex]::Escape($Address) + "\]:$Port\s+ssh-ed25519\s+(?<key>[A-Za-z0-9+/]+={0,2})$")) {
    throw 'The pinned SSH identity is not the reviewed host/port identity.'
  }
  $pinnedKey = $Matches.key
  if ($ExpectedKey -and $pinnedKey -ne $ExpectedKey) {
    throw 'The pinned SSH identity does not match the reviewed public key.'
  }
  $scannedRaw = @(& $ScanCommand -p $Port -t ed25519 $Address)
  $scanned = @($scannedRaw | Where-Object {
    $_ -notmatch '^#\s' -and -not [string]::IsNullOrWhiteSpace($_)
  })
  if ($LASTEXITCODE -ne 0 -or $scanned.Count -ne 1 -or
      $scanned[0].Trim() -notmatch '^[^ ]+\s+ssh-ed25519\s+(?<scanKey>[A-Za-z0-9+/]+={0,2})$') {
    throw 'The public SSH identity changed from its pinned value.'
  }
  $scanKey = $Matches.scanKey
  if ($scanKey -ne $pinnedKey) {
    throw 'The public SSH identity changed from its pinned value.'
  }
  return [pscustomobject]@{
    PinnedKey = $pinnedKey
    ScanKey = $scanKey
  }
}

function Get-FrpExecutableHash {
  param([string]$Directory, [string]$Version)
  $checksums = Invoke-RestMethod -Uri "https://api.github.com/repos/fatedier/frp/releases/tags/v$Version" `
    -Headers @{ 'User-Agent' = 'vps-ssh-launcher-frp-setup' }
  $checksumAsset = $archiveAsset = $null
  foreach ($asset in $checksums.assets) {
    if ($asset.name -eq 'frp_sha256_checksums.txt') { $checksumAsset = $asset }
    if ($asset.name -eq "frp_${Version}_windows_amd64.zip") { $archiveAsset = $asset }
  }
  if (-not $checksumAsset -or -not $archiveAsset) {
    throw 'The reviewed frp executable cannot be authenticated.'
  }
  $text = Get-FrpWebText -Uri $checksumAsset.browser_download_url
  $line = @($text -split "`r?`n" | Where-Object {
    [string]::Join("`n", @($_)).EndsWith(" $($archiveAsset.name)", [StringComparison]::Ordinal)
  })
  if ($line.Count -ne 1 -or $line[0] -notmatch '^(?<hash>[0-9a-fA-F]{64})\s') {
    throw 'The executable archive has no single official checksum.'
  }
  return [pscustomobject]@{
    ArchiveHash = $Matches.hash.ToLowerInvariant()
    InstalledHash = (Get-FileHash -LiteralPath (Join-Path $Directory 'frpc.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
  }
}

function Install-FrpSetup {
  $publicAddress = '8.163.41.198'
  Enter-FrpStep -Name 'resolve-profile'
  $configuration = Get-FrpProfile -Name aliyun -ExpectedAddress $publicAddress
  $configuration.Password = $null
  $stateDirectory = Join-Path $env:USERPROFILE '.frp'
  $knownHosts = Join-Path $stateDirectory 'bootstrap-ssh\known_hosts'
  $scan = 'C:\Program Files\Git\usr\bin\ssh-keyscan.exe'
  foreach ($required in @($scan, $knownHosts)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
      throw "A required reviewed frp tool is unavailable: $required"
    }
  }
  $version = Get-FrpVersion
  Enter-FrpStep -Name 'verified-release'
  $versionRoot = Join-Path $stateDirectory "release-$($version.Version)"
  $localRoot = Join-Path $stateDirectory 'bin'
  $windowsArchive = [IO.Path]::Combine($versionRoot, 'frp_windows_amd64.zip')
  $linuxArchive = [IO.Path]::Combine($versionRoot, 'frp_linux_amd64.tar.gz')
  $checksumFile = [IO.Path]::Combine($versionRoot, 'frp_sha256_checksums.txt')
  New-Item -ItemType Directory -Path $versionRoot -Force | Out-Null
  if (-not (Test-Path -LiteralPath $checksumFile -PathType Leaf)) {
    $checksumTemporary = "$checksumFile.download"
    if (Test-Path -LiteralPath $checksumTemporary) {
      throw "An unresolved checksum download exists: $checksumTemporary"
    }
    Invoke-WebRequest -Uri $version.ChecksumAsset.browser_download_url `
      -Headers @{ 'User-Agent' = 'vps-ssh-launcher-frp-setup' } `
      -OutFile $checksumTemporary -UseBasicParsing
    Move-Item -LiteralPath $checksumTemporary -Destination $checksumFile
  } elseif (Test-Path -LiteralPath "$checksumFile.download") {
    $pendingChecksums = Get-Content -LiteralPath "$checksumFile.download" -Raw
    if ($pendingChecksums -ceq (Get-Content -LiteralPath $checksumFile -Raw)) {
      Move-Item -LiteralPath "$checksumFile.download" -Destination $checksumFile -Force
    } else {
      throw "An unresolved checksum download exists: $checksumFile.download"
    }
  }
  $localChecksums = Get-Content -LiteralPath $checksumFile -Raw
  if ($localChecksums.TrimEnd() -ne $version.Checksums.TrimEnd()) {
    throw 'The local checksum file differs from the current official release.'
  }
  foreach ($asset in $version.Assets) {
    if ($asset.name -eq 'frp_sha256_checksums.txt') { continue }
    $target = if ($asset.name.EndsWith('.zip')) { $windowsArchive } else { $linuxArchive }
    $assetName = [string]$asset.name
    $checksumLine = @($localChecksums -split "`r?`n" | Where-Object {
      [string]::Join("`n", @($_)).EndsWith(" $assetName", [StringComparison]::Ordinal)
    })
    if ($checksumLine.Count -ne 1 -or
        $checksumLine[0] -notmatch '^(?<hash>[0-9a-fA-F]{64})\s') {
      throw "No single official checksum exists for $($asset.name)."
    }
    $expectedArchiveHash = $Matches.hash.ToLowerInvariant()
  if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
      $temporaryAsset = "$target.download"
      if (Test-Path -LiteralPath $temporaryAsset -PathType Leaf) {
        $partialHash = (Get-FileHash -LiteralPath $temporaryAsset -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($partialHash -ne $expectedArchiveHash) {
          throw "An unresolved archive download exists: $temporaryAsset"
        }
      } else {
        Invoke-WebRequest -Uri $asset.browser_download_url `
          -Headers @{ 'User-Agent' = 'vps-ssh-launcher-frp-setup' } -OutFile $temporaryAsset -UseBasicParsing
      }
      Move-Item -LiteralPath $temporaryAsset -Destination $target
    }
    $actualArchiveHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualArchiveHash -ne $expectedArchiveHash) {
      throw "The frp archive failed its official checksum: $assetName"
    }
    Enter-FrpStep -Name "checked-one-$($script:StepNumber)-$assetName"
  }
  Enter-FrpStep -Name 'archive-verified'
  $binDirectory = [IO.Path]::Combine($localRoot, $version.Version)
  New-Item -ItemType Directory -Path $binDirectory -Force | Out-Null
  $localFrpc = Join-Path $binDirectory 'frpc.exe'
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [IO.Compression.ZipFile]::ExtractToDirectory($windowsArchive, $versionRoot, $true)
  Enter-FrpStep -Name 'windows-archive-extracted'
  $extractedCandidates = @(Get-ChildItem -LiteralPath $versionRoot -Recurse -Filter 'frpc.exe' -File |
    Where-Object { $_.DirectoryName -ne $binDirectory } |
    Sort-Object LastWriteTime -Descending)
  if ($extractedCandidates.Count -ne 1 -or
      $extractedCandidates[0].DirectoryName -notmatch [Regex]::Escape($version.Version)) {
    throw 'The official frp archive did not contain one versioned frpc.exe.'
  }
  $extracted = $extractedCandidates[0].FullName
  $extractedHash = (Get-FileHash -LiteralPath $extracted -Algorithm SHA256).Hash.ToLowerInvariant()
  if ((Test-Path -LiteralPath $localFrpc -PathType Leaf) -and
      (Get-FileHash -LiteralPath $localFrpc -Algorithm SHA256).Hash.ToLowerInvariant() -ne
        $extractedHash) {
    throw 'The installed frpc.exe does not match the official release.'
  }
  if (-not (Test-Path -LiteralPath $localFrpc -PathType Leaf)) {
    Copy-Item -LiteralPath $extracted -Destination $localFrpc
  }
  Enter-FrpStep -Name 'frpc-projected'
  $installedVersion = @(& $localFrpc --version)
  if ($LASTEXITCODE -ne 0 -or -not ($installedVersion -join '').Contains($version.Version)) {
    throw 'The local frpc executable has the wrong version.'
  }
  $localChecksum = Get-FrpExecutableHash -Directory $binDirectory -Version $version.Version
  Enter-FrpStep -Name 'executable-authenticated'
  New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
  $pinPath = [IO.Path]::Combine($stateDirectory, 'server-ssh-ed25519.pin')
    if (-not (Test-Path -LiteralPath $pinPath -PathType Leaf)) {
      $scannedRaw = @(& $scan -p 22 -t ed25519 $publicAddress)
      $scannedInitial = @($scannedRaw | Where-Object {
        $_ -notmatch '^#\s' -and -not [string]::IsNullOrWhiteSpace($_)
      })
      if ($LASTEXITCODE -ne 0 -or $scannedInitial.Count -ne 1 -or
          $scannedInitial[0].Trim() -notmatch '^[^ ]+\s+ssh-ed25519\s+(?<initialKey>[A-Za-z0-9+/]+={0,2})$') {
        throw 'Unable to obtain exactly one initial SSH identity for trust-on-first-use.'
      }
      $initialKey = $Matches.initialKey
      $knownLine = "[$publicAddress]:22 ssh-ed25519 $initialKey"
      [IO.File]::WriteAllText($pinPath, "$initialKey`n", [Text.UTF8Encoding]::new($false))
      [IO.File]::WriteAllText($knownHosts, "$knownLine`n", [Text.UTF8Encoding]::new($false))
    }
  $sshPin = (Get-Content -LiteralPath $pinPath -Raw).Trim()
  $publicState = Get-FrpResolvedPublicState -Address $publicAddress -Port 22 `
    -KnownHosts $knownHosts -ScanCommand $scan -ExpectedKey $sshPin
  Enter-FrpStep -Name 'ssh-identity-matched'
  $stateDirectoryFull = [IO.Path]::GetFullPath($stateDirectory)
  $bootstrapDirectory = [IO.Path]::GetFullPath(
    (Join-Path $stateDirectory 'bootstrap-ssh'))
  if (-not $bootstrapDirectory.StartsWith(
    $stateDirectoryFull.TrimEnd([IO.Path]::DirectorySeparatorChar) +
      [IO.Path]::DirectorySeparatorChar)) {
    throw 'The reviewed bootstrap directory escapes the private frp state root.'
  }
  New-Item -ItemType Directory -Path $bootstrapDirectory -Force | Out-Null
  $askpassInput = Join-Path $bootstrapDirectory 'askpass-input.txt'
  $askpassCommand = Join-Path $bootstrapDirectory 'askpass-reviewed.cmd'
  New-FrpAskpass -Path $askpassCommand
  foreach ($temporary in @($askpassInput)) {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
  }
  $sshProfile = Get-FrpProfile -Name aliyun -ExpectedAddress $publicAddress
  $probe = Invoke-FrpRemote -Profile $sshProfile -KnownHosts $knownHosts `
    -AskpassInput $askpassInput -AskpassCommand $askpassCommand `
    -Command @'
set -eu
printf 'REMOTE_HOST=%s\n' "$(hostname)"
printf 'REMOTE_ARCH=%s\n' "$(uname -m)"
test "$(uname -m)" = x86_64
test "$(id -u)" = 0
printf 'REMOTE_ROOT=YES\n'
systemctl show frps-aliyun.service -p LoadState -p ActiveState -p UnitFileState |
  grep -Eq 'LoadState=loaded' || true
systemctl show frps.service -p LoadState -p ActiveState |
  grep -Eq 'LoadState=loaded' || true
ss -Htln | awk '$4 ~ /:(5200|7000|27000|52443)$/ || true'
'@ -TimeoutSeconds 20
  Enter-FrpStep -Name 'remote-ssh-read'
  if (($probe -join "`n") -notmatch 'REMOTE_ROOT=YES') {
    throw 'The reviewed Aliyun SSH profile lacks the required root access.'
  }
  $probe |
    Where-Object { $_ -match '^REMOTE_' } |
    ForEach-Object { Write-Output $_ }
  Write-Output 'FRP_FILES=official-release-verified'
  Write-Output "FRP_VERSION=$($version.Version)"
  Write-Output "FRP_EXECUTABLE_SHA256=$($localChecksum.InstalledHash)"
  Write-Output "FRP_WINDOWS_ARCHIVE_SHA256=$($localChecksum.ArchiveHash)"
  Write-Output "FRP_SSH_IDENTITY_MATCH=$($publicState.ScanKey -eq $publicState.PinnedKey)"
  Write-Output "FRP_PUBLIC_ADDRESS=$publicAddress"
  Write-Output 'FRP_FILESYSTEM_PRECHECK=PASS'
  Write-Output 'FRP_LIVE_ACCEPTANCE_PENDING'
  if ($Apply) {
    Write-Output 'FRP_REMOTE_MUTATION=NOT_RUN'
    Write-Output 'The reviewed precheck passed; remote server installation requires separate staged apply.'
  }
}

function Get-FrpStatus {
  $stateDirectory = Join-Path $env:USERPROFILE '.frp'
  $task = Get-ScheduledTask -TaskName 'vps-ssh-launcher-frpc' -ErrorAction SilentlyContinue
  $localFrpc = if ($task -and $task.Actions.Count -eq 1) {
    [string]$task.Actions[0].Execute
  } else { $null }
  $processes = if (Test-Path -LiteralPath $localFrpc) {
    @(Get-CimInstance Win32_Process -Filter "Name='frpc.exe'" | Where-Object {
      $_.ExecutablePath -and $_.ExecutablePath -eq $localFrpc
    })
  } else { @() }
  if ($processes.Count -gt 0) {
    $frpcProcessId = [int]$processes[0].ProcessId
    $connections = @(Get-NetTCPConnection -OwningProcess $frpcProcessId `
      -ErrorAction SilentlyContinue | Where-Object State -eq Established)
    Write-Output "FRPC_ESTABLISHED_CONNECTION_COUNT=$($connections.Count)"
  } else {
    Write-Output 'FRPC_ESTABLISHED_CONNECTION_COUNT=0'
  }
  $listener = @(Get-NetTCPConnection -LocalPort 5200 -State Listen -ErrorAction SilentlyContinue)
  $remoteListener = @(Get-NetTCPConnection -LocalPort $ProfilePort -State Listen -ErrorAction SilentlyContinue)
  if ($task) {
    Write-Output "FRPC_TASK_STATE=$($task.State)"
    Write-Output "FRPC_TASK_ACTION=$($task.Actions[0].Execute) $($task.Actions[0].Arguments)"
  } else {
    Write-Output 'FRPC_TASK_STATE=ABSENT'
  }
  Write-Output "FRPC_REVIEWED_PROCESS_COUNT=$($processes.Count)"
  Write-Output "FRPC_MAPPING_LISTENER_COUNT=$($listener.Count)"
  Write-Output "FRPC_PROFILE_LISTENER_COUNT=$($remoteListener.Count)"
  if ($listener) {
    Write-Output "FRPC_MAPPING_LISTENER_PORT=$($listener[0].LocalPort)"
  }
  $token = Join-Path $stateDirectory 'token.dpapi'
  $certificate = Join-Path $stateDirectory 'server.crt'
  Write-Output "FRPC_TOKEN_DPAPI_PRESENT=$([bool](Test-Path -LiteralPath $token -PathType Leaf))"
  Write-Output "FRPC_TLS_CERT_PRESENT=$([bool](Test-Path -LiteralPath $certificate -PathType Leaf))"
  Write-Output 'FRP_STATUS=OBSERVED'
}

function Test-FrpAcceptance {
  # The final BWG-hosted topology has no HOME mapping. When no
  # acceptance command is supplied, validation only inspects the linked
  # BWG host and ECS host; it never starts a local frpc.
  if (-not $AcceptanceCommand) {
    throw 'Acceptance requires a command or an explicitly separate host verifier.'
  }
  if ([string]::IsNullOrWhiteSpace($AcceptanceCommand) -or
      [string]::IsNullOrWhiteSpace($ExpectedEgress)) {
    throw 'Acceptance requires a command and expected downstream egress.'
  }
  $stateDirectory = Join-Path $env:USERPROFILE '.frp'
  $task = Get-ScheduledTask -TaskName 'vps-ssh-launcher-frpc' -ErrorAction SilentlyContinue
  if (-not $AcceptanceDirect) {
    if (-not $task -or $task.Actions.Count -ne 1 -or
        $task.Actions[0].Arguments -ne
          '-c "C:\Users\sciman\.frp\frpc.toml"') {
      throw 'The reviewed control-plane task is not installed.'
    }
  }
  $controlConfig = Get-Content -Raw -LiteralPath (Join-Path $stateDirectory 'frpc.toml')
  $mappingPath = if ($AcceptanceDirect) {
    [IO.Path]::Combine($env:TEMP, 'frp-acceptance-review-mapping.toml')
  } else {
    Join-Path $stateDirectory 'home-bwg-reality.toml'
  }
  $mappingConfig = Get-Content -Raw -LiteralPath $mappingPath
  $controlPortMatch = [Regex]::Match(
    $controlConfig, '(?m)^serverPort = (?<port>[0-9]+)$')
  $mappingPortMatch = [Regex]::Match(
    $mappingConfig, '(?m)^serverPort = (?<port>[0-9]+)$')
  $homePortMatch = [Regex]::Match(
    $mappingConfig, '(?m)^localPort = (?<port>[0-9]+)$')
  $remotePortMatch = [Regex]::Match(
    $mappingConfig, '(?m)^remotePort = (?<port>[0-9]+)$')
  if (-not $controlPortMatch.Success -or
      [int]$controlPortMatch.Groups['port'].Value -ne $ProfilePort -or
      -not $mappingPortMatch.Success -or
      [int]$mappingPortMatch.Groups['port'].Value -ne $ProfilePort -or
      -not $homePortMatch.Success -or
      [int]$homePortMatch.Groups['port'].Value -ne $HomePort -or
      -not $remotePortMatch.Success -or
      [int]$remotePortMatch.Groups['port'].Value -ne $ServerPort) {
    Write-Output "PROFILE_PORT=$ProfilePort SERVER_PORT=$ServerPort HOME_PORT=$HomePort"
    Write-Output "CONTROL=$($controlPortMatch.Groups['port'].Value)"
    Write-Output "MAPPING=$($mappingPortMatch.Groups['port'].Value)"
    Write-Output "LOCAL=$($homePortMatch.Groups['port'].Value)"
    Write-Output "REMOTE=$($remotePortMatch.Groups['port'].Value)"
    throw 'The deployed configuration does not match the requested ports.'
  }
  if ($controlConfig -match '\[\[proxies\]\]') {
    throw 'The control-plane configuration contains a local mapping.'
  }
  $serverName = 'frp.sciman.top'
  if ($controlConfig -notmatch '(?m)^transport\.tls\.serverName = "(?<sni>[^"]+)"$') {
    throw 'The control-plane TLS server name is not explicit.'
  }
  $serverName = $Matches.sni
  $frpc = Join-Path $stateDirectory "bin\0.72.0\frpc.exe"
  if (-not (Test-Path -LiteralPath $frpc -PathType Leaf)) {
    throw 'The reviewed frpc executable is missing.'
  }
  $privateRoot = Join-Path $env:TEMP ('frp-acceptance-' + [Guid]::NewGuid().ToString('N'))
  $mapping = $null
  New-Item -ItemType Directory -Path $privateRoot | Out-Null
  try {
    $tokenPath = [IO.Path]::Combine($privateRoot, 'token.stage')
    $controlCopy = [IO.Path]::Combine($privateRoot, 'control.stage.toml')
    $mappingCopy = [IO.Path]::Combine($privateRoot, 'mapping.stage.toml')
    & (Join-Path $PSScriptRoot 'frp_relay_dpapi.ps1') -Mode Unprotect `
      -InputPath (Join-Path $stateDirectory 'token.dpapi') -OutputPath $tokenPath
    $runtimeControl = $controlConfig.Replace(
      'C:/Users/sciman/.frp/token.dpapi', $tokenPath.Replace('\', '/'))
    $runtimeMapping = $mappingConfig.Replace(
      'C:/Users/sciman/.frp/token.dpapi', $tokenPath.Replace('\', '/'))
    $expectedProfileName = [IO.Path]::GetFileNameWithoutExtension($mappingPath)
    $runtimeMapping = $runtimeMapping.Replace(
      'home-bwg-reality-relay', "home-bwg-reality-relay-$expectedProfileName")
    foreach ($pair in @(
      @($controlCopy, $runtimeControl),
      @($mappingCopy, $runtimeMapping)
    )) {
      [IO.File]::WriteAllText($pair[0], $pair[1], [Text.UTF8Encoding]::new($false))
      & $frpc verify -c $pair[0] | Out-Null
      if ($LASTEXITCODE -ne 0) { throw 'A staged frpc config failed verification.' }
    }
    $owner = (Get-CimInstance Win32_Process -Filter "Name='frpc.exe'" |
      Where-Object ExecutablePath -eq $frpc | Select-Object -First 1)
    if (-not $owner -and -not $AcceptanceDirect) {
      throw 'The reviewed frpc control plane is not running.'
    }
    if (-not $owner -and $AcceptanceDirect) {
      $owner = Start-Process -FilePath $frpc -ArgumentList @('-c', $controlCopy) `
        -RedirectStandardOutput ([IO.Path]::Combine($privateRoot, 'control.stdout.txt')) `
        -RedirectStandardError ([IO.Path]::Combine($privateRoot, 'control.stderr.txt')) `
        -WindowStyle Hidden -PassThru
      $deadline = [DateTime]::UtcNow.AddSeconds(20)
      while (-not (Get-NetTCPConnection -OwningProcess $owner.Id -ErrorAction SilentlyContinue |
          Where-Object State -eq Established) -and
          [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 500
      }
    }
    $ownerId = [int]$owner.ProcessId
    $stdout = [IO.Path]::Combine($privateRoot, 'mapping.stdout.txt')
    $stderr = [IO.Path]::Combine($privateRoot, 'mapping.stderr.txt')
    $before = @(Get-NetTCPConnection -OwningProcess $ownerId -ErrorAction SilentlyContinue)
    $mapping = Start-Process -FilePath $frpc -ArgumentList @('-c', $mappingCopy) `
      -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
      -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    $mappingLive = $false
    while (-not $mapping.HasExited -and [DateTime]::UtcNow -lt $deadline) {
      $mappingConnections = @(Get-NetTCPConnection -OwningProcess $ownerId `
        -ErrorAction SilentlyContinue | Where-Object State -eq Established)
      if ($mappingConnections.Count -gt $before.Count -and
          (Get-NetTCPConnection -LocalPort $HomePort -State Listen -ErrorAction SilentlyContinue)) {
        $mappingLive = $true
        break
      }
      Start-Sleep -Milliseconds 500
    }
    if (-not $mappingLive) {
      $safeOutput = ((Get-Content -LiteralPath $stdout -ErrorAction SilentlyContinue) +
        (Get-Content -LiteralPath $stderr -ErrorAction SilentlyContinue)) |
        ForEach-Object { $_ -replace '[0-9a-f]{32,}', '[REDACTED_HEX]' } |
        Select-Object -Last 3
      Write-Output 'FRPC_MAPPING=NOT_ESTABLISHED'
      $safeOutput | ForEach-Object { Write-Output $_ }
      return $false
    }
    Write-Output "FRPC_MAPPING=ESTABLISHED_PORT_$HomePort"
    $tlsHello = ConvertTo-FrpTlsHello -ServerName $serverName
    try {
      $controlState = Test-FrpControlTransport -Address '8.163.41.198' `
        -Port $ProfilePort -TlsHello $tlsHello
    } catch {
      Write-Output 'FRP_PUBLIC_CONTROL=BLOCKED'
      Write-Output $_.Exception.Message
      return $false
    } finally {
      [Array]::Clear($tlsHello, 0, $tlsHello.Length)
    }
    Write-Output "FRP_PUBLIC_CONTROL=$($controlState.State)"
    $acceptanceStart = [DateTime]::UtcNow
    $acceptanceResult = @(& {
      param([string]$InputCode)
      $block = [ScriptBlock]::Create($InputCode)
      & $block 2>&1
    } $AcceptanceCommand)
    $acceptanceText = ($acceptanceResult | ForEach-Object { [string]$_ }) -join "`n"
    $safeAcceptance = ($acceptanceText -split "`r?`n") |
      ForEach-Object { $_ -replace '[0-9a-f]{32,}', '[REDACTED_HEX]' } |
      Select-Object -Last 10
    $safeAcceptance | ForEach-Object { Write-Output $_ }
    if ($acceptanceText -notmatch
        [Regex]::Escape($ExpectedEgress) -or
        $acceptanceText -notmatch 'GENERATE_204=(?<code>[0-9]{3})' -or
        $Matches.code -ne '204') {
      throw 'The real client did not reach both the expected landing and downstream egress.'
    }
    $remote = Get-FrpProfile -Name aliyun -ExpectedAddress '8.163.41.198'
    $knownHosts = Join-Path $stateDirectory 'bootstrap-ssh\known_hosts'
    $askpassInput = Join-Path $stateDirectory 'bootstrap-ssh\askpass-input.txt'
    $askpassCommand = Join-Path $stateDirectory 'bootstrap-ssh\askpass-reviewed.cmd'
    if (-not (Test-Path -LiteralPath $askpassCommand -PathType Leaf)) {
      New-FrpAskpass -Path $askpassCommand
    }
    $remoteLog = Invoke-FrpRemote -Profile $remote -KnownHosts $knownHosts `
      -AskpassInput $askpassInput -AskpassCommand $askpassCommand `
      -Command @'
set -eu
since="$(date -u -d '2 minutes ago' '+%Y-%m-%d %H:%M:%S')"
journalctl -u frps-aliyun.service --since "$since" --no-pager -o cat |
  grep -E 'login|proxy' | tail -8 || true
'@ -TimeoutSeconds 20
    $remoteLog | ForEach-Object {
      $_ -replace '[0-9a-f]{16,}', '[REDACTED_RUN_ID]'
    }
    Write-Output 'FRP_LIVE_ACCEPTANCE=PASS'
    Write-Output 'ACCEPTANCE_RESULT=PASS'
    return $true
  } finally {
    if ($mapping -and -not $mapping.HasExited) {
      Stop-Process -Id $mapping.Id -Force
      $mapping.WaitForExit(5000)
    }
    if ($AcceptanceDirect -and $owner -and -not $owner.HasExited) {
      Stop-Process -Id $owner.Id -Force
      $owner.WaitForExit(5000)
    }
    $resolvedPrivateRoot = [IO.Path]::GetFullPath($privateRoot)
    $resolvedTemp = [IO.Path]::GetFullPath($env:TEMP)
    if ($resolvedPrivateRoot.StartsWith(
        $resolvedTemp.TrimEnd([IO.Path]::DirectorySeparatorChar) +
          [IO.Path]::DirectorySeparatorChar) -and
        (Split-Path -Leaf $resolvedPrivateRoot) -like 'frp-acceptance-*') {
      Remove-Item -LiteralPath $resolvedPrivateRoot -Recurse -Force
    }
  }
}

try {
  if ($Setup) {
    if ($Apply) {
      Install-FrpSetup
    } else {
      $version = Get-FrpVersion
      Write-Output "FRP_VERSION=$($version.Version)"
      Write-Output 'FRP_SETUP_DRY_RUN=PASS'
    }
  } elseif ($Acceptance) {
    if (-not (Test-FrpAcceptance)) { exit 2 }
  } else {
    Get-FrpStatus
  }
} catch {
  Write-Error $_.Exception.Message
  exit 1
}

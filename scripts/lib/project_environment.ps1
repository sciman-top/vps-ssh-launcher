function Initialize-WindowsProcessEnvironment {
  if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) {
    return
  }

  $windowsRoot = $env:SYSTEMROOT
  if (-not $windowsRoot) {
    $windowsRoot = $env:WINDIR
  }
  if (-not $windowsRoot -and (Test-Path -LiteralPath "C:\Windows")) {
    $windowsRoot = "C:\Windows"
  }

  if ($windowsRoot) {
    if (-not $env:SYSTEMROOT) {
      $env:SYSTEMROOT = $windowsRoot
    }
    if (-not $env:WINDIR) {
      $env:WINDIR = $windowsRoot
    }

    $cmdExe = Join-Path $windowsRoot "System32\cmd.exe"
    if ((-not $env:COMSPEC) -and (Test-Path -LiteralPath $cmdExe)) {
      $env:COMSPEC = $cmdExe
    }
  }

  if (-not $env:USERPROFILE) {
    $userProfile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    if ($userProfile -and (Test-Path -LiteralPath $userProfile)) {
      $env:USERPROFILE = $userProfile
    } elseif ($HOME -and (Test-Path -LiteralPath $HOME)) {
      $env:USERPROFILE = $HOME
    }
  }

  if (-not $env:APPDATA) {
    $roamingAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
    if ($roamingAppData -and (Test-Path -LiteralPath $roamingAppData)) {
      $env:APPDATA = $roamingAppData
    }
  }

  if (-not $env:LOCALAPPDATA) {
    $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ($localAppData -and (Test-Path -LiteralPath $localAppData)) {
      $env:LOCALAPPDATA = $localAppData
    }
  }

  if ($env:USERPROFILE) {
    $roamingAppData = Join-Path $env:USERPROFILE "AppData\Roaming"
    if ((-not $env:APPDATA) -and (Test-Path -LiteralPath $roamingAppData)) {
      $env:APPDATA = $roamingAppData
    }

    $localAppData = Join-Path $env:USERPROFILE "AppData\Local"
    if ((-not $env:LOCALAPPDATA) -and (Test-Path -LiteralPath $localAppData)) {
      $env:LOCALAPPDATA = $localAppData
    }
  }

  if (-not $env:PROGRAMDATA) {
    $programData = [Environment]::GetFolderPath([Environment+SpecialFolder]::CommonApplicationData)
    if ($programData -and (Test-Path -LiteralPath $programData)) {
      $env:PROGRAMDATA = $programData
    } elseif (Test-Path -LiteralPath "C:\ProgramData") {
      $env:PROGRAMDATA = "C:\ProgramData"
    }
  }
}

function Test-PythonIsIsolated {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Exe,
    [string[]]$PythonArgs = @()
  )

  $probeResult = & $Exe @($PythonArgs + @(
    "-c",
    "import sys; print('1' if sys.prefix != sys.base_prefix else '0')"
  )) 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Python environment isolation: $Exe"
  }
  return @($probeResult)[-1] -eq "1"
}

function Resolve-ProjectPython {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [switch]$AllowPyLauncher,
    [switch]$PathPythonIsIsolatedInCi
  )

  $candidates = @()

  if ($env:VPS_SSH_LAUNCHER_PYTHON) {
    if (-not (Test-Path -LiteralPath $env:VPS_SSH_LAUNCHER_PYTHON -PathType Leaf)) {
      throw "VPS_SSH_LAUNCHER_PYTHON is set but the file does not exist: $env:VPS_SSH_LAUNCHER_PYTHON"
    }
    $envPython = (Resolve-Path -LiteralPath $env:VPS_SSH_LAUNCHER_PYTHON).Path
    $envPythonIsIsolated = Test-PythonIsIsolated -Exe $envPython
    $candidates += @{ Exe = $envPython; Args = @(); Source = "env"; IsIsolated = $envPythonIsIsolated }
  }

  $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    $candidates += @{ Exe = $venvPython; Args = @(); Source = "venv"; IsIsolated = $true }
  }

  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate.Exe) {
      return $candidate
    }
  }

  $pathPythonIsIsolated = $false
  if ($PathPythonIsIsolatedInCi) {
    $pathPythonIsIsolated = $env:CI -eq "true" -or $env:GITHUB_ACTIONS -eq "true"
  }

  if (Get-Command python -ErrorAction SilentlyContinue) {
    return @{ Exe = "python"; Args = @(); Source = "path:python"; IsIsolated = $pathPythonIsIsolated }
  }
  if ($AllowPyLauncher -and (Get-Command py -ErrorAction SilentlyContinue)) {
    return @{ Exe = "py"; Args = @("-3"); Source = "path:py"; IsIsolated = $pathPythonIsIsolated }
  }
  throw "Python not found. Install Python 3 or set VPS_SSH_LAUNCHER_PYTHON."
}

function Resolve-LauncherConfigPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [string]$Config
  )

  if ($Config) {
    return Resolve-LauncherExplicitPath -Path $Config
  }

  $configBase = if ($env:APPDATA) {
    Join-Path $env:APPDATA "vps-ssh-launcher"
  } elseif ($HOME) {
    Join-Path (Join-Path $HOME ".config") "vps-ssh-launcher"
  } else {
    $null
  }
  $repoConfig = Join-Path $ProjectRoot "target.json"

  if (-not $configBase) {
    return $repoConfig
  }

  $userConfig = Join-Path $configBase "target.json"

  if (Test-Path -LiteralPath $userConfig) {
    return $userConfig
  }
  if (Test-Path -LiteralPath $repoConfig) {
    return $repoConfig
  }
  return $userConfig
}

function Resolve-LauncherExplicitPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function New-LauncherTemplateConfig {
  return @'
{
  "profiles": {
    "example": {
      "host": "YOUR_VPS_IP",
      "port": 22,
      "user": "root",
      "password_env": "VPS_EXAMPLE_PASSWORD"
    }
  },
  "default": "example"
}
'@
}

function Invoke-LauncherPython {
  param(
    [Parameter(Mandatory = $true)]
    [hashtable]$Python,
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [AllowEmptyCollection()]
    [string[]]$LauncherArgs,
    # Invoke-LauncherPython normally runs the repository's ssh_tool.py. A test
    # can supply the same exact entrypoint path without changing $ProjectRoot,
    # which is also the current location used for relative launcher arguments.
    [string]$EntryPointPath = "",
    # The production default is ssh_tool.py in ProjectRoot. Tests can point at
    # a disposable entrypoint while exercising the same stream capture path.
    # Opt-in capture mode for wrappers that must parse remote output instead
    # of streaming it to the host: returns @{ ExitCode; Lines } and never
    # throws on a non-zero remote exit. The default streaming path is
    # byte-for-byte unchanged.
    [switch]$CaptureOutput
  )

  $pythonScript = if ([string]::IsNullOrWhiteSpace($EntryPointPath)) {
    Join-Path $ProjectRoot "ssh_tool.py"
  } else {
    $EntryPointPath
  }
  if (-not (Test-Path -LiteralPath $pythonScript)) {
    throw "ssh_tool.py not found at $pythonScript"
  }

  Push-Location $ProjectRoot
  try {
    $normalizedLauncherArgs = @(
      $LauncherArgs | ForEach-Object { $_ -replace "`r`n", "`n" }
    )
    if ($CaptureOutput) {
      # Pipeline capture merges the remote command's stderr into stdout. A
      # tool such as `docker inspect` can then return exit 0 while emitting a
      # fail-closed marker on stderr (``<no value>``), which a wrapper would
      # mistake for a field value. Keep stderr separate and bounded.
      $capture = [Diagnostics.ProcessStartInfo]::new()
      $capture.FileName = $Python.Exe
      $capture.UseShellExecute = $false
      $capture.EnvironmentVariables["DOTNET_SYSTEM_CONSOLE_ALLOW_ANSI_COLOR_REDIRECTION"] = "0"
      $capture.RedirectStandardOutput = $true
      $capture.RedirectStandardError = $true
      $capture.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
      $capture.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
      $capture.CreateNoWindow = $true
      foreach ($argument in @($Python.Args + @($pythonScript) + $normalizedLauncherArgs)) {
        [void]$capture.ArgumentList.Add([string]$argument)
      }
      $proc = [Diagnostics.Process]::Start($capture)
      $captureLimit = 8 * 1024 * 1024
      try {
        # These are .NET pipe pumps, not PowerShell script blocks. They must
        # drain independently on thread-pool workers: reading one pipe to EOF
        # first can block whenever the child fills the other pipe.
        $outStream = $proc.StandardOutput.BaseStream
        $errStream = $proc.StandardError.BaseStream
        # RuntimeMethodHandle avoids PowerShell's overload binder ambiguity
        # for MemoryStream(int) and MemoryStream(byte[]).
        $memoryCtor = [IO.MemoryStream].GetConstructor([Type[]]@())
        $outMemory = $memoryCtor.Invoke($null)
        $errMemory = $memoryCtor.Invoke($null)
        $copyMethod = [IO.Stream].GetMethod(
          "CopyToAsync", [Type[]]@([IO.Stream])
        )
        $drainOut = $copyMethod.Invoke($outStream, @($outMemory))
        $drainErr = $copyMethod.Invoke($errStream, @($errMemory))
        try {
          $proc.WaitForExit()
          $drainOut.GetAwaiter().GetResult() | Out-Null
          $drainErr.GetAwaiter().GetResult() | Out-Null
          $outBytes = [byte[]]$outMemory.ToArray()
          $errBytes = [byte[]]$errMemory.ToArray()
          if (($outBytes.Length + $errBytes.Length) -gt $captureLimit) {
            throw "Launcher output exceeded the 8 MiB capture limit."
          }
          $stdoutText = [Text.UTF8Encoding]::new($false).GetString($outBytes)
          $stderrText = [Text.UTF8Encoding]::new($false).GetString($errBytes)
          $exitCode = [int]$proc.ExitCode
        } catch {
          try { $proc.Kill($true) } catch { }
          throw
        } finally {
          $outMemory.Dispose()
          $errMemory.Dispose()
        }
      }
      finally {
        $proc.Dispose()
      }
      $stdoutLines = [string[]]@()
      $stderrLines = [string[]]@()
      $lineBreak = [char]10
      $lineTrim = [char[]]@([char]13, [char]10)
      if ($stdoutText) {
        $stdoutLines = [string[]](
          ($stdoutText.TrimEnd($lineTrim) -split [string]$lineBreak)
        ).ForEach({ $_.TrimEnd([char]13) })
      }
      if ($stderrText) {
        $stderrLines = [string[]](
          ($stderrText.TrimEnd($lineTrim) -split [string]$lineBreak)
        ).ForEach({ $_.TrimEnd([char]13) })
      }
      return @{
        ExitCode = $exitCode
        Lines = [string[]]$stdoutLines
        StandardErrorLines = [string[]]$stderrLines
      }
    }
    & $Python.Exe @($Python.Args + @($pythonScript) + $normalizedLauncherArgs) | Out-Host
    $exitCode = $LASTEXITCODE
    return $exitCode
  } finally {
    Pop-Location
  }
}

function Invoke-LauncherRemoteCommand {
  param(
    [Parameter(Mandatory = $true)]
    [hashtable]$Python,
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [string]$Config,
    [Parameter(Mandatory = $true)]
    [string]$Profile,
    [Parameter(Mandatory = $true)]
    [string]$Command,
    [int]$IdleTimeoutSeconds = 0,
    [int]$HardTimeoutSeconds = 0,
    [string]$RemoteTempPrefix = "vps-launcher",
    # Sensitive payloads (private keys, shared secrets) must not ride the
    # argv fast path: below the chunk threshold the base64 payload lands in
    # the remote bash process's /proc/*/cmdline. -ViaTempFile forces the
    # mode-600 temp-file transport regardless of payload size.
    [switch]$ViaTempFile,
    # Opt-in capture mode for read probes that must parse remote output:
    # returns @{ ExitCode; Lines } for the payload's final step instead of
    # streaming to the host and without throwing on its non-zero exit, so
    # wrappers keep full exit-code fidelity. Intermediate temp-file steps
    # still fail fast with the standard throw.
    [switch]$CaptureOutput
  )

  # gitattributes checks *.ps1 out as CRLF; real Linux bash rejects CR in the
  # payload (e.g. "func() {<CR>" is a syntax error), so commands must travel
  # LF-only. base64 keeps quoting intact; past the 12000-char mark a payload
  # no longer fits the Win32 CreateProcess argument budget as one token and
  # moves through a mode-600 remote temp file assembled in bounded chunks -
  # the transport scripts/cpa_bwg_guardrails.ps1 established.
  $normalizedCommand = $Command.Replace("`r`n", "`n").Replace("`r", "`n")
  $payload = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($normalizedCommand)
  )
  $commonArgs = @(
    "--config", $Config,
    "--profile", $Profile,
    "--strict-host-key-checking"
  )
  # --command-timeout/-hard-timeout belong to the `run` subparser, so they
  # travel after "run"; 0 keeps them unset and inherits the CLI defaults.
  $runArgs = @("run")
  if ($IdleTimeoutSeconds -gt 0) {
    $runArgs += @("--command-timeout", "$IdleTimeoutSeconds")
  }
  if ($HardTimeoutSeconds -gt 0) {
    $runArgs += @("--command-hard-timeout", "$HardTimeoutSeconds")
  }
  $invoke = {
    param([string]$RemoteCommand, [bool]$IsFinalStep = $false)
    $launcherArgs = $commonArgs + $runArgs + @("--command", $RemoteCommand)
    if ($CaptureOutput) {
      $result = Invoke-LauncherPython -Python $Python -ProjectRoot $ProjectRoot `
        -CaptureOutput -LauncherArgs $launcherArgs
      if (-not $IsFinalStep -and $result.ExitCode -ne 0) {
        throw "Remote command failed with exit code $($result.ExitCode)."
      }
      return $result
    }
    $exitCode = Invoke-LauncherPython -Python $Python -ProjectRoot $ProjectRoot -LauncherArgs $launcherArgs
    if ($exitCode -ne 0) {
      throw "Remote command failed with exit code $exitCode."
    }
  }

  $chunkSize = 12000
  if (-not $ViaTempFile -and $payload.Length -le $chunkSize) {
    if ($CaptureOutput) {
      return & $invoke (
        "set -o pipefail; printf %s $payload | base64 -d | bash"
      ) $true
    }
    & $invoke "printf %s $payload | base64 -d | bash"
    return
  }

  if ($RemoteTempPrefix -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$') {
    throw "RemoteTempPrefix must be 1-96 ASCII letters, digits, underscore or hyphen."
  }
  # Win32 CreateProcess caps one command line near 32K characters and the
  # launcher argument carries the whole framed transfer. The single-invocation
  # framing below is therefore reserved for payloads whose outer base64 stays
  # well inside that budget; anything larger -- and every -ViaTempFile
  # transfer, whose payload must not ride one argv -- stages a mode-600
  # remote temp file through separate bounded invocations (each chunk small
  # enough for one argv) and runs a small commit receiver at the end.
  $estimatedOuter = [int](($payload.Length + 900) * 4 / 3) + 80
  $framedFits = (-not $ViaTempFile) -and ($estimatedOuter -le 24000)
  if ($framedFits) {
    $chunkCount = [int][Math]::Ceiling($payload.Length / $chunkSize)
  $chunks = for ($offset = 0; $offset -lt $payload.Length; $offset += $chunkSize) {
    $length = [Math]::Min($chunkSize, $payload.Length - $offset)
    $payload.Substring($offset, $length)
  }
  $receiver = @(
    "set -eu"
    "umask 077"
    "b64_path=`$(mktemp /tmp/$RemoteTempPrefix-XXXXXXXXXX.b64)"
    "cleanup_owned() { rm -f -- `"`$b64_path`"; }"
    "trap 'cleanup_owned' EXIT HUP INT TERM"
    "chmod 600 `"`$b64_path`""
    "[ -f `"`$b64_path`" ] && [ ! -L `"`$b64_path`" ]"
    "[ `"`$(stat -c '%u:%a' -- `"`$b64_path`")`" = `"`$(id -u):600`" ]"
    "# chunks=$chunkCount"
    "set -o pipefail"
    "while IFS= read -r line; do"
    "  if [ `"`$line`" = REMOTE_COMMIT ]; then"
    "    test `"`$(wc -l < `"`$b64_path`")`" -eq $chunkCount"
    "    decoded=`$(mktemp /tmp/$RemoteTempPrefix-decoded-XXXXXXXXXX.sh)"
    "    cleanup_owned() { rm -f -- `"`$b64_path`" `"`$decoded`"; }"
    "    base64 -d -- `"`$b64_path`" > `"`$decoded`""
    "    bash `"`$decoded`""
    "    exit 0"
    "  fi"
    "  printf '%s\n' `"`$line`" >> `"`$b64_path`""
    "done"
  )
  try {
    # SSH command mode has no stdin. The base64 argument contains both the
    # bounded receiver and the payload, decoded by one bash process.
    $transferText = [string]::Join("`n", [string[]]($receiver + $chunks + @("REMOTE_COMMIT")))
    $transferPayload = [Convert]::ToBase64String(
      [Text.Encoding]::UTF8.GetBytes($transferText)
    )
    $framed = "printf %s $transferPayload | base64 -d | env -u BASH_ENV bash"
    if ($CaptureOutput) {
      return & $invoke $framed $true
    }
    & $invoke $framed
    }
    finally {
      # The remote EXIT trap owns cleanup for both success and failure.
    }
  }

  # Staged fallback: the same mode-600 temp-file transport the guardrail
  # drivers established. Every invocation stays inside the Win32 budget and
  # the byte-count check fails a partially staged file before decode.
  $remoteTemp = "/tmp/$RemoteTempPrefix-$([guid]::NewGuid().ToString('N')).b64"
  $tempCreated = $false
  try {
    if ($CaptureOutput) {
      $null = & $invoke ("umask 077; : > '$remoteTemp'; chmod 600 '$remoteTemp'")
    } else {
      & $invoke ("umask 077; : > '$remoteTemp'; chmod 600 '$remoteTemp'")
    }
    $tempCreated = $true
    for ($offset = 0; $offset -lt $payload.Length; $offset += $chunkSize) {
      $length = [Math]::Min($chunkSize, $payload.Length - $offset)
      $chunk = $payload.Substring($offset, $length)
      if ($CaptureOutput) {
        $null = & $invoke ("printf %s '$chunk' >> '$remoteTemp'")
      } else {
        & $invoke ("printf %s '$chunk' >> '$remoteTemp'")
      }
      # Pace the chunks so a burst of SSH channels cannot trip server-side
      # rate limits or crowd out interactive sessions mid-transfer.
      Start-Sleep -Milliseconds 100
    }
    # pipefail keeps a corrupted payload visible (decoder exit code) while a
    # failing remote script still propagates its own exit code to ssh_tool.
    $final = (
      "test \`"`$(wc -c < '$remoteTemp')\`" -eq $($payload.Length); " +
      "set -o pipefail; base64 -d -- '$remoteTemp' | bash; " +
      "rc=`$?; rm -f -- '$remoteTemp'; exit `$rc"
    )
    $tempCreated = $false
    if ($CaptureOutput) {
      return & $invoke $final $true
    }
    & $invoke $final
    return
  }
  finally {
    # A failed chunk step leaves the staged file behind; remove it before
    # surfacing the failure so /tmp cannot accumulate orphan payloads.
    if ($tempCreated) {
      try {
        $null = & $invoke ("rm -f -- '$remoteTemp'")
      } catch {
        Write-Warning "Failed to remove remote $RemoteTempPrefix temp file."
      }
    }
  }
}

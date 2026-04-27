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

  if ((-not $env:USERPROFILE) -and $HOME -and (Test-Path -LiteralPath $HOME)) {
    $env:USERPROFILE = $HOME
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

  if ((-not $env:PROGRAMDATA) -and (Test-Path -LiteralPath "C:\ProgramData")) {
    $env:PROGRAMDATA = "C:\ProgramData"
  }
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
    if (-not (Test-Path -LiteralPath $env:VPS_SSH_LAUNCHER_PYTHON)) {
      throw "VPS_SSH_LAUNCHER_PYTHON is set but the file does not exist: $env:VPS_SSH_LAUNCHER_PYTHON"
    }
    $candidates += @{ Exe = $env:VPS_SSH_LAUNCHER_PYTHON; Args = @(); Source = "env"; IsIsolated = $true }
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

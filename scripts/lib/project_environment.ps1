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

function Resolve-LauncherConfigPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [string]$Config
  )

  if ($Config) {
    return $Config
  }

  $configBase = if ($env:APPDATA) {
    Join-Path $env:APPDATA "vps-ssh-launcher"
  } else {
    Join-Path (Join-Path $HOME ".config") "vps-ssh-launcher"
  }
  $userConfig = Join-Path $configBase "target.json"
  $repoConfig = Join-Path $ProjectRoot "target.json"

  if (Test-Path -LiteralPath $userConfig) {
    return $userConfig
  }
  if (Test-Path -LiteralPath $repoConfig) {
    return $repoConfig
  }
  return $userConfig
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
    [string[]]$LauncherArgs
  )

  $pythonScript = Join-Path $ProjectRoot "ssh_tool.py"
  if (-not (Test-Path -LiteralPath $pythonScript)) {
    throw "ssh_tool.py not found at $pythonScript"
  }

  Push-Location $ProjectRoot
  try {
    $normalizedLauncherArgs = @(
      $LauncherArgs | ForEach-Object { $_ -replace "`r`n", "`n" }
    )
    & $Python.Exe @($Python.Args + @($pythonScript) + $normalizedLauncherArgs) | Out-Host
    $exitCode = $LASTEXITCODE
    return $exitCode
  } finally {
    Pop-Location
  }
}

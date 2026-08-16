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
    return $Config
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

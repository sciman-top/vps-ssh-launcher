param(
  [string]$Config,
  [string]$Profile,
  [string]$Command,
  [string]$Key,
  [switch]$AllowAgent,
  [switch]$StrictHostKeyChecking,
  [switch]$RunAll,
  [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

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

Initialize-WindowsProcessEnvironment

# --- Resolve Python runtime ---
function Resolve-ProjectPython {
  $candidates = @()

  if ($env:VPS_SSH_LAUNCHER_PYTHON) {
    $candidates += @{ Exe = $env:VPS_SSH_LAUNCHER_PYTHON; Args = @(); Source = "env" }
  }

  $venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    $candidates += @{ Exe = $venvPython; Args = @(); Source = "venv" }
  }

  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate.Exe) {
      return $candidate
    }
  }

  if (Get-Command python -ErrorAction SilentlyContinue) {
    return @{ Exe = "python"; Args = @(); Source = "path:python" }
  }
  if (Get-Command py -ErrorAction SilentlyContinue) {
    return @{ Exe = "py"; Args = @("-3"); Source = "path:py" }
  }
  throw "Python not found. Install Python 3 or set VPS_SSH_LAUNCHER_PYTHON."
}

# --- Ensure local config exists ---
if (-not $Config) {
  $configBase = if ($env:APPDATA) {
    Join-Path $env:APPDATA "vps-ssh-launcher"
  } else {
    Join-Path $HOME ".config\vps-ssh-launcher"
  }
  $userConfig = Join-Path $configBase "target.json"

  # Prefer user-local config, then repo-local fallback (matches Python resolve_default_config_path)
  if (Test-Path -LiteralPath $userConfig) {
    $Config = $userConfig
  } elseif (Test-Path -LiteralPath (Join-Path $scriptDir "target.json")) {
    $Config = Join-Path $scriptDir "target.json"
  } else {
    $Config = $userConfig
  }
}

if (-not (Test-Path -LiteralPath $Config)) {
  $configDir = Split-Path -Parent $Config
  if ($configDir -and -not (Test-Path -LiteralPath $configDir)) {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
  }
  $template = @'
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
  Set-Content -LiteralPath $Config -Value $template -Encoding UTF8
  Write-Host "Created template config at $Config"
  Write-Host "Edit the file with your VPS details, then run connect.cmd again."
  exit 0
}

$py = Resolve-ProjectPython

# --- Ensure paramiko is installed ---
$probe = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('paramiko') else 1)"
& $py.Exe @($py.Args + @("-c", $probe))
if ($LASTEXITCODE -ne 0) {
  Write-Host "Installing dependencies..."
  & $py.Exe @($py.Args + @("-m", "pip", "install", "-r", (Join-Path $scriptDir "requirements.txt")))
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Python dependencies."
  }
}

# --- Build ssh_tool.py arguments ---
$pythonScript = Join-Path $scriptDir "ssh_tool.py"
if (-not (Test-Path -LiteralPath $pythonScript)) {
  throw "ssh_tool.py not found at $pythonScript"
}

$pyArgs = @($pythonScript, "--config", $Config)
if ($Profile)                { $pyArgs += @("--profile", $Profile) }
if ($Key)                    { $pyArgs += @("--key", $Key) }
if ($Verbose)                { $pyArgs += "--verbose" }
if ($StrictHostKeyChecking)  { $pyArgs += "--strict-host-key-checking" }
if ($AllowAgent)             { $pyArgs += "--allow-agent" }

# Default to "check" when no command is provided
if ($PSBoundParameters.ContainsKey("Command")) {
  $pyArgs += @("run", "--command", $Command)
  if ($RunAll) { $pyArgs += "--all" }
} else {
  $pyArgs += "check"
}

# --- Execute ---
& $py.Exe @($py.Args + $pyArgs)
exit $LASTEXITCODE

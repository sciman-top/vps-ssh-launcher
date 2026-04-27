param(
  [string]$Config,
  [string]$Profile,
  [string]$Command,
  [ValidateRange(0, 86400)]
  [int]$CommandTimeout = 60,
  [string]$Key,
  [switch]$AllowAgent,
  [switch]$StrictHostKeyChecking,
  [switch]$RunAll,
  [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "scripts\lib\project_environment.ps1")

Initialize-WindowsProcessEnvironment

# --- Ensure local config exists ---
if (-not $Config) {
  $configBase = if ($env:APPDATA) {
    Join-Path $env:APPDATA "vps-ssh-launcher"
  } else {
    Join-Path (Join-Path $HOME ".config") "vps-ssh-launcher"
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

$py = Resolve-ProjectPython -ProjectRoot $scriptDir -AllowPyLauncher

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
  $pyArgs += @("run", "--command", $Command, "--command-timeout", "$CommandTimeout")
  if ($RunAll) { $pyArgs += "--all" }
} else {
  $pyArgs += "check"
}

# --- Execute ---
& $py.Exe @($py.Args + $pyArgs)
exit $LASTEXITCODE

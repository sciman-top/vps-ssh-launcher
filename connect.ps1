param(
  [string]$Config,
  [string]$Profile,
  [string]$Command,
  [ValidateRange(0, 86400)]
  [int]$CommandTimeout = 60,
  [ValidateRange(0, 86400)]
  [int]$CommandHardTimeout = 0,
  [string]$Key,
  [switch]$AllowAgent,
  [switch]$AllowGlobalBootstrap,
  [switch]$StrictHostKeyChecking,
  [switch]$RunAll,
  [ValidateRange(1, 128)]
  [int]$MaxWorkers,
  [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "scripts\lib\project_environment.ps1")

Initialize-WindowsProcessEnvironment

$Config = Resolve-LauncherConfigPath -ProjectRoot $scriptDir -Config $Config

if (-not (Test-Path -LiteralPath $Config)) {
  $configDir = Split-Path -Parent $Config
  if ($configDir -and -not (Test-Path -LiteralPath $configDir)) {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
  }
  $template = New-LauncherTemplateConfig
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
  if (-not $py.IsIsolated -and -not $AllowGlobalBootstrap) {
    throw "Refusing to install dependencies into non-isolated Python. Create .venv, set VPS_SSH_LAUNCHER_PYTHON, or pass -AllowGlobalBootstrap to accept global installation risk."
  }
  Write-Host "Installing dependencies..."
  & $py.Exe @($py.Args + @("-m", "pip", "install", "-r", (Join-Path $scriptDir "requirements.txt")))
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Python dependencies."
  }
}

$pyArgs = @("--config", $Config)
if ($Profile)                { $pyArgs += @("--profile", $Profile) }
if ($Key)                    { $pyArgs += @("--key", $Key) }
if ($Verbose)                { $pyArgs += "--verbose" }
if ($StrictHostKeyChecking)  { $pyArgs += "--strict-host-key-checking" }
if ($AllowAgent)             { $pyArgs += "--allow-agent" }

# Default to "check" when no command is provided
if ($PSBoundParameters.ContainsKey("Command")) {
  $pyArgs += @(
    "run",
    "--command", $Command,
    "--command-timeout", "$CommandTimeout",
    "--command-hard-timeout", "$CommandHardTimeout"
  )
  if ($RunAll) { $pyArgs += "--all" }
  if ($PSBoundParameters.ContainsKey("MaxWorkers")) {
    $pyArgs += @("--max-workers", "$MaxWorkers")
  }
} else {
  $pyArgs += "check"
}

# --- Execute ---
$exitCode = Invoke-LauncherPython -Python $py -ProjectRoot $scriptDir -LauncherArgs $pyArgs
exit $exitCode

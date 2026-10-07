param(
  [string]$Config,
  [string]$Profile,
  [string]$Command,
  [string]$CommandFile,
  [ValidateRange(0, 86400)]
  [int]$CommandTimeout = 60,
  [ValidateRange(0, 86400)]
  [int]$CommandHardTimeout = 0,
  [string]$Key,
  [switch]$AllowAgent,
  [switch]$AllowGlobalBootstrap,
  [switch]$StrictHostKeyChecking,
  [switch]$AllowUnknownHostKey,
  [switch]$RunAll,
  [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "scripts\lib\project_environment.ps1")

Initialize-WindowsProcessEnvironment

$hasCommand = $PSBoundParameters.ContainsKey("Command")
$hasCommandFile = $PSBoundParameters.ContainsKey("CommandFile")
if ($hasCommand -and $hasCommandFile) {
  throw "Command and CommandFile are mutually exclusive."
}

$commandToRun = $null
if ($hasCommandFile) {
  $CommandFile = Resolve-LauncherExplicitPath -Path $CommandFile
  if (-not (Test-Path -LiteralPath $CommandFile -PathType Leaf)) {
    throw "Command file not found: $CommandFile"
  }
  $commandToRun = [System.IO.File]::ReadAllText($CommandFile)
} elseif ($hasCommand) {
  $commandToRun = $Command
}

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

# --- Ensure the supported Paramiko major version is installed ---
# The probe costs a full Python process start on every launcher invocation, so
# a passing result is cached next to the config file. The cache is keyed on
# interpreter identity (path, mtime, size, launcher args) plus the repo
# requirements.txt stamp, and the recorded paramiko distribution directory
# must still exist. Any doubt re-runs the probe; the cache never bypasses the
# install or upgrade path.
$probe = @'
import importlib.metadata
import sys
import sysconfig

try:
    version = importlib.metadata.version("paramiko")
    major = int(version.split(".", 1)[0])
except (importlib.metadata.PackageNotFoundError, TypeError, ValueError):
    sys.exit(1)

if major == 5:
    print(sysconfig.get_paths()["purelib"])
    print(version)
sys.exit(0 if major == 5 else 1)
'@

function Get-FileStamp {
  param([string]$Path)

  $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
  if (-not $item) {
    return ""
  }
  return "$($item.LastWriteTimeUtc.Ticks)/$($item.Length)"
}

function Resolve-PythonExecutableIdentity {
  param([hashtable]$Python)

  if ([IO.Path]::IsPathRooted($Python.Exe)) {
    return $Python.Exe
  }
  $resolved = Get-Command $Python.Exe -ErrorAction SilentlyContinue
  if ($resolved -and $resolved.Source) {
    return $resolved.Source
  }
  return $Python.Exe
}

function Test-ParamikoProbeCache {
  param(
    [string]$CachePath,
    [hashtable]$Python,
    [string]$PythonIdentity,
    [string]$RequirementsStamp
  )

  if (-not (Test-Path -LiteralPath $CachePath -PathType Leaf)) {
    return $false
  }
  $marker = @{}
  foreach ($line in (Get-Content -LiteralPath $CachePath -ErrorAction SilentlyContinue)) {
    $separator = $line.IndexOf("=")
    if ($separator -gt 0) {
      $marker[$line.Substring(0, $separator)] = $line.Substring($separator + 1)
    }
  }
  foreach ($key in @("exe", "exe_stamp", "py_args", "site_packages", "version", "requirements_stamp")) {
    if (-not $marker.ContainsKey($key)) {
      return $false
    }
  }
  if ($marker.exe -ne $PythonIdentity -or $marker.py_args -ne ($Python.Args -join " ")) {
    return $false
  }
  if ($marker.requirements_stamp -ne $RequirementsStamp) {
    return $false
  }
  if ((Get-FileStamp -Path $PythonIdentity) -ne $marker.exe_stamp) {
    return $false
  }
  $sitePackages = $marker.site_packages
  $distInfo = Join-Path $sitePackages "paramiko-$($marker.version).dist-info"
  $eggInfo = Join-Path $sitePackages "paramiko-$($marker.version).egg-info"
  return ((Test-Path -LiteralPath $distInfo) -or (Test-Path -LiteralPath $eggInfo))
}

$pythonIdentity = Resolve-PythonExecutableIdentity -Python $py
$probeCachePath = Join-Path (Split-Path -Parent $Config) "paramiko-probe.cache"
$requirementsStamp = Get-FileStamp -Path (Join-Path $scriptDir "requirements.txt")
$probeCached = Test-ParamikoProbeCache -CachePath $probeCachePath -Python $py -PythonIdentity $pythonIdentity -RequirementsStamp $requirementsStamp
if (-not $probeCached) {
  $probeOutput = & $py.Exe @($py.Args + @("-c", $probe))
  if ($LASTEXITCODE -ne 0) {
    if (-not $py.IsIsolated -and -not $AllowGlobalBootstrap) {
      throw "Paramiko >=5,<6 is required. Refusing to install or upgrade dependencies in non-isolated Python. Create .venv, set VPS_SSH_LAUNCHER_PYTHON, or pass -AllowGlobalBootstrap to accept global installation risk."
    }
    Write-Host "Installing compatible dependencies..."
    & $py.Exe @($py.Args + @("-m", "pip", "install", "-r", (Join-Path $scriptDir "requirements.txt")))
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to install Python dependencies."
    }
    # A successful install is still verified before it may be cached; a
    # broken install must surface on this run, not on the next cache miss.
    $probeOutput = & $py.Exe @($py.Args + @("-c", $probe))
    if ($LASTEXITCODE -ne 0) {
      throw "Paramiko probe still failing after dependency install."
    }
  }
  $probeLines = @($probeOutput | Where-Object { $_ })
  $executableStamp = Get-FileStamp -Path $pythonIdentity
  if ($probeLines.Count -ge 2 -and $executableStamp) {
    $marker = @(
      "exe=$pythonIdentity",
      "exe_stamp=$executableStamp",
      "py_args=$($py.Args -join ' ')",
      "site_packages=$($probeLines[-2])",
      "version=$($probeLines[-1])",
      "requirements_stamp=$requirementsStamp"
    )
    try {
      Set-Content -LiteralPath $probeCachePath -Value $marker -Encoding UTF8
    } catch {
      # A read-only config directory must not break the launcher; the next
      # run simply pays the probe process again.
    }
  }
}

$pyArgs = @("--config", $Config)
if ($Key) {
  $Key = Resolve-LauncherExplicitPath -Path $Key
}
if ($Profile)                { $pyArgs += @("--profile", $Profile) }
if ($Key)                    { $pyArgs += @("--key", $Key) }
if ($Verbose)                { $pyArgs += "--verbose" }
if ($StrictHostKeyChecking -and $AllowUnknownHostKey) {
  throw "StrictHostKeyChecking and AllowUnknownHostKey are mutually exclusive."
}
if ($AllowUnknownHostKey) {
  $pyArgs += "--allow-unknown-host-key"
} else {
  $pyArgs += "--strict-host-key-checking"
}
if ($AllowAgent)             { $pyArgs += "--allow-agent" }

# Default to "check" when no command is provided
if ($hasCommand -or $hasCommandFile) {
  $pyArgs += @(
    "run",
    "--command", $commandToRun,
    "--command-timeout", "$CommandTimeout",
    "--command-hard-timeout", "$CommandHardTimeout"
  )
  if ($RunAll) { $pyArgs += "--all" }
} else {
  $pyArgs += "check"
}

# --- Execute ---
$exitCode = Invoke-LauncherPython -Python $py -ProjectRoot $scriptDir -LauncherArgs $pyArgs
exit $exitCode

param(
  [switch]$SkipDependencyAudit,
  [switch]$RunIntegration,
  [switch]$AllowGlobalPython,
  [string]$IntegrationConfig,
  [string]$IntegrationProfile,
  [string]$IntegrationCommand,
  [string]$IntegrationExpected
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")
Push-Location $repoRoot

try {
  function Assert-IsolatedPythonForEnvironmentGate {
    param(
      [hashtable]$Python,
      [string]$GateId
    )

    if ($Python.IsIsolated -or $AllowGlobalPython) {
      return
    }

    $bootstrap = 'python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".[dev]"'
    throw (
      "Gate '$GateId' needs an isolated project Python environment because it " +
      "checks the whole interpreter environment. Create one with '$bootstrap', " +
      "set VPS_SSH_LAUNCHER_PYTHON, or pass -AllowGlobalPython only when the " +
      "global interpreter is known to be dedicated to this repo. Current source: " +
      "$($Python.Source)."
    )
  }

  function Assert-PythonAsyncioAvailable {
    param([string]$PythonExe)

    & $PythonExe -c "import asyncio; print('asyncio ok')" | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw (
        "Python asyncio is unavailable in this Windows environment. This blocks " +
        "pip-audit because its cache dependency imports asyncio. Run an elevated " +
        "PowerShell and execute 'netsh winsock reset', then reboot and rerun gates."
      )
    }
  }

  function Assert-NodeCryptoAvailable {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
      throw "Node.js is required for pyright but was not found on PATH."
    }

    & node -e "console.log('node ok')" | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw (
        "Node.js cannot initialize crypto/CSPRNG in this Windows environment. " +
        "This blocks pyright. Run an elevated PowerShell and execute " +
        "'netsh winsock reset', then reboot and rerun gates."
      )
    }
  }

  function Assert-IntegrationProfileIsNonInteractive {
    param(
      [string]$ConfigPath,
      [string]$ProfileName
    )

    if ($ProfileName) {
      return
    }
    if (-not $ConfigPath) {
      return
    }
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
      return
    }

    $configText = Get-Content -LiteralPath $ConfigPath -Raw
    $configJson = $configText | ConvertFrom-Json
    if (-not $configJson.profiles) {
      return
    }
    if ($configJson.default) {
      return
    }

    $profileCount = @($configJson.profiles.PSObject.Properties).Count
    if ($profileCount -gt 1) {
      throw (
        "RunIntegration is non-interactive. Pass -IntegrationProfile when " +
        "the integration config contains multiple profiles and no default."
      )
    }
  }

  function Resolve-IntegrationConfigPath {
    param(
      [string]$ConfigPath,
      [string]$RepoRoot
    )

    if ($ConfigPath) {
      return $ConfigPath
    }

    if ($env:APPDATA) {
      $userConfig = Join-Path $env:APPDATA "vps-ssh-launcher\target.json"
      if (Test-Path -LiteralPath $userConfig) {
        return $userConfig
      }
    }

    $repoConfig = Join-Path $RepoRoot "target.json"
    if (Test-Path -LiteralPath $repoConfig) {
      return $repoConfig
    }

    return ""
  }

  Initialize-WindowsProcessEnvironment

  $python = Resolve-ProjectPython -ProjectRoot $repoRoot -PathPythonIsIsolatedInCi
  $pythonExe = $python.Exe
  $pythonGateFiles = @(
    "ssh_tool.py",
    "auto_install.py",
    "test_ssh_tool.py",
    "test_auto_install.py",
    "test_scripts.py",
    "test_integration_real_ssh.py"
  )
  $powershellPolicyVerifier = ".governed-ai\verify-powershell-policy.py"
  $commands = @(
    @{ Id = "build"; Command = @($pythonExe, "-m", "compileall", "-q") + $pythonGateFiles },
    @{ Id = "test"; Command = @($pythonExe, "-m", "pytest", "-q") },
    @{ Id = "contract"; Command = @($pythonExe, "-m", "unittest", "-q") },
    @{ Id = "contract:powershell-policy"; Command = @($pythonExe, $powershellPolicyVerifier) },
    @{ Id = "invariant:pip-check"; RequiresIsolatedPython = $true; Command = @($pythonExe, "-m", "pip", "check") },
    @{ Id = "invariant:dependency-audit"; RequiresIsolatedPython = $true; RequiresPythonAsyncio = $true; Command = @($pythonExe, "-m", "pip_audit", "-r", "requirements.txt") },
    @{ Id = "hotspot:bandit"; Command = @($pythonExe, "-m", "bandit", "-q", "-r", "ssh_tool.py", "auto_install.py") },
    @{ Id = "hotspot:vulture"; Command = @($pythonExe, "-m", "vulture") + $pythonGateFiles + @("--min-confidence", "80") },
    @{ Id = "lint:ruff"; Command = @($pythonExe, "-m", "ruff", "check") + $pythonGateFiles },
    @{ Id = "lint:format"; Command = @($pythonExe, "-m", "ruff", "format", "--check") + $pythonGateFiles },
    @{ Id = "type:mypy"; Command = @($pythonExe, "-m", "mypy") + $pythonGateFiles },
    @{ Id = "type:pyright"; RequiresNodeCrypto = $true; Command = @($pythonExe, "-m", "pyright") + $pythonGateFiles }
  )

  if (-not $SkipDependencyAudit) {
    $commandsToRun = $commands
  } else {
    $commandsToRun = $commands | Where-Object { $_.Id -ne "invariant:dependency-audit" }
  }

  if ($RunIntegration) {
    $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
    $effectiveIntegrationConfig = Resolve-IntegrationConfigPath `
      -ConfigPath $IntegrationConfig `
      -RepoRoot $repoRoot
    Assert-IntegrationProfileIsNonInteractive `
      -ConfigPath $effectiveIntegrationConfig `
      -ProfileName $IntegrationProfile
    if ($effectiveIntegrationConfig) {
      $env:VPS_SSH_LAUNCHER_INTEGRATION_CONFIG = $effectiveIntegrationConfig
    }
    if ($IntegrationProfile) {
      $env:VPS_SSH_LAUNCHER_INTEGRATION_PROFILE = $IntegrationProfile
    }
    if ($IntegrationCommand) {
      $env:VPS_SSH_LAUNCHER_INTEGRATION_COMMAND = $IntegrationCommand
    }
    if ($IntegrationExpected) {
      $env:VPS_SSH_LAUNCHER_INTEGRATION_EXPECTED = $IntegrationExpected
    }
  }

  foreach ($entry in $commandsToRun) {
    if ($entry.RequiresIsolatedPython) {
      Assert-IsolatedPythonForEnvironmentGate -Python $python -GateId $entry.Id
    }
    if ($entry.RequiresPythonAsyncio) {
      Assert-PythonAsyncioAvailable -PythonExe $pythonExe
    }
    if ($entry.RequiresNodeCrypto) {
      Assert-NodeCryptoAvailable
    }
    Write-Host "==> $($entry.Id): $($entry.Command -join ' ')"
    & $entry.Command[0] @($entry.Command[1..($entry.Command.Count - 1)])
    if ($LASTEXITCODE -ne 0) {
      throw "Gate failed: $($entry.Id)"
    }
  }
} finally {
  Pop-Location
}

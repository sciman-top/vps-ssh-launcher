param(
  [switch]$SkipDependencyAudit,
  [switch]$RunIntegration,
  [string]$IntegrationConfig,
  [string]$IntegrationProfile,
  [string]$IntegrationCommand,
  [string]$IntegrationExpected
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $repoRoot

try {
  function Resolve-ProjectPython {
    if ($env:VPS_SSH_LAUNCHER_PYTHON -and (Test-Path -LiteralPath $env:VPS_SSH_LAUNCHER_PYTHON)) {
      return $env:VPS_SSH_LAUNCHER_PYTHON
    }

    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
      return $venvPython
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
      return "python"
    }

    throw "Python not found. Install Python 3 or set VPS_SSH_LAUNCHER_PYTHON."
  }

  $pythonExe = Resolve-ProjectPython
  $commands = @(
    @{ Id = "build"; Command = @($pythonExe, "-m", "compileall", "-q", "ssh_tool.py", "auto_install.py", "test_ssh_tool.py", "test_auto_install.py", "test_scripts.py", "test_integration_real_ssh.py") },
    @{ Id = "test"; Command = @($pythonExe, "-m", "pytest", "-q") },
    @{ Id = "contract"; Command = @($pythonExe, "-m", "unittest", "-q") },
    @{ Id = "invariant:pip-check"; Command = @($pythonExe, "-m", "pip", "check") },
    @{ Id = "invariant:dependency-audit"; Command = @($pythonExe, "-m", "pip_audit", "-r", "requirements.txt") },
    @{ Id = "hotspot:bandit"; Command = @($pythonExe, "-m", "bandit", "-q", "-r", "ssh_tool.py", "auto_install.py") },
    @{ Id = "hotspot:vulture"; Command = @($pythonExe, "-m", "vulture", "ssh_tool.py", "auto_install.py", "test_ssh_tool.py", "test_auto_install.py", "test_scripts.py", "test_integration_real_ssh.py", "--min-confidence", "80") },
    @{ Id = "lint:ruff"; Command = @($pythonExe, "-m", "ruff", "check", ".") },
    @{ Id = "lint:format"; Command = @($pythonExe, "-m", "ruff", "format", "--check", ".") },
    @{ Id = "type:mypy"; Command = @($pythonExe, "-m", "mypy", "ssh_tool.py", "auto_install.py", "test_ssh_tool.py", "test_auto_install.py", "test_scripts.py", "test_integration_real_ssh.py") },
    @{ Id = "type:pyright"; Command = @($pythonExe, "-m", "pyright", "ssh_tool.py", "auto_install.py", "test_ssh_tool.py", "test_auto_install.py", "test_scripts.py", "test_integration_real_ssh.py") }
  )

  if (-not $SkipDependencyAudit) {
    $commandsToRun = $commands
  } else {
    $commandsToRun = $commands | Where-Object { $_.Id -ne "invariant:dependency-audit" }
  }

  if ($RunIntegration) {
    $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
    if ($IntegrationConfig) {
      $env:VPS_SSH_LAUNCHER_INTEGRATION_CONFIG = $IntegrationConfig
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
    Write-Host "==> $($entry.Id): $($entry.Command -join ' ')"
    & $entry.Command[0] @($entry.Command[1..($entry.Command.Count - 1)])
    if ($LASTEXITCODE -ne 0) {
      throw "Gate failed: $($entry.Id)"
    }
  }
} finally {
  Pop-Location
}

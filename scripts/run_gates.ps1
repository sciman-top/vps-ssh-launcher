param(
  [switch]$SkipDependencyAudit,
  [switch]$RunIntegration
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $repoRoot

try {
  $commands = @(
    @{ Id = "build"; Command = @("python", "-m", "compileall", "-q", "ssh_tool.py", "auto_install.py", "test_ssh_tool.py", "test_scripts.py", "test_integration_real_ssh.py") },
    @{ Id = "test"; Command = @("python", "-m", "pytest", "-q") },
    @{ Id = "contract"; Command = @("python", "-m", "unittest", "-q") },
    @{ Id = "invariant:pip-check"; Command = @("python", "-m", "pip", "check") },
    @{ Id = "invariant:dependency-audit"; Command = @("python", "-m", "pip_audit", "-r", "requirements.txt") },
    @{ Id = "hotspot:bandit"; Command = @("bandit", "-q", "-r", "ssh_tool.py", "auto_install.py") },
    @{ Id = "hotspot:vulture"; Command = @("vulture", "ssh_tool.py", "auto_install.py", "test_ssh_tool.py", "test_scripts.py", "test_integration_real_ssh.py", "--min-confidence", "80") },
    @{ Id = "lint:ruff"; Command = @("ruff", "check", ".") },
    @{ Id = "lint:format"; Command = @("ruff", "format", "--check", ".") },
    @{ Id = "type:mypy"; Command = @("python", "-m", "mypy", "ssh_tool.py", "auto_install.py", "test_ssh_tool.py", "test_scripts.py", "test_integration_real_ssh.py") },
    @{ Id = "type:pyright"; Command = @("pyright", "ssh_tool.py", "auto_install.py", "test_ssh_tool.py", "test_scripts.py", "test_integration_real_ssh.py") }
  )

  if (-not $SkipDependencyAudit) {
    $commandsToRun = $commands
  } else {
    $commandsToRun = $commands | Where-Object { $_.Id -ne "invariant:dependency-audit" }
  }

  if ($RunIntegration) {
    $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
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

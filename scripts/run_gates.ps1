#requires -Version 7
param(
  [ValidateSet("Focused", "Full", "Integration")]
  [string]$Profile = "Full",
  [string[]]$FocusPath = @(),
  [switch]$RunDependencyAudit,
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

  function Resolve-FocusedPythonTargets {
    param(
      [string[]]$Paths
    )

    $targets = @()
    foreach ($rawPath in @($Paths | Where-Object { $_ })) {
      foreach ($path in ($rawPath -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        $candidate = $path -replace "/", "\"
        if (-not (Test-Path -LiteralPath $candidate)) {
          throw "Focused gate target does not exist: $path"
        }

        $item = Get-Item -LiteralPath $candidate
        if (-not $item.PSIsContainer -and $item.Extension -ne ".py") {
          throw "Focused gate target must be a Python file or directory: $path"
        }
        $targets += $candidate
      }
    }

    if ($targets.Count -eq 0) {
      throw "Focused profile requires at least one -FocusPath Python file or directory."
    }
    return $targets
  }

  Initialize-WindowsProcessEnvironment

  if ($Profile -eq "Integration" -and -not $RunIntegration) {
    throw "Integration profile requires -RunIntegration."
  }
  if ($Profile -eq "Focused" -and ($RunIntegration -or $RunDependencyAudit)) {
    throw "Focused profile cannot be combined with -RunIntegration or -RunDependencyAudit."
  }
  if ($Profile -eq "Integration" -and $RunDependencyAudit) {
    throw "Integration profile cannot be combined with -RunDependencyAudit."
  }
  if ($Profile -ne "Focused" -and @($FocusPath | Where-Object { $_ }).Count -gt 0) {
    throw "-FocusPath is only valid with -Profile Focused."
  }

  $python = Resolve-ProjectPython -ProjectRoot $repoRoot -PathPythonIsIsolatedInCi
  $pythonExe = $python.Exe
  $sourceTargets = @(
    "ssh_tool.py",
    "auto_install.py",
    "vps_ssh_launcher"
  )
  $testFiles = @(
    "test_ssh_tool.py",
    "test_auto_install.py",
    "test_cpa_admission.py",
    "test_scripts.py",
    "test_integration_real_ssh.py",
    "test_maintenance.py"
  )
  $pythonTargets = $sourceTargets + $testFiles
  if ($Profile -eq "Focused") {
    $focusedTargets = @(Resolve-FocusedPythonTargets -Paths $FocusPath)
    $focusedTestTargets = @(
      $focusedTargets | Where-Object {
        $leaf = Split-Path -Leaf $_
        $leaf -like "test_*.py" -or $leaf -like "*_test.py"
      }
    )
    if ($focusedTestTargets.Count -eq 0) {
      throw "Focused profile requires at least one test_*.py or *_test.py -FocusPath."
    }

    $commands = @(
      @{ Id = "focused:test"; Command = @($pythonExe, "-m", "pytest", "-q") + $focusedTestTargets },
      @{ Id = "focused:ruff"; Command = @($pythonExe, "-m", "ruff", "check") + $focusedTargets },
      @{ Id = "focused:format"; Command = @($pythonExe, "-m", "ruff", "format", "--check") + $focusedTargets },
      @{ Id = "focused:mypy"; Command = @($pythonExe, "-m", "mypy") + $focusedTargets }
    )
  } elseif ($Profile -eq "Integration") {
    $commands = @(
      @{ Id = "integration:test"; Command = @($pythonExe, "-m", "pytest", "-q", "test_integration_real_ssh.py") }
    )
  } else {
    $commands = @(
      @{ Id = "build"; Command = @($pythonExe, "-m", "compileall", "-q") + $pythonTargets },
      @{ Id = "test"; Command = @($pythonExe, "-m", "pytest", "-q") }
    )

    if ($RunDependencyAudit) {
      Assert-IsolatedPythonForEnvironmentGate -Python $python -GateId "dependency-audit"
      $commands += @(
        @{ Id = "invariant:pip-check"; Command = @($pythonExe, "-m", "pip", "check") },
        @{ Id = "invariant:dependency-audit"; Command = @($pythonExe, "-m", "pip_audit", "-r", "requirements.txt") }
      )
    }

    $commands += @(
      @{ Id = "hotspot:bandit"; Command = @($pythonExe, "-m", "bandit", "-q", "-r") + $sourceTargets },
      @{ Id = "lint:ruff"; Command = @($pythonExe, "-m", "ruff", "check") + $pythonTargets },
      @{ Id = "lint:format"; Command = @($pythonExe, "-m", "ruff", "format", "--check") + $pythonTargets },
      @{ Id = "type:mypy"; Command = @($pythonExe, "-m", "mypy") + $pythonTargets }
    )
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

  foreach ($entry in $commands) {
    Write-Host "==> $($entry.Id): $($entry.Command -join ' ')"
    & $entry.Command[0] @($entry.Command[1..($entry.Command.Count - 1)])
    if ($LASTEXITCODE -ne 0) {
      throw "Gate failed: $($entry.Id)"
    }
  }
} finally {
  Pop-Location
}

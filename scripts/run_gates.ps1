#requires -Version 7
param(
  [ValidateSet("Focused", "Full", "Integration")]
  [string]$Profile = "Full",
  [ValidateSet("All", "Runtime", "Static", "Format")]
  [string]$FullScope = "All",
  [string[]]$FocusPath = @(),
  [switch]$RunDependencyAudit,
  [switch]$RunIntegration,
  [switch]$AllowGlobalPython,
  [string]$IntegrationConfig,
  [string]$IntegrationProfile
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")
$integrationEnvironment = @{}
foreach ($name in @(
  "VPS_SSH_LAUNCHER_RUN_INTEGRATION",
  "VPS_SSH_LAUNCHER_INTEGRATION_CONFIG",
  "VPS_SSH_LAUNCHER_INTEGRATION_PROFILE"
)) {
  $integrationEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
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
      [string[]]$Paths,
      [string]$Description
    )

    $targets = @()
    foreach ($rawPath in @($Paths | Where-Object { $_ })) {
      foreach ($path in ($rawPath -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        $candidate = $path -replace "/", "\"
        if (-not (Test-Path -LiteralPath $candidate)) {
          throw "$Description target does not exist: $path"
        }

        $item = Get-Item -LiteralPath $candidate
        if (-not $item.PSIsContainer -and $item.Extension -ne ".py") {
          throw "$Description target must be a Python file or directory: $path"
        }
        $targets += $candidate
      }
    }
    return $targets
  }

  function Resolve-IntegrationProfile {
    param([string]$ConfigPath, [string]$ProfileName)

    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $profiles = $config.profiles
    if (-not $profiles) {
      return ""
    }
    $available = @($profiles.PSObject.Properties.Name)
    if ($ProfileName) {
      if ($available -notcontains $ProfileName) {
        throw "Integration profile '$ProfileName' is not present in $ConfigPath."
      }
      return $ProfileName
    }
    if ($available.Count -eq 1) {
      return $available[0]
    }
    $defaultName = [string]$config.default
    if ($defaultName) {
      if ($available -notcontains $defaultName) {
        throw "Integration default '$defaultName' is not present in $ConfigPath."
      }
      return $defaultName
    }
    throw (
      "RunIntegration requires a single-profile config or " +
      "-IntegrationProfile when multiple profiles have no default."
    )
  }

  Initialize-WindowsProcessEnvironment

  if ($Profile -eq "Integration" -and -not $RunIntegration) {
    throw "Integration profile requires -RunIntegration."
  }
  if ($Profile -eq "Focused" -and $RunIntegration) {
    throw "Focused profile cannot be combined with -RunIntegration."
  }
  if ($Profile -eq "Integration" -and $RunDependencyAudit) {
    throw "Integration profile cannot be combined with -RunDependencyAudit."
  }
  if ($Profile -ne "Focused" -and @($FocusPath | Where-Object { $_ }).Count -gt 0) {
    throw "-FocusPath is only valid with -Profile Focused."
  }
  if ($Profile -notin @("Focused", "Full") -and $FullScope -ne "All") {
    throw "-FullScope is only valid with -Profile Full."
  }
  if ($Profile -eq "Focused" -and $FullScope -ne "All") {
    throw "-FullScope is only valid with -Profile Full."
  }

  $python = Resolve-ProjectPython -ProjectRoot $repoRoot -PathPythonIsIsolatedInCi
  $pythonExe = $python.Exe
  $sourceTargets = @(
    "ssh_tool.py",
    "auto_install.py",
    "vps_ssh_launcher"
  )
  $testFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $repoRoot "tests") -Filter "test_*.py" -File |
      Sort-Object Name | ForEach-Object { "tests/" + $_.Name }
  )
  # Support modules imported by the test files: compiled, linted and
  # type-checked with them but never collected or run by pytest. Derived
  # from the tests/ tree so a new support module cannot skip the gates.
  $supportFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $repoRoot "tests") -Filter "*.py" -File |
    Where-Object { $_.Name -notlike "test_*.py" } |
    Sort-Object Name |
    ForEach-Object { "tests/" + $_.Name }
  )
  $pythonTargets = $sourceTargets + $testFiles + $supportFiles
  # Include projected remote code in syntax, lint and security checks.
  $scriptTargets = @("scripts")
  # Remote files are projected byte-for-byte and have their own runtime gates.
  # Formatting therefore covers the full scripts tree; mypy keeps the narrower
  # top-level compatibility contract.
  $scriptTopLevelTargets = @(
    Get-ChildItem -LiteralPath (Join-Path $repoRoot "scripts") -Filter "*.py" -File |
      Sort-Object Name |
      ForEach-Object { "scripts/" + $_.Name }
  )
  if ($Profile -eq "Focused") {
    if ($RunDependencyAudit) {
      Assert-IsolatedPythonForEnvironmentGate -Python $python -GateId "dependency-audit"
    }
    $focusedTargets = @(Resolve-FocusedPythonTargets -Paths $FocusPath -Description "Focused gate")
    if ($focusedTargets.Count -eq 0) {
      throw "Focused profile requires at least one -FocusPath Python file or directory."
    }
    # Pass only explicitly requested tests to pytest. Source files still get
    # the static legs; exit 5 tolerates a directory with no collected tests.
    $focusedTestTargets = @(
      $focusedTargets | Where-Object {
        if (Test-Path -LiteralPath $_ -PathType Container) {
          return $true
        }
        $leaf = Split-Path -Leaf $_
        $leaf -like "test_*.py" -or $leaf -like "*_test.py"
      }
    )
    $commands = @()
    if ($focusedTestTargets.Count -gt 0) {
      $commands += @{
        Id = "focused:test"
        ToleratedExitCodes = @(5)
        Command = @($pythonExe, "-m", "pytest", "-q") + $focusedTestTargets
      }
    }
    $commands += @(
      @{ Id = "focused:ruff"; Command = @($pythonExe, "-m", "ruff", "check") + $focusedTargets },
      @{ Id = "focused:format"; Command = @($pythonExe, "-m", "ruff", "format", "--check") + $focusedTargets },
      @{ Id = "focused:mypy"; Command = @($pythonExe, "-m", "mypy", "--explicit-package-bases") + $focusedTargets }
    )
    if ($RunDependencyAudit) {
      $commands += @(
        @{ Id = "invariant:pip-check"; Command = @($pythonExe, "-m", "pip", "check") },
        @{ Id = "invariant:dependency-audit"; Command = @($pythonExe, "-m", "pip_audit", "-r", "requirements.txt") }
      )
    }
  } elseif ($Profile -eq "Integration") {
    $commands = @(
      @{ Id = "integration:test"; Command = @($pythonExe, "-m", "pytest", "-q", "tests/test_integration_real_ssh.py") }
    )
  } else {
    $commands = @()
    if ($FullScope -in @("All", "Runtime")) {
      $commands += @(
        # Importing the installed console entry point catches packaging and
        # import errors that compileall used to re-check syntactically.
        @{
          Id = "build"
          Command = @(
            $pythonExe,
            "-c",
            "import vps_ssh_launcher, vps_ssh_launcher.maintenance_cli"
          )
        },
        @{ Id = "test"; Command = @($pythonExe, "-m", "pytest", "-q") }
      )
    }

    if ($RunDependencyAudit) {
      Assert-IsolatedPythonForEnvironmentGate -Python $python -GateId "dependency-audit"
      $commands += @(
        @{ Id = "invariant:pip-check"; Command = @($pythonExe, "-m", "pip", "check") },
        @{ Id = "invariant:dependency-audit"; Command = @($pythonExe, "-m", "pip_audit", "-r", "requirements.txt") }
      )
    }

    if ($FullScope -eq "Format") {
      $commands += @(
        @{ Id = "lint:format"; Command = @($pythonExe, "-m", "ruff", "format", "--check") + $pythonTargets + $scriptTargets }
      )
    } elseif ($FullScope -in @("All", "Static")) {
      $commands += @(
        # Product code keeps the strict any-severity contract.
        @{ Id = "hotspot:bandit"; Command = @($pythonExe, "-m", "bandit", "-q", "-r") + $sourceTargets },
        # The scripts tree is scanned at Medium+ only: its Low findings are
        # structural to a maintenance tool that shells out to local binaries
        # (B404/B603/B607) and to acceptance harnesses that assert on disposable
        # fixtures (B101/B110). Anything Medium or higher still fails the gate.
        @{ Id = "hotspot:bandit-scripts"; Command = @($pythonExe, "-m", "bandit", "-q", "-ll", "-r") + $scriptTargets },
        @{ Id = "lint:ruff"; Command = @($pythonExe, "-m", "ruff", "check") + $pythonTargets + $scriptTargets },
        @{ Id = "lint:format"; Command = @($pythonExe, "-m", "ruff", "format", "--check") + $pythonTargets + $scriptTargets },
        @{ Id = "type:mypy"; Command = @($pythonExe, "-m", "mypy", "--explicit-package-bases") + $pythonTargets + $scriptTopLevelTargets }
      )
    }
  }

  # Only this invocation's explicit switch may enable real SSH in a gate.
  $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = if ($RunIntegration) { "1" } else { "0" }
  if ($RunIntegration) {
    $effectiveIntegrationConfig = Resolve-IntegrationConfigPath `
      -ConfigPath $IntegrationConfig `
      -RepoRoot $repoRoot
    Assert-IntegrationProfileIsNonInteractive `
      -ConfigPath $effectiveIntegrationConfig `
      -ProfileName $IntegrationProfile
    if ($effectiveIntegrationConfig) {
      $env:VPS_SSH_LAUNCHER_INTEGRATION_CONFIG = $effectiveIntegrationConfig
    }
    # Do not turn a one-profile integration file into an interactive CI prompt.
    $effectiveProfile = ""
    if ($effectiveIntegrationConfig) {
      $effectiveProfile = Resolve-IntegrationProfile `
        -ConfigPath $effectiveIntegrationConfig `
        -ProfileName $IntegrationProfile
    } elseif ($IntegrationProfile) {
      $effectiveProfile = $IntegrationProfile
    }
    if ($effectiveProfile) {
      $env:VPS_SSH_LAUNCHER_INTEGRATION_PROFILE = $effectiveProfile
    }
  }

  foreach ($entry in $commands) {
    Write-Host "==> $($entry.Id): $($entry.Command -join ' ')"
    & $entry.Command[0] @($entry.Command[1..($entry.Command.Count - 1)])
    if ($LASTEXITCODE -ne 0) {
      if (@($entry.ToleratedExitCodes) -contains $LASTEXITCODE) {
        Write-Host "    exit $LASTEXITCODE tolerated for $($entry.Id)"
        continue
      }
      throw "Gate failed: $($entry.Id)"
    }
  }
} finally {
  foreach ($name in $integrationEnvironment.Keys) {
    [Environment]::SetEnvironmentVariable($name, $integrationEnvironment[$name], "Process")
  }
  Pop-Location
}

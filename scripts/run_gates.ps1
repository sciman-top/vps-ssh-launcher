#requires -Version 7
param(
  # Auto picks Focused or Full from the current diff, so "which gate does this
  # change need" stops being a judgement call that errs toward "run everything".
  [ValidateSet("Auto", "Focused", "Full", "Integration")]
  [string]$Profile = "Auto",
  [ValidateSet("All", "Runtime", "Static", "Format")]
  [string]$FullScope = "All",
  [string[]]$FocusPath = @(),
  [switch]$RunDependencyAudit,
  # Opt-in, report-only: coverage answers "which branch was never exercised",
  # not "did this change pass". A hard threshold on a repo whose suite is
  # mostly contract tests would only teach people to game the number.
  [switch]$RunCoverage,
  [switch]$RunIntegration,
  [switch]$AllowGlobalPython,
  [string]$IntegrationConfig,
  [string]$IntegrationProfile
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")
$platform = if ($IsLinux) { "linux" } else { "win32" }
$integrationEnvironment = @{}
foreach ($name in @(
  "VPS_SSH_LAUNCHER_RUN_INTEGRATION",
  "VPS_SSH_LAUNCHER_INTEGRATION_CONFIG",
  "VPS_SSH_LAUNCHER_INTEGRATION_PROFILE",
  "MYPY_PLATFORM",
  "VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP"
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

    # One resolver, shared with every wrapper: Resolve-LauncherConfigPath owns
    # the user-then-repo precedence. The previous local copy probed
    # %APPDATA%\vps-ssh-launcher\<platform>\target.json, a layout the
    # launcher never writes, so an implicit integration config silently fell
    # through to the repository file and skipped the non-interactive profile
    # guard below. Keep the empty-string contract: callers test for it.
    $resolved = Resolve-LauncherConfigPath -ProjectRoot $RepoRoot -Config ""
    if ($resolved -and (Test-Path -LiteralPath $resolved)) {
      return $resolved
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
        $candidate = $path -replace "/", [IO.Path]::DirectorySeparatorChar
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

  # Auto: any touched file that can change runtime behaviour forces the full
  # gate; a rules/docs/test-only diff stays focused. This is deliberately
  # conservative -- an unknown path counts as risky.
  function Resolve-AutoProfile {
    param([string]$RepoRoot)
    # One working-tree read serves both the risk decision and the focus list,
    # so the caller never repeats these two git calls.
    $riskyPatterns = @(
      '^vps_ssh_launcher/',
      '^scripts/',
      '^ssh_tool\.py$',
      '^auto_install\.py$',
      '^connect\.(ps1|cmd)$',
      '^run\.cmd$',
      '^pyproject\.toml$',
      '^requirements\.txt$',
      '^\.github/workflows/'
    )
    $safePatterns = @(
      '^(docs|tests|outputs)/',
      '^AGENTS\.md$',
      '^README(\.en)?\.md$',
      '^CLAUDE\.md$',
      '^GEMINI\.md$'
    )

    $changed = @()
    $diff = & git -C $RepoRoot diff --name-only HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $diff) {
      $changed += @($diff)
    }
    $untracked = & git -C $RepoRoot ls-files --others --exclude-standard 2>$null
    if ($LASTEXITCODE -eq 0 -and $untracked) {
      $changed += @($untracked)
    }
    $changed = @($changed | Where-Object { $_ } | Sort-Object -Unique)
    if ($changed.Count -eq 0) {
      Write-Host "==> auto: no working-tree change; running the full gate"
      return "Full"
    }

    $risky = @($changed | Where-Object {
      $path = $_ -replace '\\', '/'
      ($riskyPatterns | Where-Object { $path -match $_ }).Count -gt 0
    })
    if ($risky.Count -gt 0) {
      Write-Host "==> auto: runtime surface touched -> Full ($($risky -join ', '))"
      return "Full"
    }
    $unknown = @($changed | Where-Object {
      $path = $_ -replace '\\', '/'
      ($safePatterns | Where-Object { $path -match $_ }).Count -eq 0
    })
    if ($unknown.Count -gt 0) {
      Write-Host "==> auto: unclassified path -> Full ($($unknown -join ', '))"
      return "Full"
    }
    $script:AutoChangedPaths = $changed
    Write-Host "==> auto: test/doc change -> Focused ($($changed.Count) files)"
    return "Focused"
  }

  # "Skip" is not a member of the -Profile ValidateSet: assigning it to
  # $Profile raises MetadataError at runtime, so the document-only outcome
  # travels in its own variable instead of overloading the parameter.
  $autoSkip = $false
  if ($Profile -eq "Auto") {
    if ((Resolve-AutoProfile -RepoRoot $repoRoot) -eq "Focused") {
      $autoPython = @($AutoChangedPaths | Where-Object { $_ -match '\.py$' })
      if ($autoPython.Count -eq 0) {
        Write-Host "==> auto: nothing to lint or test; running doc checks instead"
        $autoSkip = $true
      } else {
        $Profile = "Focused"
        $FocusPath = @($autoPython)
      }
    } else {
      $Profile = "Full"
    }
  }

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
  if ($Profile -ne "Full" -and $FullScope -ne "All") {
    throw "-FullScope is only valid with -Profile Full."
  }

  $python = Resolve-ProjectPython -ProjectRoot $repoRoot -PathPythonIsIsolatedInCi
  $pythonExe = $python.Exe

  if ($autoSkip) {
    # Auto found nothing to lint or test (rules/docs-only diff): the two cheap
    # checks that still apply. This branch needs the resolved interpreter, so
    # it lives after Resolve-ProjectPython, not next to the Auto decision.
    $diffCheck = & git -C $repoRoot diff --check
    if ($LASTEXITCODE -ne 0) {
      throw "git diff --check failed: $diffCheck"
    }
    & $pythonExe (Join-Path $repoRoot "scripts/check_docs.py")
    if ($LASTEXITCODE -ne 0) {
      throw "scripts/check_docs.py failed"
    }
    Write-Host "GATE_RESULT=PASS (auto: document-only change)"
    return
  }
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
        @{ Id = "invariant:dependency-audit"; Command = @($pythonExe, "scripts/dependency_audit.py") }
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
        # Cheapest contract first: a catalog mismatch is reported in
        # milliseconds, before the import check and long before the suite or
        # an -Apply round trip.
        @{ Id = "contract:catalog"; Command = @($pythonExe, "scripts/cpa_catalog_check.py") },
        # Importing the installed console entry point catches packaging and
        # import errors that compileall used to re-check syntactically.
        @{
          Id = "build"
          Command = @(
            $pythonExe,
            "-c",
            "import vps_ssh_launcher, vps_ssh_launcher.maintenance_cli"
          )
        }
      )
      $pytestArgs = @("-m", "pytest", "-q")
      if ($RunCoverage) {
        # One coverage invocation for the same suite: --cov-target is the
        # launcher package, and the scripts tree is measured separately so a
        # projected remote runtime does not dilute the number.
        $pytestArgs += @(
          "--cov=vps_ssh_launcher",
          "--cov=scripts",
          "--cov-report=term-missing:skip-covered"
        )
      }
      $commands += @{ Id = "test"; Command = @($pythonExe) + $pytestArgs }
      if ($IsLinux) {
        # Fixture writes are private by default; run the original permission
        # contract explicitly, then make this disposable run's fixtures private.
        $hostPrivateTemp = $env:VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP -eq "1"
        $env:VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP = "0"
        $commands += @{
          Id = "test:filesystem-baseline"
          Command = @(
            $pythonExe,
            "-m",
            "pytest",
            "-q",
            "tests/test_config.py::ConfigTests::test_target_config_auto_tightens_other_user_read_access",
            "tests/test_safe_files.py::test_atomic_json_new_file_is_private"
          )
        }
        if (-not $hostPrivateTemp) {
          $env:VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP = "1"
        }
      }
    }

    if ($RunDependencyAudit) {
      Assert-IsolatedPythonForEnvironmentGate -Python $python -GateId "dependency-audit"
      $commands += @(
        @{ Id = "invariant:pip-check"; Command = @($pythonExe, "-m", "pip", "check") },
        @{ Id = "invariant:dependency-audit"; Command = @($pythonExe, "scripts/dependency_audit.py") }
      )
    }

    if ($FullScope -eq "Format") {
      $commands += @(
        @{ Id = "lint:format"; Command = @($pythonExe, "-m", "ruff", "format", "--check") + $pythonTargets + $scriptTargets }
      )
    } elseif ($FullScope -in @("All", "Static")) {
      $commands += @(
        # Tests import the same package modules as the launcher; scanning the
        # full Python target set closes that import path instead of leaving it
        # exercised only on CI's static runner.
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
  # Linux CPython omits ctypes.wintypes, while the Windows runner checks
  # Windows-only compatibility tests. Type-check against the actual gate host.
  # In tests, model that platform without switching the process OS: the
  # Windows ACL guard follows the process's real host behavior.
  $env:MYPY_PLATFORM = if ($env:VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP -eq "1") {
    "test-" + $platform
  } else {
    $platform
  }
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

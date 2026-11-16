#requires -Version 7
param(
  # Auto picks Focused or Full from the current diff, so "which gate does this
  # change need" stops being a judgement call that errs toward "run everything".
  [ValidateSet("Auto", "Focused", "Full", "Integration")]
  [string]$Profile = "Auto",
  [ValidateSet("All", "Runtime", "Static")]
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
  [string]$IntegrationProfile,
  # Opt-in, as the docs put it: "considerable speed ups, especially if your
  # test suite takes a noticeable amount of time"
  # (https://pytest-xdist.readthedocs.io/en/stable/distribution.html). The
  # serial order stays the default so the gate keeps producing one canonical
  # log; a parallel run proves itself before it may stand in for that log.
  [switch]$RunParallel,
  [ValidateRange(2, 32)]
  [int]$MaxWorkers = 4,
  # Explicit proof escape hatch for -ProveParallel: trust the recorded
  # parallel-proof.json without revalidating its tree or worker count. The
  # test receipt itself is still content-keyed, so a changed tree still
  # re-runs the suite; ordinary parallel runs never pay for a serial
  # duplicate, with or without this switch.
  [switch]$ReuseReceipts,
  # Opt-in compatibility check for the parallel lane. The normal parallel
  # gate runs the suite once; this switch adds one serial recheck and records
  # the result in parallel-proof.json for later reuse.
  [switch]$ProveParallel
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")
$integrationEnvironment = @{}
foreach ($name in @(
  "VPS_SSH_LAUNCHER_RUN_INTEGRATION",
  "VPS_SSH_LAUNCHER_INTEGRATION_CONFIG",
  "VPS_SSH_LAUNCHER_INTEGRATION_PROFILE",
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

    $bootstrap = 'python -m venv .venv; .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock; .\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .'
    throw (
      "Gate '$GateId' needs an isolated project Python environment because it " +
      "checks the whole interpreter environment. Create one with '$bootstrap', " +
      "set VPS_SSH_LAUNCHER_PYTHON, or pass -AllowGlobalPython only when the " +
      "global interpreter is known to be dedicated to this repo. Current source: " +
      "$($Python.Source)."
    )
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

  function ConvertTo-GatePythonPath {
    param([string[]]$Prefixes)

    # An empty PYTHONPATH entry is a no-op for that variable and load-bearing
    # for the worker bootstrap: pytest rewrites a PYTHONPATH it finds in the
    # environment into its own sys.path changes and drops the original, so a
    # worker must see one here or the prelude never runs.
    $segments = @($Prefixes | Where-Object { $_ })
    if ($env:PYTHONPATH) {
      $segments += $env:PYTHONPATH
    }
    return ($segments -join [IO.Path]::PathSeparator)
  }

  function Get-GateWorkerEnvironment {
    param([int]$WorkerCount)

    # xdist starts each worker as its own process and a child inherits this
    # process TEMP/TMP. scripts/dsh_gate_xdist_worker_prelude.py moves each
    # worker TEMP under the run root, keeping its fixtures private there
    # instead of in the shared host temp directory. -n resolves its worker
    # count from the environment, so the requested count needs no second flag.
    # Each gate invocation gets an isolated leaf. A previous xdist run can
    # still be finishing child cleanup when the parent returns; reusing one
    # fixed root would let that stale run race with a fresh -ReuseReceipts
    # invocation and remove or repopulate the fresh workers' directories.
    $workerRoot = Join-Path `
      (Join-Path $script:runRoot "tmp") `
      ("run-" + [guid]::NewGuid().ToString("N"))
    $script:workerTempRoot = $workerRoot
    New-Item -ItemType Directory -Path $workerRoot -Force | Out-Null
    for ($index = 0; $index -lt $WorkerCount; $index++) {
      New-Item -ItemType Directory -Path (Join-Path $workerRoot "gw$index") -Force |
        Out-Null
    }
    if ($IsWindows) {
      # POSIX workers inherit a private temp from their own os. A Windows
      # worker tree inside the checkout would otherwise carry the checkout's
      # broad ACLs into every fixture, and credential-shaped fixtures (SSH
      # keys, target.json) then fail the launcher's own privacy contract on
      # a shared runner. Harden to the same tolerated principals as
      # vps_ssh_launcher.windows_acl: owner rights, SYSTEM, Administrators;
      # the inheritable grants cover the per-worker leaves created below.
      $aclGrant = @(
        "*S-1-3-4:(OI)(CI)F"
        "*S-1-5-18:(OI)(CI)F"
        "*S-1-5-32-544:(OI)(CI)F"
      )
      & icacls $workerRoot /inheritance:r /grant:r @aclGrant | Out-Null
      if ($LASTEXITCODE -ne 0) {
        throw "Gate worker temp root could not be made private: $workerRoot"
      }
    }
    return @{
      PYTEST_XDIST_AUTO_NUM_WORKERS = [string]$WorkerCount
      # Per run: a crashed run cannot leave fixtures for the next one to
      # inherit, and the whole subtree is removed in finally.
      VPS_SSH_LAUNCHER_GATE_TEMP_ROOT = $workerRoot
      # The scripts directory is the import root for the worker prelude.
      # tests/conftest.py calls it after pytest starts each worker.
      PYTHONPATH = ConvertTo-GatePythonPath -Prefixes @(
        "scripts"
      )
    }
  }

  function Get-GateTestProperties {
    param(
      [string]$RepoRoot,
      [string]$PythonExe
    )

    # Every file the lane could read, minus working state. A recorded PASS is
    # only reused against a byte-identical tree and the same interpreter, so
    # an edit during the run invalidates the receipt the run is writing.
    $hashScript = Join-Path $RepoRoot "scripts/gate_content_hash.py"
    $raw = & $PythonExe $hashScript $RepoRoot
    if ($LASTEXITCODE -ne 0) {
      throw "test-receipt: hashing the test inputs failed"
    }
    return ($raw | Out-String | ConvertFrom-Json)
  }
  function Test-GateReceipt {
    param(
      [string]$RelativePath,
      [ValidateSet("Any", "Serial", "Parallel")]
      [string]$Lane = "Any"
    )

    if (-not (Test-Path -LiteralPath $RelativePath)) {
      return $false
    }
    try {
      $receipt = Get-Content -LiteralPath $RelativePath -Raw | ConvertFrom-Json
    } catch {
      return $false
    }
    if ($receipt.profile -ne "Full" -or $receipt.status -ne "PASS") {
      return $false
    }
    if ($receipt.tree -ne $script:testProperties.tree) {
      return $false
    }
    if ($receipt.environment -ne $script:testProperties.environment) {
      return $false
    }
    $workers = 0
    if ($null -ne $receipt.workers) {
      try {
        $workers = [int]$receipt.workers
      } catch {
        return $false
      }
    }
    if ($Lane -eq "Parallel" -and $workers -ne $MaxWorkers) {
      return $false
    }
    if ($Lane -eq "Serial" -and $workers -ne 0) {
      return $false
    }
    return $true
  }

  function Write-GateReceipt {
    param(
      [string]$RelativePath,
      [string]$Status,
      [double]$Seconds,
      [int]$Workers = 0,
      [int]$Tests = 0
    )

    $directory = Split-Path -Parent $RelativePath
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
      New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $receipt = [ordered]@{
      status = $Status
      profile = "Full"
      tree = $script:testProperties.tree
      environment = $script:testProperties.environment
      seconds = [math]::Round($Seconds, 1)
      workers = $Workers
      host = if ($env:COMPUTERNAME) { $env:COMPUTERNAME } else { [Environment]::MachineName }
      recorded_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    # Whole-test outcomes from the run's own summary line (~collected on a
    # green run): makes suite growth an observable in the receipt instead of
    # a number that only exists in review ledgers.
    if ($Tests -gt 0) {
      $receipt["tests"] = $Tests
    }
    $receipt | ConvertTo-Json | Set-Content -LiteralPath $RelativePath -Encoding utf8
  }

  function Remove-GateWorkerTemporaryRoots {
    param([string]$Root)

    # A parallel run owns this directory outright, so whatever a worker left
    # behind (crashed fixture trees, spilled scripts) goes with it. Serial runs
    # keep their fixtures on the host temp path they always used.
    if (-not (Test-Path -LiteralPath $Root)) {
      return
    }
    $lastError = $null
    # Windows worker processes can release SQLite/file handles a few seconds
    # after pytest has printed its summary. Keep the cleanup bounded, but give
    # normal xdist teardown a 30-second window before reporting a leak.
    $maxAttempts = 120
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
      try {
        Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction Stop
        return
      } catch {
        $lastError = $_.Exception.Message
        if ($attempt -lt $maxAttempts) {
          Start-Sleep -Milliseconds 250
        }
      }
    }
    if (Test-Path -LiteralPath $Root) {
      Write-Host "    warning: could not remove $Root ($lastError)"
    }
  }

  function Resolve-TestLanePlan {
    param([object[]]$Entries)

    # Ordinary parallel runs execute the suite once. A serial recheck is an
    # explicit proof mode because paying for a second full suite on every
    # developer run defeats the throughput benefit of xdist.
    $testEntry = $null
    $others = @()
    foreach ($entry in $Entries) {
      if ($entry.Id -eq "test-lane") {
        $testEntry = $entry
      } else {
        $others += $entry
      }
    }
    if ($null -eq $testEntry) {
      return $Entries
    }

    $skipNextPytestArgument = $false
    $serialCommand = @(
      foreach ($argument in @($testEntry.Command)) {
        if (
          $argument -eq "-n" -or
          $argument -eq "--dist" -or
          $argument -eq "--max-worker-restart"
        ) {
          $skipNextPytestArgument = $true
          continue
        }
        if ($skipNextPytestArgument) {
          $skipNextPytestArgument = $false
          continue
        }
        $argument
      }
    )
    if ($serialCommand -notcontains "--import-mode=importlib") {
      $serialCommand += "--import-mode=importlib"
    }
    $serialEntry = @{
      Id = "test"
      Command = $serialCommand
      Environment = $testEntry.Environment
      Receipt = $testEntry.Receipt
      Repeatable = $testEntry.Repeatable
    }
    if (-not $script:testParallel) {
      return @($others) + @($serialEntry)
    }
    if (-not $ProveParallel) {
      return @($others) + @($testEntry)
    }

    # Proof is evidence about this host and tree, so it follows the receipt
    # exactly the way every other reused answer does.
    $proofPath = Join-Path $script:receiptDirectory "parallel-proof.json"
    if (
      -not $RunCoverage -and
      ($ReuseReceipts -or (Test-GateReceipt -RelativePath $proofPath -Lane Parallel))
    ) {
      return @($others) + @($testEntry)
    }

    $parallelEntry = @{
      Id = "test"
      Command = $testEntry.Command
      Environment = $testEntry.Environment
      Repeatable = $testEntry.Repeatable
    }
    # The one serial run that decides whether the parallel answer may stand.
    $serialRecheck = @{
      Id = "test:serial-recheck"
      Command = $serialCommand
      Environment = $testEntry.Environment
      Receipt = $testEntry.Receipt
      SerialRecheck = $true
      Repeatable = $testEntry.Repeatable
    }
    return @($others) + @($parallelEntry) + @($serialRecheck)
  }

  function Get-PytestSummaryVector {
    param([string[]]$Lines)

    # pytest reports on one line: "1708 passed, 31 skipped, 810 subtests passed
    # in 90.31s". Duration is the only field a parallel run may change; every
    # other field, subtest assertions included, is a claim about behaviour.
    $vector = @{ counts = @(); sawRun = $false }
    foreach ($line in $Lines) {
      $text = [string]$line
      if (-not $text.Contains(" in ")) {
        continue
      }
      # A summary line ends with " in <duration>"; strip that once so no field
      # below can absorb the duration into its own name.
      $head = $text.Substring(0, $text.LastIndexOf(" in "))
      if ($head -notmatch "(\d+) passed") {
        continue
      }
      $vector.sawRun = $true
      $counts = @()
      foreach ($piece in ($head -split ",")) {
        $trimmed = $piece.Trim()
        if ($trimmed -match "^(\d+) (.+)$") {
          $counts += ($Matches[2] + "=" + $Matches[1])
        }
      }
      $vector.counts = @($counts | Sort-Object)
    }
    return $vector
  }
  function Compare-ParallelAndSerialSummaries {
    param($Parallel, $Serial)

    # Exact agreement is the norm; the tolerant mode exists because pytest
    # counts a subtest assertion as "subtests passed", so a test that fails its
    # parent assertion part-way through legitimately reports fewer of them on a
    # second run. What must never move is the number of tests that passed,
    # skipped, failed or errored: those are whole tests, not assertions.
    if ($Parallel.counts.Count -eq 0 -or $Serial.counts.Count -eq 0) {
      return @{ Ok = $false; Mode = ""; Reason = "one of the two runs reported no pytest summary line" }
    }
    $a = @($Parallel.counts) -join ", "
    $b = @($Serial.counts) -join ", "
    if ($a -eq $b) {
      return @{ Ok = $true; Mode = "exact"; Reason = "" }
    }
    $stable = @("passed", "failed", "skipped", "errors")
    $filtered = {
      param([string[]]$Counts)
      @($Counts | Where-Object {
        $name = ($_ -split "=")[0]
        $stable -contains $name
      })
    }
    $aStable = & $filtered $Parallel.counts
    $bStable = & $filtered $Serial.counts
    if (($aStable -join ", ") -eq ($bStable -join ", ")) {
      return @{ Ok = $true; Mode = "whole-tests"; Reason = "" }
    }
    $reason = "parallel run reported [" + $a + "] but the serial run reported [" + $b + "]"
    return @{ Ok = $false; Mode = ""; Reason = $reason }
  }

  function Invoke-GateLeg {
    param(
      [hashtable]$Entry,
      [hashtable]$Environment
    )

    # Per-command environment overrides are applied only around this command:
    # the previous build-time mutation leaked into every later gate and made
    # the documented "private fixtures" switch a no-op.
    $overrides = if ($Environment) { $Environment } else { @{} }
    $saved = @{}
    foreach ($name in $overrides.Keys) {
      $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
      [Environment]::SetEnvironmentVariable($name, [string]$overrides[$name], "Process")
    }
    # Tee instead of a bare pipe: only two legs consume their own output (the
    # summary vector and the proof baseline), and every other leg keeps
    # streaming to the console exactly as before.
    $output = New-Object System.Collections.Generic.List[string]
    $lines = @()
    try {
      & $Entry.Command[0] @($Entry.Command[1..($Entry.Command.Count - 1)]) | Tee-Object -Variable lines | Out-Null
      foreach ($line in $lines) {
        $output.Add([string]$line)
        Write-Host $line
      }
    } finally {
      foreach ($name in $saved.Keys) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process")
      }
    }
    return $output
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

  $script:parallelSummary = $null
  $script:testParallel = $false
  $script:testProperties = $null
  $script:workerTempRoot = $null
  $script:runRoot = Join-Path $repoRoot (Join-Path "outputs" "gates")
  # Machine-local, never a product artifact: a receipt is evidence about this
  # working tree on this host, so it lives outside docs/change-evidence and
  # stays untracked.
  $script:receiptDirectory = Join-Path $script:runRoot "receipts"

  Initialize-WindowsProcessEnvironment

  # Auto: any touched file that can change runtime behaviour forces the full
  # gate; a rules/docs/test-only diff stays focused. This is deliberately
  # conservative -- an unknown path counts as risky.
  function Resolve-AutoProfile {
    param(
      [string]$RepoRoot,
      [ref]$ChangedPaths
    )
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
    if ($LASTEXITCODE -ne 0) {
      throw "auto profile could not inspect the working tree with git diff."
    }
    if ($diff) {
      $changed += @($diff)
    }
    $untracked = & git -C $RepoRoot ls-files --others --exclude-standard 2>$null
    if ($LASTEXITCODE -ne 0) {
      throw "auto profile could not inspect untracked files with git ls-files."
    }
    if ($untracked) {
      $changed += @($untracked)
    }
    $changed = @($changed | Where-Object { $_ } | Sort-Object -Unique)
    if ($changed.Count -eq 0) {
      Write-Host "==> auto: no working-tree change; no gate needed"
      return "NoChanges"
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
    $ChangedPaths.Value = @($changed)
    Write-Host "==> auto: test/doc change -> Focused ($($changed.Count) files)"
    return "Focused"
  }

  # "Skip" is not a member of the -Profile ValidateSet: assigning it to
  # $Profile raises MetadataError at runtime, so the document-only outcome
  # travels in its own variable instead of overloading the parameter.
  $autoSkip = $false
  $autoNoChanges = $false
  $autoChangedPaths = @()
  if ($Profile -eq "Auto") {
    $autoDecision = Resolve-AutoProfile `
      -RepoRoot $repoRoot `
      -ChangedPaths ([ref]$autoChangedPaths)
    if ($autoDecision -eq "NoChanges") {
      if (
        $RunDependencyAudit -or
        $RunCoverage -or
        $RunIntegration -or
        $FullScope -ne "All"
      ) {
        # An explicit extra gate is still a request to do work, even when the
        # working tree is clean. Keep the normal Full path for that request.
        $Profile = "Full"
      } else {
        $autoNoChanges = $true
      }
    } elseif ($autoDecision -eq "Focused") {
      $autoPython = @($autoChangedPaths | Where-Object { $_ -match "\.py$" })
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
  if ($ProveParallel -and -not $RunParallel) {
    throw "-ProveParallel requires -RunParallel."
  }
  if ($Profile -ne "Focused" -and @($FocusPath | Where-Object { $_ }).Count -gt 0) {
    throw "-FocusPath is only valid with -Profile Focused."
  }
  if ($Profile -ne "Full" -and $FullScope -ne "All") {
    throw "-FullScope is only valid with -Profile Full."
  }

  if ($autoNoChanges) {
    Write-Host "GATE_RESULT=PASS (auto: no working-tree change)"
    return
  }

  $python = Resolve-ProjectPython -ProjectRoot $repoRoot -PathPythonIsIsolatedInCi
  $pythonExe = $python.Exe
  # Establish the integration boundary before any helper process is invoked.
  # Receipt hashing and other preflight helpers must observe the same explicit
  # opt-in value as the actual test and static legs.
  $env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = if ($RunIntegration) { "1" } else { "0" }

  # -RunParallel is a request for the faster lane, not a promise it is
  # available. Falling back to the serial run keeps the gate's answer intact
  # on a host without xdist instead of failing the change under test.
  if ($RunParallel -and $Profile -in @("Focused", "Full")) {
    & $pythonExe -c "import xdist" 2>$null
    if ($LASTEXITCODE -eq 0) {
      $script:testParallel = $true
    } else {
      Write-Host "==> parallel: pytest-xdist is not installed; running serially"
    }
  }
  if ($Profile -eq "Full") {
    # Every file the lane could read, minus working state. A recorded PASS is
    # only reused against a byte-identical tree and the same interpreter, so
    # an edit during the run invalidates the receipt the run is writing.
    $properties = Get-GateTestProperties -RepoRoot $repoRoot -PythonExe $pythonExe
    $script:testProperties = @{
      tree = [string]$properties.tree
      environment = [string]$properties.environment
    }
    New-Item -ItemType Directory -Path $script:receiptDirectory -Force | Out-Null
  }

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
    # Pass only explicitly requested tests to pytest. A support module or
    # conftest can change collection and fixtures for every test, so those
    # files widen the behavior leg to the tests tree. Source files still get
    # the static legs; exit 5 tolerates a directory with no collected tests.
    $focusedRunsAllTests = @(
      $focusedTargets | Where-Object {
        if (Test-Path -LiteralPath $_ -PathType Container) {
          return $true
        }
        $normalized = (Resolve-Path -LiteralPath $_).Path -replace '\\', '/'
        $testsRoot = (Resolve-Path -LiteralPath (Join-Path $repoRoot "tests")).Path -replace '\\', '/'
        $normalized.StartsWith("$testsRoot/") -and
          $normalized.EndsWith(".py") -and
          (Split-Path -Leaf $_) -notlike "test_*.py" -and
          (Split-Path -Leaf $_) -notlike "*_test.py"
      }
    ).Count -gt 0
    if ($focusedRunsAllTests) {
      $focusedTestTargets = @("tests")
    } else {
      $focusedTestTargets = @(
        $focusedTargets | Where-Object {
          if (Test-Path -LiteralPath $_ -PathType Container) {
            return $true
          }
          $leaf = Split-Path -Leaf $_
          $leaf -like "test_*.py" -or $leaf -like "*_test.py"
        }
      )
    }
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
      # The serial run keeps the flag it has always carried; the parallel run
      # reaches the same modules through pythonpath in pyproject.toml, so the
      # two lanes stay provenance-compatible instead of diverging silently.
      $pytestArgs = @("-m", "pytest", "-q")
      if (-not $script:testParallel) {
        $pytestArgs += "--import-mode=importlib"
      }
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
      # Fixture writes are private by default; run the original permission
      # contract explicitly, then make this disposable run's fixtures private.
      # The override is per command, not a mutation at build time: the main
      # "test" command was registered while the variable was still "0", so the
      # private branch in tests/conftest.py never ran inside the gate and this
      # "make fixtures private" step silently did nothing.
      $testEnvironment = @{ VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP = "1" }
      if ($script:testParallel) {
        $testEnvironment += Get-GateWorkerEnvironment -WorkerCount $MaxWorkers
        $pytestArgs = @($pytestArgs | Select-Object -First 3) + @(
          "-n", "auto", "--dist", "loadfile", "--max-worker-restart", "3"
        ) + @($pytestArgs | Select-Object -Skip 3)
      }
      $commands += @{
        Id = "test-lane"
        Command = @($pythonExe) + $pytestArgs
        Environment = $testEnvironment
        Receipt = "test.json"
        Repeatable = $false
      }
      if ($IsLinux) {
        $commands += @{
          Id = "test:filesystem-baseline"
          Receipt = "test-filesystem-baseline.json"
          Command = @(
            $pythonExe,
            "-m",
            "pytest",
            "-q",
            "tests/test_config.py::ConfigTests::test_target_config_auto_tightens_other_user_read_access",
            "tests/test_safe_files.py::test_atomic_json_new_file_is_private"
          )
          Environment = @{ VPS_SSH_LAUNCHER_PRIVATE_TEST_TMP = "0" }
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

    if ($FullScope -in @("All", "Static")) {
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
        @{ Id = "lint:ruff"; Command = @($pythonExe, "-m", "ruff", "check") + $pythonTargets + $scriptTargets; Repeatable = $true },
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

  $commands = Resolve-TestLanePlan -Entries $commands

  foreach ($entry in $commands) {
    # Per-leg receipts: exactly the legs whose answer a test receipt cannot
    # stand in for, keyed on content rather than on host time stamps. A running
    # edit invalidates the tree hash, so a green receipt of an older tree can
    # never excuse the current one.
    $receiptLane = "Any"
    if ($entry.Id -eq "test-lane") {
      $receiptLane = "Parallel"
    } elseif ($entry.Id -eq "test" -or $entry.Id -eq "test:filesystem-baseline") {
      $receiptLane = "Serial"
    }
    if (
      $entry.Receipt -and
      -not $RunCoverage -and
      -not $entry.SerialRecheck -and
      (Test-Path -LiteralPath $script:receiptDirectory)
    ) {
      if (
        Test-GateReceipt `
          -RelativePath (Join-Path $script:receiptDirectory $entry.Receipt) `
          -Lane $receiptLane
      ) {
        Write-Host "==> $($entry.Id): reuse (inputs unchanged since the recorded PASS)"
        continue
      }
    }
    Write-Host "==> $($entry.Id): $($entry.Command -join ' ')"
    # Per-command environment overrides are applied only around this command:
    # Invoke-GateLeg owns both setting and restoring the overrides. Keeping
    # that lifecycle in one place prevents the caller from accidentally
    # passing the pre-override values back into the child process.
    $legWatch = [Diagnostics.Stopwatch]::StartNew()
    $legOutput = Invoke-GateLeg -Entry $entry -Environment $entry.Environment
    $repeatable = $true
    if ($null -ne $entry.Repeatable) {
      $repeatable = [bool]$entry.Repeatable
    }
    if ($LASTEXITCODE -ne 0 -and $script:testParallel -and $repeatable) {
      # A leg that only reads the tree can be re-asked once after a transient
      # host failure; its answer does not depend on the attempt. The suite is
      # deliberately not retried: it carries the parallel loop of stateful
      # harnesses, and a retry there would report a second attempt as if it
      # were the first, hiding exactly the failure this lane has to surface.
      Write-Host "    exit $LASTEXITCODE; retrying once (read-only leg)"
      $legWatch.Restart()
      $legOutput = Invoke-GateLeg -Entry $entry -Environment $entry.Environment
    }
    if ($LASTEXITCODE -ne 0) {
      if (@($entry.ToleratedExitCodes) -contains $LASTEXITCODE) {
        Write-Host "    exit $LASTEXITCODE tolerated for $($entry.Id)"
        continue
      }
      throw "Gate failed: $($entry.Id)"
    }
    $legWatch.Stop()
    # Only the parallel result and its serial recheck are compared; mapping
    # progress lines for every other leg would be pure overhead.
    $legSummary = Get-PytestSummaryVector -Lines $legOutput
    # Whole-test count for the receipt: same stable names the parallel proof
    # compares, so subtest churn cannot move the recorded number.
    $legTestCount = 0
    if ($legSummary.sawRun) {
      $legTestCount = @(
        @($legSummary.counts) | ForEach-Object {
          $parts = $_ -split "=", 2
          if (@("passed", "failed", "skipped", "errors") -contains $parts[0]) {
            [int]$parts[1]
          }
        }
      ) | Measure-Object -Sum | Select-Object -ExpandProperty Sum
      if ($null -eq $legTestCount) {
        $legTestCount = 0
      }
    }

    if ($entry.Id -eq "test" -and $script:testParallel) {
      $script:parallelSummary = $legSummary
    }
    if ($entry.SerialRecheck) {
      $recheck = Compare-ParallelAndSerialSummaries -Parallel $script:parallelSummary -Serial $legSummary
      if (-not $recheck.Ok) {
        throw "Gate failed: test:serial-recheck. " + $recheck.Reason
      }
      Write-Host "    PARALLEL_PROVEN=PASS"
      Write-Host "    PARALLEL_MATCH=OK"
      if (Test-Path -LiteralPath $script:receiptDirectory) {
        Write-GateReceipt `
          -RelativePath (Join-Path $script:receiptDirectory "parallel-proof.json") `
          -Status "PASS" `
          -Seconds $legWatch.Elapsed.TotalSeconds `
          -Workers $MaxWorkers `
          -Tests $legTestCount
        Write-Host "    parallel proven for this tree: later runs skip the serial recheck"
      }
    }
    if (
      $entry.Receipt -and
      (Test-Path -LiteralPath $script:receiptDirectory)
    ) {
      $receiptWorkers = 0
      if ($entry.Id -eq "test-lane" -or $entry.SerialRecheck) {
        $receiptWorkers = $MaxWorkers
      }
      Write-GateReceipt `
        -RelativePath (Join-Path $script:receiptDirectory $entry.Receipt) `
        -Status "PASS" `
        -Seconds $legWatch.Elapsed.TotalSeconds `
        -Workers $receiptWorkers `
        -Tests $legTestCount
    }
  }
} finally {
  foreach ($name in $integrationEnvironment.Keys) {
    [Environment]::SetEnvironmentVariable($name, $integrationEnvironment[$name], "Process")
  }
  if ($script:testParallel -and $script:workerTempRoot) {
    Remove-GateWorkerTemporaryRoots -Root $script:workerTempRoot
  }
  Pop-Location
}

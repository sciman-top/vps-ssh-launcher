#requires -Version 7
[CmdletBinding()]
param(
  [string]$Root = 'D:\CODE\vps-ssh-launcher\outputs\stream-retry-harness-20261101-validate',
  [string]$Codex = 'C:\Users\sciman\AppData\Local\OpenAI\Codex\bin\22cfb1d302613eae\codex.exe'
)
$ErrorActionPreference = 'Stop'
$homeDir = Join-Path $Root 'home'
$workDir = Join-Path $Root 'work'
$portFile = Join-Path $Root 'port.txt'
if (-not (Test-Path -LiteralPath $portFile)) {
  $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
  $probe.Start()
  $port = [int]$probe.LocalEndpoint.Port
  $probe.Stop()
  [IO.File]::WriteAllText($portFile, "$port", [Text.UTF8Encoding]::new($false))
} else {
  $port = [int](Get-Content -LiteralPath $portFile -Raw)
}
$null = [IO.Directory]::CreateDirectory($homeDir)
$null = [IO.Directory]::CreateDirectory($workDir)
$configPath = Join-Path $homeDir 'config.toml'
$fixture = Join-Path $Root 'fixture.sse'
$python = 'D:\CODE\vps-ssh-launcher\.venv\Scripts\python.exe'
$harness = 'D:\CODE\vps-ssh-launcher\outputs\stream-retry-harness'
& $python (Join-Path $harness 'fixture-sse.py') --model deepseek-v4.1-flash --out $fixture
if ($LASTEXITCODE -ne 0) { throw 'Fixture generation failed.' }

function Write-TestConfig([int]$StreamRetries) {
  $config = @"
model = "deepseek-v4.1-flash"
model_provider = "retry_fixture"
approval_policy = "never"
sandbox_mode = "read-only"

[model_providers.retry_fixture]
name = "local retry fixture"
base_url = "http://127.0.0.1:$port/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = $StreamRetries
stream_idle_timeout_ms = 3000
"@
  [IO.File]::WriteAllText($configPath, $config, [Text.UTF8Encoding]::new($false))
}

function Stop-Fixture {
  $connections = @(Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue)
  foreach ($owner in @($connections | Select-Object -ExpandProperty OwningProcess -Unique)) {
    if ($owner) {
      Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessId -eq $owner -and $_.CommandLine -like '*fault_server.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
  }
}

function Start-Fixture {
  Stop-Fixture
  [Net.ServicePointManager]::Expect100Continue = $false
  $env:FIRST_MODEL = 'deepseek-v4.1-flash'
  $process = Start-Process -FilePath $python `
    -ArgumentList @((Join-Path $harness 'fault_server.py'), $fixture, [string]$port, 'truncate') `
    -RedirectStandardOutput (Join-Path $Root 'server.out') `
    -RedirectStandardError (Join-Path $Root 'server.err') `
    -WindowStyle Hidden -PassThru
  $deadline = [DateTime]::UtcNow.AddSeconds(5)
  do {
    Start-Sleep -Milliseconds 100
    try {
      $state = Invoke-RestMethod -Uri "http://127.0.0.1:$port/state" -TimeoutSec 1
      if ($state.attempts -eq 0) { return $process }
    } catch {}
  } while ([DateTime]::UtcNow -lt $deadline)
  throw "Fault server did not become ready: $(Get-Content -LiteralPath (Join-Path $Root 'server.err') -Raw)"
}

function Invoke-Case([int]$StreamRetries) {
  Write-TestConfig $StreamRetries
  $server = Start-Fixture
  $saved = @(
    'CODEX_HOME','NO_PROXY','HTTPS_PROXY','HTTP_PROXY','ALL_PROXY'
  )
  $children = @{}
  try {
    $env:CODEX_HOME = $homeDir
    $env:NO_PROXY = '127.0.0.1,localhost'
    $env:HTTPS_PROXY = ''
    $env:HTTP_PROXY = ''
    $env:ALL_PROXY = ''
    $prompt = 'Reply with exactly OK. Do not call tools.'
    $prompt | & $Codex exec --json --skip-git-repo-check --sandbox read-only `
      --model deepseek-v4.1-flash -C $workDir - 2>&1 |
      Tee-Object -FilePath (Join-Path $Root "client-retry$StreamRetries.log") | Out-Null
    $exitCode = $LASTEXITCODE
    $state = Invoke-RestMethod -Uri "http://127.0.0.1:$port/state" -TimeoutSec 3
  } finally {
    Stop-Fixture
  }
  [ordered]@{
    stream_max_retries = $StreamRetries
    attempts = [int]$state.attempts
    exit_code = $exitCode
    reconnecting = [bool](Select-String -LiteralPath (Join-Path $Root "client-retry$StreamRetries.log") -SimpleMatch 'Reconnecting')
    terminal_failed = [bool](Select-String -LiteralPath (Join-Path $Root "client-retry$StreamRetries.log") -SimpleMatch 'turn.failed')
    terminal_completed = [bool](Select-String -LiteralPath (Join-Path $Root "client-retry$StreamRetries.log") -SimpleMatch 'turn.completed')
  }
}

$results = @()
try {
  $results += Invoke-Case 2
  $results += Invoke-Case 0
} finally {
  Stop-Fixture
}
$pass = (
  $results[0].stream_max_retries -eq 2 -and $results[0].attempts -eq 2 -and
  $results[0].exit_code -eq 0 -and $results[0].reconnecting -and
  $results[0].terminal_completed -and
  $results[1].stream_max_retries -eq 0 -and $results[1].attempts -eq 1 -and
  $results[1].exit_code -ne 0 -and $results[1].terminal_failed
)
$receipt = [ordered]@{
  acceptance = 'isolated SSE retry A/B through Codex CLI'
  pass = $pass
  client_version = (& $Codex --version 2>&1)
  model = 'deepseek-v4.1-flash'
  fixture = $fixture
  loopback_only = $true
  credentials_consumed = $false
  results = $results
  timestamp = (Get-Date).ToString('o')
}
$receipt | ConvertTo-Json -Depth 5 |
  Set-Content -LiteralPath (Join-Path $Root 'receipt.json') -Encoding utf8
$receipt | ConvertTo-Json -Depth 5
if (-not $pass) { exit 1 }

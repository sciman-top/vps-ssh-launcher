#requires -Version 7
<##
.SYNOPSIS
  Unified read-only/explicit-apply workflow for CPA capacity, 429, and slow paths.

.DESCRIPTION
  Keeps diagnosis, projection, reload-required state, and controlled replay as
  separate evidence layers.  Default Audit is read-only.  Remote projection is
  only allowed with -ApplyRemote; this script never stops Cockpit or API-bearing
  processes. OAuth recovery requires an explicit external recovery confirmation.
##>
param(
  [ValidateSet("Triage", "Audit", "Project", "Verify", "ControlledReplay", "WaitCapSimulation", "RecoverAfterReset", "RestartAndRecover")]
  [string]$Mode = "Audit",
  [string]$SidecarCandidatePath = "",
  [switch]$ApplyRemote,
  [switch]$SkipRemote,
  [string]$DoctorOutput = "",
  [switch]$QuotaResetConfirmed,
  [switch]$CooldownResetConfirmed,
  [ValidateSet("gpt-6.1-sol", "gpt-6-luna", "gpt-5.6-luna")]
  [string]$RecoveryModel = "gpt-6.1-sol",
  [ValidateRange(0.01, 720)]
  [double]$Hours = 4
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  $python = (Get-Command python).Source
}
$sidecar = Join-Path $PSScriptRoot "cockpit_sidecar_guardrails.ps1"
$providerHealth = Join-Path $PSScriptRoot "cockpit_provider_health.py"
$triage = Join-Path $PSScriptRoot "cpa_failure_triage.py"
$replay = Join-Path $PSScriptRoot "cockpit_non_oauth_replay.py"
$waitCap = Join-Path $PSScriptRoot "cockpit_gate_wait_cap_check.py"
$cpa = Join-Path $PSScriptRoot "cpa_bwg_guardrails.ps1"

function Invoke-Checked {
  param([string]$File, [string[]]$Arguments)
  & $File @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $File"
  }
}

function Invoke-Doctor {
  param([switch]$Apply)
  $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $cpa, "-Profile", "bwg")
  if ($Apply) { $args += "-Apply" }
  if ([string]::IsNullOrWhiteSpace($DoctorOutput)) {
    Invoke-Checked "pwsh" $args
    return
  }
  $parent = Split-Path -Parent (Resolve-Path -LiteralPath $DoctorOutput -ErrorAction SilentlyContinue)
  if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  & pwsh @args 2>&1 | Tee-Object -FilePath $DoctorOutput
  if ($LASTEXITCODE -ne 0) { throw "CPA doctor/apply failed with exit code $LASTEXITCODE" }
}

function Invoke-Triage {
  $args = @($triage, "--hours", "$Hours")
  if (-not [string]::IsNullOrWhiteSpace($DoctorOutput) -and
      (Test-Path -LiteralPath $DoctorOutput -PathType Leaf)) {
    $args += @("--doctor", $DoctorOutput)
  }
  Invoke-Checked $python $args
}

function Get-ProviderHealth {
  # The sidecar state gate only applies when Desktop actually targets the
  # local 10909 provider gateway. In public_gateway mode the selected target
  # is fq.sciman.top and 10909/14185 being absent is the expected state. Keep
  # the mode decision tied to the same read-only provider health tool that
  # validates the selected catalog and key/endpoint relationship; never infer
  # it from a listener alone.
  $raw = & $python $providerHealth "--json"
  if ($LASTEXITCODE -ne 0) {
    throw "Cockpit provider health failed with exit code $LASTEXITCODE"
  }
  try {
    return (($raw -join "`n") | ConvertFrom-Json)
  } catch {
    throw "Cockpit provider health returned invalid JSON"
  }
}

Write-Output "WORKFLOW_MODE=$Mode"
Write-Output "EVIDENCE_ORDER=repo_verified,filesystem_projected,host_loaded,controlled_live_replay,natural_live_accepted"

if ($Mode -in @("RecoverAfterReset", "RestartAndRecover")) {
  $confirmationCount = [int]$QuotaResetConfirmed.IsPresent + [int]$CooldownResetConfirmed.IsPresent
  if ($confirmationCount -ne 1) {
    throw "$Mode requires exactly one external recovery confirmation: -QuotaResetConfirmed or -CooldownResetConfirmed."
  }
  if ($SkipRemote -or $ApplyRemote) {
    throw "$Mode requires BWG access and does not accept -ApplyRemote."
  }
  if ($Mode -eq "RestartAndRecover") {
    # CPA runtime cooldown lives only in process memory (save-cooldown-status
    # defaults to false and is unset on bwg), so a container restart clears the
    # CPA-side cooldown without needing the bcrypt-hashed management key; the
    # probe stage below then clears the independent admission lane. Restarting
    # briefly interrupts the other lanes, so the preflight refuses when any
    # chatgpt-oauth request is in flight and leaves the container untouched.
    $restartRemote = @'
python3 - <<'PY'
import json
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:8318/healthz", timeout=5) as response:
    state = json.load(response)["lanes"]["chatgpt-oauth"]["state"]
print(
    "PREFLIGHT inflight=%d pending=%d half_open=%s cooldown_remaining=%s"
    % (state["inflight"], state["pending"], state["half_open_probe"], state["cooldown_remaining"])
)
if state["inflight"] or state["pending"] or state["half_open_probe"]:
    print("RESTART_RESULT=LANE_BUSY")
    raise SystemExit(14)
PY
preflight_rc=$?
if [ "$preflight_rc" -ne 0 ]; then
  exit "$preflight_rc"
fi
docker restart cli-proxy-api >/dev/null
status=unknown
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  status=$(docker inspect -f '{{.State.Status}}' cli-proxy-api 2>/dev/null || echo unknown)
  if [ "$status" = "running" ]; then
    break
  fi
  sleep 0.5
done
if [ "$status" != "running" ]; then
  echo "RESTART_RESULT=CONTAINER_NOT_RUNNING status=$status"
  exit 15
fi
sleep 4
echo RESTART_RESULT=OK
'@
    & (Join-Path $repoRoot "connect.ps1") -Profile bwg -StrictHostKeyChecking `
      -Command $restartRemote -CommandTimeout 90 -CommandHardTimeout 110
    if ($LASTEXITCODE -ne 0) {
      throw "RestartAndRecover preflight/restart failed with exit code $LASTEXITCODE (container left untouched on LANE_BUSY)."
    }
    Write-Output "CPA_RESTART_STAGE=COMPLETE"
  }
  $remote = @'
python3 - '__RECOVERY_MODEL__' <<'PY'
import datetime
import importlib.util
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location(
    "admission_recovery", "/opt/cliproxyapi/cpa-admission.py"
)
admission = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = admission
spec.loader.exec_module(admission)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def state():
    with opener.open("http://127.0.0.1:8318/healthz", timeout=5) as response:
        return json.load(response)["lanes"]["chatgpt-oauth"]["state"]

try:
    before = state()
    print("RECOVERY_UTC=" + datetime.datetime.now(datetime.timezone.utc).isoformat())
    print("COOLDOWN_BEFORE=" + str(before["cooldown_remaining"]))
    if before["inflight"] or before["pending"] or before["half_open_probe"]:
        print("RECOVERY_RESULT=LANE_BUSY")
        raise SystemExit(14)
    if not before.get("cooldown_active", before["cooldown_remaining"]):
        print("RECOVERY_RESULT=NOT_REQUIRED")
        raise SystemExit(0)
    generation = before.get("failure_generation")
    if type(generation) is not int:
        print("RECOVERY_RESULT=RUNTIME_UPGRADE_REQUIRED")
        raise SystemExit(20)
    config = yaml.safe_load(Path("/opt/cliproxyapi/config.yaml").read_text())
    # The v8 schema moved the client key read side to access.api-keys; the
    # top-level api-keys remnant is a mapping there, so index only a list.
    access = config.get("access") if isinstance(config, dict) else None
    api_keys = access.get("api-keys") if isinstance(access, dict) else None
    if not (
        isinstance(api_keys, list)
        and api_keys
        and isinstance(api_keys[0], str)
        and api_keys[0]
    ):
        print("RECOVERY_RESULT=UNVERIFIED class=config_schema")
        raise SystemExit(10)
    payload = {
        "model": sys.argv[1],
        "reason": "__RECOVERY_REASON__",
        "expected_generation": generation,
    }
    request = urllib.request.Request(
        "http://127.0.0.1:8318/admin/recover-after-reset",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + api_keys[0],
            "Content-Type": "application/json",
        },
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=120) as response:
            status, body = response.status, response.read(262145)
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read(262145)
    print("RECOVERY_HTTP_STATUS=" + str(status))
    print("RECOVERY_SECONDS=" + str(round(time.monotonic() - started, 3)))
    completed = status == 200 and admission.completed_reset_probe(body)
    deadline = time.monotonic() + 2
    after = state()
    while after["half_open_probe"] and time.monotonic() < deadline:
        time.sleep(0.05)
        after = state()
    print("COMPLETED_WITH_EXPECTED_OUTPUT=" + str(completed).lower())
    print("COOLDOWN_AFTER=" + str(after["cooldown_remaining"]))
    recovered = (
        completed
        and not after["cooldown_remaining"]
        and not after["failure_streak"]
        and not after["half_open_probe"]
    )
    print("RECOVERY_RESULT=" + ("RECOVERED" if recovered else "NOT_RECOVERED"))
    raise SystemExit(0 if recovered else 10)
except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
    print("RECOVERY_RESULT=UNVERIFIED class=" + type(error).__name__)
    raise SystemExit(10)
PY
'@
  $recoveryReason = if ($CooldownResetConfirmed) { "cooldown_reset" } else { "quota_reset" }
  $remote = $remote.Replace("__RECOVERY_MODEL__", $RecoveryModel)
  $remote = $remote.Replace("__RECOVERY_REASON__", $recoveryReason)
  & (Join-Path $repoRoot "connect.ps1") -Profile bwg -StrictHostKeyChecking `
    -Command $remote -CommandTimeout 140 -CommandHardTimeout 160
  exit $LASTEXITCODE
}

if ($Mode -eq "Triage") {
  Invoke-Triage
  exit 0
}

if ($Mode -in @("Audit", "Project", "Verify")) {
  if ($Mode -eq "Project") {
    if ([string]::IsNullOrWhiteSpace($SidecarCandidatePath) -and -not $ApplyRemote) {
      throw "Project requires -SidecarCandidatePath and/or -ApplyRemote."
    }
    if (-not [string]::IsNullOrWhiteSpace($SidecarCandidatePath)) {
      Invoke-Checked "pwsh" @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $sidecar,
        "-Mode", "Project", "-CandidatePath", $SidecarCandidatePath
      )
    }
  } else {
    $provider = Get-ProviderHealth
    if ($null -eq $provider -or [string]::IsNullOrWhiteSpace([string]$provider.configTarget)) {
      throw "Cockpit provider health did not expose configTarget"
    }
    $targetUri = $null
    try {
      $targetUri = [Uri]$provider.configTarget
    } catch {
      throw "Cockpit provider health did not expose a valid configTarget"
    }
    if ($targetUri.Host -eq "fq.sciman.top") {
      Write-Output "COCKPIT_GATEWAY_MODE=public_gateway"
      Write-Output "COCKPIT_PROVIDER_VERIFY=PASS"
      Write-Output "COCKPIT_SIDECAR_VERIFY=SKIPPED_PUBLIC_GATEWAY"
      Write-Output "COCKPIT_SIDECAR_RULE=10909/14185 are optional in public_gateway mode"
    } elseif ($targetUri.Host -in @("127.0.0.1", "localhost", "::1")) {
      Write-Output "COCKPIT_GATEWAY_MODE=local_gateway"
      Invoke-Checked "pwsh" @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $sidecar,
        "-Mode", $Mode
      )
    } elseif ([string]::IsNullOrWhiteSpace($targetUri.Host)) {
      throw "Unsupported Cockpit provider target host: $($provider.configTarget)"
    } else {
      # The desktop may point at any third-party OpenAI-compatible provider.
      # cockpit_provider_health.py records that shape as `other` and warns
      # instead of failing, so the CPA sidecar contract simply does not apply:
      # 10909/14185 carry no meaning on this path, and failing here would turn a
      # valid configuration into a permanent false alarm.
      Write-Output "COCKPIT_GATEWAY_MODE=other_provider"
      Write-Output "COCKPIT_PROVIDER_VERIFY=RECORDED"
      Write-Output "COCKPIT_SIDECAR_VERIFY=SKIPPED_OTHER_PROVIDER"
      Write-Output "COCKPIT_SIDECAR_RULE=Desktop targets $($targetUri.Host); the CPA 10909/14185 sidecar contract is not on this path"
    }
  }

  if (-not $SkipRemote) {
    Invoke-Doctor -Apply:$ApplyRemote
  }
  Invoke-Triage
  # Project-mode reload signalling is owned by cockpit_sidecar_guardrails.ps1,
  # which knows whether it actually changed a file; a duplicate unconditional
  # RELOAD_REQUIRED=1 here would keep crying wolf after a no-op projection.
  exit 0
}

if ($Mode -eq "ControlledReplay") {
  Invoke-Checked $python @($replay)
  exit 0
}

if ($Mode -eq "WaitCapSimulation") {
  # This uses a loopback stub and scratch sidecar only.  It never reaches CPA
  # or an OAuth provider and proves the local 45 s rejection cap behaviour.
  $waitArgs = @($waitCap, "--expect-cap-s", "45")
  if (-not [string]::IsNullOrWhiteSpace($SidecarCandidatePath)) {
    $candidate = (Resolve-Path -LiteralPath $SidecarCandidatePath -ErrorAction Stop).Path
    $waitArgs += @("--candidate", $candidate)
  }
  Invoke-Checked $python $waitArgs
  exit 0
}

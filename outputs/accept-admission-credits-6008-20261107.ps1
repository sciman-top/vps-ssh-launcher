#requires -Version 7
# Controlled acceptance for the deployed WorkBuddy "Credits exhausted" (6008)
# fix. Runs the REAL admission module already on the host against synthetic
# 6008 bodies and asserts the classification and the narrowed scope. No
# upstream request is made and no lane state is modified: this is a pure
# in-process proof of the deployed generation.
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
python3 - <<'PY'
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

LIVE = "/opt/cliproxyapi/cpa-admission.py"
spec = importlib.util.spec_from_file_location("deployed_admission", LIVE)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

print("== classification ==")
cases = {
    "wrapped": b'upstream 429: {"code":6008,"msg":"Credits exhausted. Please visit the link"}',
    "bare": b'{"code":6008,"msg":"Credits exhausted. Please visit the link"}',
    "envelope": b'{"error":{"code":6008,"message":"Credits exhausted"}}',
}
for name, body in cases.items():
    hold, delay = module.oauth_risk_signal("workbuddy-oauth", 429, body)
    assert hold == "account_credits_exhausted", (name, hold)
    assert delay is None, (name, delay)
    print("   %-10s -> %s" % (name, hold))

print("== neighbours unchanged ==")
for name, body, expect in [
    ("6004", b'upstream 429: {"code":6004,"msg":"model rate limit"}', "6004"),
    ("14003", b'upstream 429: {"code":14003,"msg":"Model busy"}', "14003"),
    ("6003", b'upstream 429: {"code":6003,"msg":"rate limited"}', "6003"),
]:
    hold, _ = module.oauth_risk_signal("workbuddy-oauth", 429, body)
    assert hold != "account_credits_exhausted", (name, hold)
    print("   %-6s -> %s (not billing)" % (name, hold))

print("== narrowed scope (credits hold) ==")
with tempfile.TemporaryDirectory() as td:
    state = Path(td) / "workbuddy-risk.json"
    state.write_text(
        json.dumps({"version": 1, "not_before": 0,
                    "hold_reason": "account_credits_exhausted"}),
        encoding="utf-8",
    )
    lane = module.LaneState({
        "name": "workbuddy-oauth", "max_inflight": 2, "max_pending": 2,
        "queue_timeout_seconds": 1, "cooldown_schedule_seconds": [60, 120],
        "retry_after_max_seconds": 86400, "heartbeat_interval_seconds": 15,
    }, state_path=state)
    for model, expect in [
        ("hy4free", True),
        ("deepseek-v4.1-flash", False),
        ("deepseek-v4.1-flash-sg", False),
    ]:
        lease = lane.acquire(alive=None, model=model)
        assert lease.admitted == expect, (model, lease.admitted, lease.reason)
        print("   %-24s admitted=%s" % (model, lease.admitted))
        if lease.admitted:
            lane.release(lease, capacity_error=False, retry_after=None,
                         successful=True)

    print("== other holds stay lane-wide ==")
    state2 = Path(td) / "workbuddy-risk2.json"
    state2.write_text(
        json.dumps({"version": 1, "not_before": 0,
                    "hold_reason": "account_restricted"}),
        encoding="utf-8",
    )
    lane2 = module.LaneState({
        "name": "workbuddy-oauth", "max_inflight": 2, "max_pending": 2,
        "queue_timeout_seconds": 1, "cooldown_schedule_seconds": [60, 120],
        "retry_after_max_seconds": 86400, "heartbeat_interval_seconds": 15,
    }, state_path=state2)
    for model in ("hy4free", "deepseek-v4.1-flash"):
        lease = lane2.acquire(alive=None, model=model)
        assert not lease.admitted, (model, lease.reason)
        print("   %-24s admitted=%s (expected False)" % (model, lease.admitted))

assert "hy4free" in module.ZERO_COST_MODELS
assert module.WORKBUDDY_6004_MAX_COOLDOWN_SECONDS == 300
print("CONTROLLED_ACCEPTANCE=PASS")
PY
'@

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300 -RemoteTempPrefix "credits-6008-acceptance"

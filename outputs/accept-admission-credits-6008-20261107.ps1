#requires -Version 7
# Controlled acceptance for the deployed WorkBuddy 6008 credits fix.
#
# Runs the REAL admission module already on the host. Two layers:
#   1. classification + scope, in-process (no I/O, no state change);
#   2. cooldown bypass -- the same traffic a credits refusal arms must still
#      admit the zero-cost model, while the paid models stay refused.
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

def make_lane(td, hold):
    state = Path(td) / "workbuddy-risk.json"
    state.write_text(json.dumps({"version": 1, "not_before": 0,
                                 "hold_reason": hold}), encoding="utf-8")
    return module.LaneState({
        "name": "workbuddy-oauth", "max_inflight": 2, "max_pending": 2,
        "queue_timeout_seconds": 1, "cooldown_schedule_seconds": [60, 120],
        "retry_after_max_seconds": 86400, "heartbeat_interval_seconds": 15,
        # Read the deployed lane config so this assertion tracks the real
        # zero-cost list instead of a copy that can drift from it.
        "zero_cost_models": next(
            item for item in module.load_config(
                Path("/opt/cliproxyapi/cpa-admission.json")
            )["lanes"] if item["name"] == "workbuddy-oauth"
        )["zero_cost_models"],
    }, state_path=state)

print("== classification ==")
for name, body in {
    "wrapped": b'upstream 429: {"code":6008,"msg":"Credits exhausted"}',
    "bare": b'{"code":6008,"msg":"Credits exhausted"}',
    "envelope": b'{"error":{"code":6008,"message":"Credits exhausted"}}',
}.items():
    hold, delay = module.oauth_risk_signal("workbuddy-oauth", 429, body)
    assert hold == "account_credits_exhausted", (name, hold)
    assert delay is None
    print("   %-10s -> %s" % (name, hold))

print("== neighbours keep their meaning ==")
for name, body in {
    "6004": b'upstream 429: {"code":6004,"msg":"model rate limit"}',
    "14003": b'upstream 429: {"code":14003,"msg":"Model busy"}',
}.items():
    hold, _ = module.oauth_risk_signal("workbuddy-oauth", 429, body)
    assert hold != "account_credits_exhausted", (name, hold)
    print("   %-6s -> %s (not billing)" % (name, hold))

print("== credits hold: free model admitted past hold AND cooldown ==")
with tempfile.TemporaryDirectory() as td:
    lane = make_lane(td, "account_credits_exhausted")
    # Arm the cooldown that the same refusal sets, then verify the free model
    # still gets in. Without the second exemption this returns "cooldown".
    lane.record_risk(None, 60)
    for model, expect in [
        ("hy4free", True),
        ("deepseek-v4.1-flash", False),
        ("deepseek-v4.1-flash-sg", False),
    ]:
        lease = lane.acquire(alive=None, model=model)
        assert lease.admitted == expect, (model, lease.admitted, lease.reason)
        print("   %-24s admitted=%-5s reason=%s" % (model, lease.admitted, lease.reason))
        if lease.admitted:
            lane.release(lease, capacity_error=False, retry_after=None,
                         successful=True)

print("== other holds stay lane-wide (incl. the free model) ==")
with tempfile.TemporaryDirectory() as td:
    lane = make_lane(td, "account_restricted")
    for model in ("hy4free", "deepseek-v4.1-flash"):
        lease = lane.acquire(alive=None, model=model)
        assert not lease.admitted, (model, lease.reason)
        print("   %-24s admitted=False (expected)" % model)

cfg = module.load_config(Path("/opt/cliproxyapi/cpa-admission.json"))
assert "hy4free" in next(
    l for l in cfg["lanes"] if l["name"] == "workbuddy-oauth"
)["zero_cost_models"]
assert module.WORKBUDDY_6004_MAX_COOLDOWN_SECONDS == 300
print("CONTROLLED_ACCEPTANCE=PASS")
PY
'@

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 300 -RemoteTempPrefix "credits-6008-acceptance"

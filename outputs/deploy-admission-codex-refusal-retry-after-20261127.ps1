#requires -Version 7
# Reviewed admission generation (2026-11-27, refusal retry advice):
# 1. A streaming refusal is written into an already-committed 200, so its
#    retry window can only travel inside the event. Codex reads that window
#    from `error.headers["retry-after"]` and nowhere else
#    (codex-rs `sse/responses_error.rs`); the bare `retry_after` leaf alone
#    made the client treat the refusal as an ordinary stream error, spend both
#    stream retries against the closed window within two seconds and report a
#    dead turn (10:30:47Z: `retry after 317s`, reconnects at +1.4s/+1.7s).
#    Cooldown and model-cooldown refusals now repeat the window in that header
#    map; windows over CLIENT_RETRY_ADVICE_MAX_SECONDS (600s) and every other
#    refusal keep the old shape and fail fast.
# 2. The model-scoped refusal names its own lane instead of "Antigravity".
# 3. A refused upstream connection on a streaming request no longer raises
#    UnboundLocalError and severs an empty stream; the client receives the
#    intended `upstream_transport_error` terminal event.
# 4. A client that spells the header `content-length` left two disagreeing
#    Content-Length headers on the upstream request once the body was
#    rewritten, and the upstream waited for bytes that never arrived. The
#    client's own length is now dropped by case-folded name.
# Atomically swaps cpa-admission.py, the integrity pin, and the request guard
# rendered from it (the config is re-staged unchanged) under maintenance +
# request-drain locks with backup-first rollback. Run
# cpa_bwg_guardrails.ps1 -Profile bwg -Apply AFTER this.
param(
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$expectedOldPin = "769b3b6d087c80ca1ae606107f2daf03747c687e0a51012f34906ba97ad7f343"
$reviewedNewPin = "2bc34f739f60570fb9551ad59a3b96c22d5425b6063bacd520819ea9b8eb5267"
$expectedOldConfigSha = "0e976f752571cf510e73ebd5b95d60001173ca9abd055bc9cd5ec6e40a44acb8"
$reviewedNewConfigSha = "0e976f752571cf510e73ebd5b95d60001173ca9abd055bc9cd5ec6e40a44acb8"
$expectedRoutesSha = "43faf4b9b76a63312bf7b4bd6a9781571bb608a078f37f237aff7ca756d33b62"
$expectedBootstrapSha = "24254069d3fa07ed4107b526372fa9c39bc45628de0f81161c848490af20d24d"

foreach ($rel in @(
    "scripts/remote/cpa-admission.py",
    "scripts/remote/cpa-admission.json",
    "scripts/remote/cpa_request_guard.py",
    "scripts/remote/cpa-admission-integrity-pin.txt"
  )) {
  $st = @(git -C $repoRoot status --porcelain=v1 -- $rel 2>&1)
  if ($LASTEXITCODE -ne 0) { throw "git status failed for $rel" }
  if ($st.Count -gt 0) { throw "Source is dirty; commit before reviewed deploy: $rel" }
}

function Read-LfBytes([string]$Relative) {
  $path = Join-Path $repoRoot ($Relative -replace "/", "\")
  $text = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($path)).
    Replace("`r`n", "`n").Replace("`r", "`n")
  return [Text.Encoding]::UTF8.GetBytes($text)
}

$admissionBytes = Read-LfBytes "scripts/remote/cpa-admission.py"
$newPin = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($admissionBytes)
).ToLowerInvariant()
if ($newPin -ne $reviewedNewPin) { throw "Admission candidate sha drift: $newPin" }
$pinText = (Get-Content -LiteralPath (
    Join-Path $repoRoot "scripts/remote/cpa-admission-integrity-pin.txt"
  ) -Raw).Trim()
if ($pinText -ne $newPin) { throw "Repo integrity pin mismatch: $pinText" }

$configBytes = Read-LfBytes "scripts/remote/cpa-admission.json"
$newConfigSha = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($configBytes)
).ToLowerInvariant()
if ($newConfigSha -ne $reviewedNewConfigSha) { throw "Admission config sha drift: $newConfigSha" }

$guardTemplate = [Text.UTF8Encoding]::new($false, $true).GetString(
  (Read-LfBytes "scripts/remote/cpa_request_guard.py")
)
if (-not $guardTemplate.Contains("__SOURCE_SHA256__")) {
  throw "Guard template missing placeholder"
}
$guardBytes = [Text.Encoding]::UTF8.GetBytes(
  $guardTemplate.Replace("__SOURCE_SHA256__", $newPin)
)
$guardSha = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($guardBytes)
).ToLowerInvariant()

$mode = if ($DryRun) { "dry-run" } else { "apply" }
$admissionB64 = [Convert]::ToBase64String($admissionBytes)
$configB64 = [Convert]::ToBase64String($configBytes)
$guardB64 = [Convert]::ToBase64String($guardBytes)

$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
LIVE=$DIR/cpa-admission.py
CONFIG=$DIR/cpa-admission.json
ROUTES=$DIR/cpa_provider_routes.json
BOOTSTRAP=/usr/local/libexec/cpa_admission_bootstrap.py
GUARD=/usr/local/libexec/cpa_request_guard.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
STAGED_LIVE=$DIR/.cpa-admission.py.new-refusaladvice.$$
STAGED_CONFIG=$DIR/.cpa-admission.json.new-refusaladvice.$$
STAGED_GUARD=/usr/local/libexec/.cpa_request_guard.py.new-refusaladvice.$$
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
OLD_CONFIG_SHA=__OLD_CONFIG_SHA__
NEW_CONFIG_SHA=__NEW_CONFIG_SHA__
ROUTES_SHA=__ROUTES_SHA__
BOOTSTRAP_SHA=__BOOTSTRAP_SHA__
GUARD_SHA=__GUARD_SHA__
MODE=__MODE__
BACKUP_PARENT=/opt/cliproxyapi/backups
TRAFFIC=/run/cpa-admission/requests.lock
MUTATED=0

cleanup_stage() {
  rm -f -- "$STAGED_LIVE" "$STAGED_CONFIG" "$STAGED_GUARD" "$PIN.new" 2>/dev/null || true
}
trap cleanup_stage EXIT

printf '%s' '__ADMISSION_B64__' | base64 -d >"$STAGED_LIVE"
printf '%s' '__CONFIG_B64__' | base64 -d >"$STAGED_CONFIG"
printf '%s' '__GUARD_B64__' | base64 -d >"$STAGED_GUARD"
chmod --reference="$LIVE" "$STAGED_LIVE"
chown --reference="$LIVE" "$STAGED_LIVE"
chmod --reference="$CONFIG" "$STAGED_CONFIG"
chown --reference="$CONFIG" "$STAGED_CONFIG"
chmod --reference="$GUARD" "$STAGED_GUARD"
chown --reference="$GUARD" "$STAGED_GUARD"

[ "$(sha256sum "$STAGED_LIVE" | awk '{print $1}')" = "$NEW_PIN" ] || { echo "REFUSE staged_admission_sha_mismatch"; exit 20; }
[ "$(sha256sum "$STAGED_CONFIG" | awk '{print $1}')" = "$NEW_CONFIG_SHA" ] || { echo "REFUSE staged_config_sha_mismatch"; exit 20; }
[ "$(sha256sum "$STAGED_GUARD" | awk '{print $1}')" = "$GUARD_SHA" ] || { echo "REFUSE staged_guard_sha_mismatch"; exit 20; }
grep -q "SOURCE_SHA256 = \"$NEW_PIN\"" "$STAGED_GUARD" || { echo "REFUSE staged_guard_generation_mismatch"; exit 20; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo "LOCK_BUSY"; exit 75; }

check_idle() {
  python3 - <<'PY'
import json
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:8318/healthz", timeout=5) as response:
    health = json.load(response)
assert health.get("status") == "ok", "REFUSE healthz_not_ok"
for name, lane in health.get("lanes", {}).items():
    state = lane.get("state", {})
    if state.get("inflight", 0) != 0 or state.get("pending", 0) != 0:
        raise SystemExit(
            f"REFUSE not_idle lane={name} inflight={state.get('inflight')} "
            f"pending={state.get('pending')}"
        )
print("ADMISSION_IDLE_OK")
PY
}

[ ! -e "$PIN.new" ] && [ ! -e "$DIR/.cpa-admission.py.new" ] || { echo "REFUSE pending_generation"; exit 20; }
current_live="$(sha256sum "$LIVE" | awk '{print $1}')"
current_pin="$(tr -d '\r\n' <"$PIN")"
[ "$current_live" = "$OLD_PIN" ] || { echo "REFUSE live_baseline_drift got=$current_live want=$OLD_PIN"; exit 20; }
[ "$current_pin" = "$OLD_PIN" ] || { echo "REFUSE pin_baseline_drift got=$current_pin want=$OLD_PIN"; exit 20; }
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$OLD_CONFIG_SHA" ] || { echo "REFUSE config_baseline_drift"; exit 20; }
[ "$(sha256sum "$ROUTES" | awk '{print $1}')" = "$ROUTES_SHA" ] || { echo "REFUSE routes_baseline_drift"; exit 20; }
[ "$(sha256sum "$BOOTSTRAP" | awk '{print $1}')" = "$BOOTSTRAP_SHA" ] || { echo "REFUSE bootstrap_baseline_drift"; exit 20; }
grep -q "SOURCE_SHA256 = \"$OLD_PIN\"" "$GUARD" || { echo "REFUSE guard_baseline_drift"; exit 20; }

runuser -u nobody -- python3 - "$STAGED_LIVE" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"), sys.argv[1], "exec")
PY
runuser -u nobody -- python3 - "$STAGED_CONFIG" "$STAGED_LIVE" "$ROUTES" <<'PY'
import importlib.machinery, importlib.util, io, json, pathlib, sys, types
loader = importlib.machinery.SourceFileLoader("staged_admission", sys.argv[2])
spec = importlib.util.spec_from_loader("staged_admission", loader)
module = importlib.util.module_from_spec(spec)
sys.modules["staged_admission"] = module
loader.exec_module(module)
cfg = module.load_config(pathlib.Path(sys.argv[1]), route_manifest_path=pathlib.Path(sys.argv[3]))
chatgpt_lane = next(lane for lane in cfg["lanes"] if lane["name"] == "chatgpt-oauth")
assert "server_is_overloaded" in chatgpt_lane["model_capacity_markers"], chatgpt_lane["model_capacity_markers"]
assert module.CLIENT_RETRY_ADVICE_MAX_SECONDS == 600

def refusal(retry_after, server_directed):
    sink = io.BytesIO()
    handler = types.SimpleNamespace(path="/v1/responses", _request_id="staged", wfile=sink)
    module.Handler._send_stream_failure(
        handler,
        code="model_cooldown",
        message="Shared upstream lane chatgpt-oauth model gpt-6-luna is cooling down; "
        "retry after 317s. Sibling models on the lane remain available.",
        retry_after=retry_after,
        server_directed=server_directed,
    )
    data = next(line[6:] for line in sink.getvalue().splitlines() if line.startswith(b"data: "))
    return json.loads(data)["response"]["error"]

window = refusal(317, True)
assert window["headers"] == {"retry-after": "317"} and window["retry_after"] == 317, window
assert "headers" not in refusal(60, False), "a heuristic value must keep the bare leaf"
quota = refusal(3600, True)
assert "headers" not in quota and quota["retry_after"] == 3600, quota
print("STAGED_RULE_OK window_header_ok heuristic_leaf_ok long_window_leaf_ok")
PY

if [ "$MODE" = "dry-run" ]; then
  runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
  echo "DRY_RUN_OK baseline=$OLD_PIN candidate=$NEW_PIN old_config=$OLD_CONFIG_SHA new_config=$NEW_CONFIG_SHA no_swap=true"
  exit 0
fi

echo "PREFLIGHT_OK"
exec 8<"$TRAFFIC"
flock -w 120 -x 8 || { echo "REFUSE drain_timeout"; exit 75; }
echo "REQUEST_GUARD_EXCLUSIVE_OK"
check_idle

BACKUP=$BACKUP_PARENT/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-codex-refusal-retry-after-20261127
mkdir -p "$BACKUP_PARENT"
mkdir -m 700 "$BACKUP"
cp -a "$LIVE" "$CONFIG" "$GUARD" "$PIN" "$BACKUP/"
echo "BACKUP_READY path=$BACKUP"

rollback() {
  local rc=${1:-$?} failed=0
  trap - EXIT HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    rm -f -- "$PIN.new" || failed=1
    cp -a "$BACKUP/cpa-admission.py" "$LIVE" || failed=1
    cp -a "$BACKUP/cpa-admission.json" "$CONFIG" || failed=1
    cp -a "$BACKUP/cpa_request_guard.py" "$GUARD" || failed=1
    cp -a "$BACKUP/cpa-admission.sha256" "$PIN" || failed=1
    systemctl restart cpa-admission || failed=1
    sleep 2
    runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check || failed=1
    if [ "$failed" = 0 ]; then
      echo "ROLLBACK_VERIFIED backup=$BACKUP"
    else
      echo "ROLLBACK_FAILED backup=$BACKUP"
    fi
  fi
  exec 8<&- 2>/dev/null || true
  if [ "$rc" = 0 ]; then rc=1; fi
  exit "$rc"
}
trap rollback EXIT HUP INT TERM

MUTATED=1
mv -f "$STAGED_LIVE" "$LIVE"
mv -f "$STAGED_CONFIG" "$CONFIG"
mv -f "$STAGED_GUARD" "$GUARD"
printf "%s\n" "$NEW_PIN" >"$PIN.new"
chmod 644 "$PIN.new"
chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN"
echo "ADMISSION_PIN_ROTATED old=$OLD_PIN new=$NEW_PIN config=$NEW_CONFIG_SHA"
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl restart cpa-admission

deadline=$((SECONDS + 30))
while :; do
  if check_idle >/dev/null 2>&1; then break; fi
  if (( SECONDS >= deadline )); then
    echo "REFUSE readiness_timeout"
    rollback 20
  fi
  sleep 1
done
[ "$(sha256sum "$LIVE" | awk '{print $1}')" = "$NEW_PIN" ]
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$NEW_CONFIG_SHA" ]
[ "$(tr -d '\r\n' <"$PIN")" = "$NEW_PIN" ]
grep -q "SOURCE_SHA256 = \"$NEW_PIN\"" "$GUARD"
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl is-active --quiet cpa-admission
echo "ADMISSION_INTEGRITY_OK"

MUTATED=0
trap - EXIT HUP INT TERM
exec 8<&- 2>/dev/null || true
echo "DEPLOY_OK pin=$NEW_PIN config=$NEW_CONFIG_SHA backup=$BACKUP"
'@

$remote = $remote.
  Replace("__OLD_PIN__", $expectedOldPin).
  Replace("__NEW_PIN__", $newPin).
  Replace("__OLD_CONFIG_SHA__", $expectedOldConfigSha).
  Replace("__NEW_CONFIG_SHA__", $newConfigSha).
  Replace("__ROUTES_SHA__", $expectedRoutesSha).
  Replace("__BOOTSTRAP_SHA__", $expectedBootstrapSha).
  Replace("__GUARD_SHA__", $guardSha).
  Replace("__MODE__", $mode).
  Replace("__ADMISSION_B64__", $admissionB64).
  Replace("__CONFIG_B64__", $configB64).
  Replace("__GUARD_B64__", $guardB64)
if ($remote -cmatch "__[A-Z0-9_]+__") { throw "Unfilled placeholder." }

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config `
  -Profile "bwg" -Command $remote -IdleTimeoutSeconds 300 `
  -HardTimeoutSeconds 900 -RemoteTempPrefix "admission-codex-refusal-retry-after-20261127" -StreamStdin

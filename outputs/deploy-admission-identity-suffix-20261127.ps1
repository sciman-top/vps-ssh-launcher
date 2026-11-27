#requires -Version 7
# Reviewed admission generation (2026-11-27, commit c8c3f49): the Codex
# identity sentence rule now consumes suffixed model ids, so
# "You are Codex, based on GPT-5.6-terra." no longer leaves a dangling
# "-terra" after the replacement. Only cpa-admission.py changes; the config,
# routes and bootstrap are byte-identical and are pinned here as baselines.
# Atomically swaps cpa-admission.py and the integrity pin (and the request
# guard rendered from it) under maintenance + request-drain locks with
# backup-first rollback. A bulk -Apply runs AFTER this transaction.
param(
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$expectedOldPin = "261a95eba2c31a34dcc1671d318a8ddf806ff763087ce9a02dfab9a16872c35d"
$reviewedNewPin = "3a9df1d3d9c205c943d769bbdce75e08f5f1e7d854c0f39bdee5c1bc1a0c10ea"
$expectedOldConfigSha = "ca018e92af69edb3c02600268ecee7f6d13a08082685b3245bbf13857b8fe0b9"
$reviewedNewConfigSha = "ca018e92af69edb3c02600268ecee7f6d13a08082685b3245bbf13857b8fe0b9"
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
if ($newConfigSha -ne $expectedOldConfigSha) { throw "Admission config must be unchanged: $newConfigSha" }

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
STAGED_LIVE=$DIR/.cpa-admission.py.new-identitysuffix.$$
STAGED_CONFIG=$DIR/.cpa-admission.json.new-identitysuffix.$$
STAGED_GUARD=/usr/local/libexec/.cpa_request_guard.py.new-identitysuffix.$$
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
import importlib.machinery, importlib.util, pathlib, sys
loader = importlib.machinery.SourceFileLoader("staged_admission", sys.argv[2])
spec = importlib.util.spec_from_loader("staged_admission", loader)
module = importlib.util.module_from_spec(spec)
sys.modules["staged_admission"] = module
loader.exec_module(module)
cfg = module.load_config(pathlib.Path(sys.argv[1]), route_manifest_path=pathlib.Path(sys.argv[3]))
neutralize = module.neutralize_agent_identity_sentences
assert neutralize("You are Codex, based on GPT-5.6-terra. You run.") == (
    "You are a coding agent. You run."
), neutralize("You are Codex, based on GPT-5.6-terra. You run.")
assert neutralize("You are Codex, based on GPT-5. You are running.") == (
    "You are a coding agent. You are running."
), "official template sentence regressed"
unchanged = "Read ~/.zcode/skills/a.md; GPT-5, OpenAI and the Codex CLI"
assert neutralize(unchanged) == unchanged, "lossy rewrite"
print("STAGED_RULE_OK identity_suffix_consumed official_shape_intact")
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

BACKUP=$BACKUP_PARENT/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-identity-suffix-20261127
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
  -HardTimeoutSeconds 900 -RemoteTempPrefix "admission-identity-suffix-20261127" -StreamStdin

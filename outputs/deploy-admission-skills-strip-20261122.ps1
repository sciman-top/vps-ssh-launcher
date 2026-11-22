#requires -Version 7
# Reviewed Antigravity standalone skills_instructions strip generation swap (2026-11-22).
# Atomically swaps cpa-admission.py, cpa-admission.json, cpa_request_guard.py,
# and cpa-admission.sha256 under maintenance + request-drain locks with
# backup-first rollback.
param(
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$expectedOldPin = "f70b874251a6f3f6eb44b83bca46818984095fac272e6b84963344b935356d29"
$expectedOldConfigSha = "77b5f465e0e9139405e190f868c38229e4f9f378f28d4aac58a075d9c1d19ff7"
$expectedRoutesSha = "5b7216965d69444ae38b57f73afafbae0d7435252967cf9a8df86029daa72ae2"
$expectedBootstrapSha = "00f4b7d78384441cf8b42ca89e44e31af26875f9bf5ea8aaffe2f717de08aac4"
$reviewedNewPin = "2822eb0a5ed5a50d9efef0cf2572cc7fe2cb505e1bdce87eef9853a81e8f3a2e"
$reviewedNewConfigSha = "77b5f465e0e9139405e190f868c38229e4f9f378f28d4aac58a075d9c1d19ff7"

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
  $p = Join-Path $repoRoot ($Relative -replace "/", "\")
  $t = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($p)).Replace("`r`n", "`n").Replace("`r", "`n")
  return [Text.Encoding]::UTF8.GetBytes($t)
}

$admissionBytes = Read-LfBytes "scripts/remote/cpa-admission.py"
$newPin = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($admissionBytes)).ToLowerInvariant()
if ($newPin -ne $reviewedNewPin) { throw "Admission candidate sha drift: $newPin" }
$pinText = (Get-Content -LiteralPath (Join-Path $repoRoot "scripts/remote/cpa-admission-integrity-pin.txt") -Raw).Trim()
if ($pinText -ne $newPin) { throw "Repo integrity pin mismatch: $pinText" }

$configBytes = Read-LfBytes "scripts/remote/cpa-admission.json"
$newConfigSha = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($configBytes)).ToLowerInvariant()
if ($newConfigSha -ne $reviewedNewConfigSha) { throw "Config candidate sha drift: $newConfigSha" }

$guardTemplate = [Text.UTF8Encoding]::new($false, $true).GetString((Read-LfBytes "scripts/remote/cpa_request_guard.py"))
if (-not $guardTemplate.Contains("__SOURCE_SHA256__")) { throw "Guard template missing placeholder" }
$guardBytes = [Text.Encoding]::UTF8.GetBytes($guardTemplate.Replace("__SOURCE_SHA256__", $newPin))
$newGuardSha = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($guardBytes)).ToLowerInvariant()

$admissionB64 = [Convert]::ToBase64String($admissionBytes)
$configB64 = [Convert]::ToBase64String($configBytes)
$guardB64 = [Convert]::ToBase64String($guardBytes)
$mode = if ($DryRun) { "dry-run" } else { "apply" }

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
STAGED_LIVE=$DIR/.cpa-admission.py.new-20261122ms.$$
STAGED_CONFIG=$DIR/.cpa-admission.json.new-20261122ms.$$
STAGED_GUARD=/usr/local/libexec/.cpa_request_guard.py.new-20261122ms.$$
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
OLD_CONFIG_SHA=__OLD_CONFIG_SHA__
NEW_CONFIG_SHA=__NEW_CONFIG_SHA__
ROUTES_SHA=__ROUTES_SHA__
BOOTSTRAP_SHA=__BOOTSTRAP_SHA__
NEW_GUARD_SHA=__NEW_GUARD_SHA__
MODE=__MODE__
BACKUP_PARENT=/opt/cliproxyapi/backups
BK=$BACKUP_PARENT/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-skills-strip-20261122
TRAFFIC=/run/cpa-admission/requests.lock
MUTATED=0

cleanup_stage() {
  rm -f -- "$STAGED_LIVE" "$STAGED_CONFIG" "$STAGED_GUARD" 2>/dev/null || true
}
trap cleanup_stage EXIT

printf '%s' '__ADMISSION_B64__' | base64 -d > "$STAGED_LIVE"
printf '%s' '__CONFIG_B64__' | base64 -d > "$STAGED_CONFIG"
printf '%s' '__GUARD_B64__' | base64 -d > "$STAGED_GUARD"
chmod --reference="$LIVE" "$STAGED_LIVE"
chown --reference="$LIVE" "$STAGED_LIVE"
chmod --reference="$CONFIG" "$STAGED_CONFIG"
chown --reference="$CONFIG" "$STAGED_CONFIG"
chmod --reference="$GUARD" "$STAGED_GUARD"
chown --reference="$GUARD" "$STAGED_GUARD"

[ "$(sha256sum "$STAGED_LIVE" | awk '{print $1}')" = "$NEW_PIN" ] || { echo 'REFUSE staged_admission_sha_mismatch'; exit 20; }
[ "$(sha256sum "$STAGED_CONFIG" | awk '{print $1}')" = "$NEW_CONFIG_SHA" ] || { echo 'REFUSE staged_config_sha_mismatch'; exit 20; }
[ "$(sha256sum "$STAGED_GUARD" | awk '{print $1}')" = "$NEW_GUARD_SHA" ] || { echo 'REFUSE staged_guard_sha_mismatch'; exit 20; }
grep -q "SOURCE_SHA256 = \"$NEW_PIN\"" "$STAGED_GUARD" || { echo 'REFUSE staged_guard_generation_mismatch'; exit 20; }
grep -Fq 'strip_antigravity_model_switch_items' "$STAGED_LIVE" || { echo 'REFUSE staged_model_switch_missing'; exit 20; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -w 60 -x 9 || { echo 'LOCK_BUSY'; exit 75; }

check_idle() {
  python3 - <<'PY'
import json, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8318/healthz', timeout=5) as r:
    h = json.load(r)
assert h.get('status') == 'ok', 'REFUSE healthz_not_ok'
for name, lane in h.get('lanes', {}).items():
    st = lane.get('state', {})
    if st.get('inflight', 0) != 0 or st.get('pending', 0) != 0:
        raise SystemExit(f'REFUSE not_idle lane={name} inflight={st.get("inflight")} pending={st.get("pending")}')
print('ADMISSION_IDLE_OK')
PY
}

[ ! -e "$PIN.new" ] && [ ! -e "$DIR/.cpa-admission.py.new" ] || { echo 'REFUSE pending_generation'; exit 20; }
cur_live="$(sha256sum "$LIVE" | awk '{print $1}')"
cur_pin="$(tr -d '\r\n' < "$PIN")"
[ "$cur_live" = "$OLD_PIN" ] || { echo "REFUSE live_baseline_drift got=$cur_live want=$OLD_PIN"; exit 20; }
[ "$cur_pin" = "$OLD_PIN" ] || { echo "REFUSE pin_baseline_drift got=$cur_pin want=$OLD_PIN"; exit 20; }
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$OLD_CONFIG_SHA" ] || { echo 'REFUSE config_baseline_drift'; exit 20; }
[ "$(sha256sum "$ROUTES" | awk '{print $1}')" = "$ROUTES_SHA" ] || { echo 'REFUSE routes_baseline_drift'; exit 20; }
[ "$(sha256sum "$BOOTSTRAP" | awk '{print $1}')" = "$BOOTSTRAP_SHA" ] || { echo 'REFUSE bootstrap_baseline_drift'; exit 20; }
grep -q "SOURCE_SHA256 = \"$OLD_PIN\"" "$GUARD" || { echo 'REFUSE guard_baseline_drift'; exit 20; }

runuser -u nobody -- python3 - "$STAGED_LIVE" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')
PY
runuser -u nobody -- python3 - "$STAGED_CONFIG" "$STAGED_LIVE" <<'PY'
import importlib.machinery, importlib.util, json, pathlib, sys
config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
loader = importlib.machinery.SourceFileLoader('staged_admission', sys.argv[2])
spec = importlib.util.spec_from_loader('staged_admission', loader)
module = importlib.util.module_from_spec(spec)
sys.modules['staged_admission'] = module
loader.exec_module(module)
module.load_config(pathlib.Path(sys.argv[1]))
assert hasattr(module, 'strip_antigravity_model_switch_items')
print('STAGED_CONFIG_LOAD_OK')
PY

if [ "$MODE" = 'dry-run' ]; then
  runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
  echo "DRY_RUN_OK baseline=$OLD_PIN candidate=$NEW_PIN config=$NEW_CONFIG_SHA no_swap=true"
  exit 0
fi

echo 'PREFLIGHT_OK'
exec 8<"$TRAFFIC"
flock -w 120 -x 8 || { echo 'REFUSE drain_timeout'; exit 75; }
echo 'REQUEST_GUARD_EXCLUSIVE_OK'
check_idle

mkdir -p "$BACKUP_PARENT"
mkdir -m 700 "$BK"
cp -a "$LIVE" "$CONFIG" "$GUARD" "$PIN" "$BK/"
echo "BACKUP_READY path=$BK"

rollback() {
  local rc=${1:-$?} failed=0
  trap - EXIT HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    rm -f -- "$PIN.new" || failed=1
    cp -a "$BK/cpa-admission.py" "$LIVE" || failed=1
    cp -a "$BK/cpa-admission.json" "$CONFIG" || failed=1
    cp -a "$BK/cpa_request_guard.py" "$GUARD" || failed=1
    cp -a "$BK/cpa-admission.sha256" "$PIN" || failed=1
    systemctl restart cpa-admission || failed=1
    sleep 2
    runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check || failed=1
    [ "$failed" = 0 ] && echo "ROLLBACK_VERIFIED backup=$BK" || echo "ROLLBACK_FAILED backup=$BK"
  fi
  exec 8<&- 2>/dev/null || true
  [ "$rc" != 0 ] || rc=1
  exit "$rc"
}
trap rollback EXIT HUP INT TERM

MUTATED=1
mv -f "$STAGED_LIVE" "$LIVE"
mv -f "$STAGED_CONFIG" "$CONFIG"
mv -f "$STAGED_GUARD" "$GUARD"
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"
chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN"
echo "ADMISSION_PIN_ROTATED old=$OLD_PIN new=$NEW_PIN config=$NEW_CONFIG_SHA"
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl restart cpa-admission

deadline=$((SECONDS + 30))
while :; do
  check_idle >/dev/null 2>&1 && break
  if (( SECONDS >= deadline )); then
    echo 'REFUSE readiness_timeout'
    rollback 20
  fi
  sleep 1
done
[ "$(sha256sum "$LIVE" | awk '{print $1}')" = "$NEW_PIN" ]
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$NEW_CONFIG_SHA" ]
[ "$(tr -d '\r\n' < "$PIN")" = "$NEW_PIN" ]
grep -q "SOURCE_SHA256 = \"$NEW_PIN\"" "$GUARD"
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl is-active --quiet cpa-admission
grep -Fq 'strip_antigravity_model_switch_items' "$LIVE"
echo 'ADMISSION_SKILLS_STRIP_LIVE_OK'

MUTATED=0
trap - EXIT HUP INT TERM
exec 8<&- 2>/dev/null || true
echo "DEPLOY_OK pin=$NEW_PIN config=$NEW_CONFIG_SHA backup=$BK"
'@
$remote = $remote.
  Replace("__OLD_PIN__", $expectedOldPin).
  Replace("__NEW_PIN__", $newPin).
  Replace("__OLD_CONFIG_SHA__", $expectedOldConfigSha).
  Replace("__NEW_CONFIG_SHA__", $newConfigSha).
  Replace("__ROUTES_SHA__", $expectedRoutesSha).
  Replace("__BOOTSTRAP_SHA__", $expectedBootstrapSha).
  Replace("__NEW_GUARD_SHA__", $newGuardSha).
  Replace("__MODE__", $mode).
  Replace("__ADMISSION_B64__", $admissionB64).
  Replace("__CONFIG_B64__", $configB64).
  Replace("__GUARD_B64__", $guardB64)
if ($remote -cmatch "__[A-Z0-9_]+__") { throw "Unfilled placeholder." }

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile "bwg" -Command $remote -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900 -RemoteTempPrefix "admission-skills-strip-20261122"

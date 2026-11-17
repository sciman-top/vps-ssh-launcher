#requires -Version 7
# User-directed CN/INTL admission lane split deployment (2026-11-17).
#
# Lands the reviewed 12c487f generation on bwg. Bulk -Apply is fail-closed by
# the admission pin transition guard (REFUSE admission_pin_transition_pending),
# because this change rotates the admission source generation. Per the
# 92f0f57/delatch precedent this transaction swaps the whole generation as one
# reviewed deploy, then a follow-up bulk -Apply projects the remaining files.
#
# Six-piece swap (the live new admission refuses to start with any old
# neighbor, so they are one atomic unit):
#   1. /opt/cliproxyapi/cpa-admission.py          (new source, pin 16ace2c9…)
#   2. /opt/cliproxyapi/cpa-admission.json        (5 lanes; workbuddy-intl-oauth)
#   3. /opt/cliproxyapi/cpa_provider_routes.json  (INTL plugin route + fork alias;
#      load_config fail-closed cross-checks model_lanes against this manifest)
#   4. /usr/local/libexec/cpa_request_guard.py    (embeds the new generation sha)
#   5. /usr/local/libexec/cpa_admission_bootstrap.py (passes
#      --workbuddy-intl-state-file; the new admission parser errors without it)
#   6. /etc/vps-ssh-launcher/cpa-admission.sha256 (pin rotated in the same step)
# plus /var/lib/cpa-admission/workbuddy-intl-risk.json pre-created with the
# exact ownership/mode contract (nobody:nogroup 0600, dir 0700).
#
# Baseline sha256 anchors were cross-proven against the host on 2026-11-17 via
# the doctor projection-drift report (its got= values equal the HEAD^ blobs).
param(
  [switch]$DryRun,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLivePin = "6abf9d7f92ceaa366fc1ba693bd99065458b1a82ddc16a0e2d66ebbee273aa9c",
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLiveConfigSha = "359d5a03062a6d47bb7b97af8046cca01087e1cda50775187a700403f019d51a",
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLiveRoutesSha = "46b5a6c843f2e4db752652cdab55e017b48b1b8dace7c3eb4c8425e382b04cd6",
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLiveBootstrapSha = "24254069d3fa07ed4107b526372fa9c39bc45628de0f81161c848490af20d24d"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

# Reviewed HEAD generation (12c487f); doctor want= values on 2026-11-17.
$reviewedAdmissionSha = "16ace2c97e457f3843993c77268429b0a3604dfbc967fb9a8a7b59520ebf7778"
$reviewedConfigSha = "c46bf5fc65cc36da69263705858d400d893786deeb052eb6b51cde15457e29d4"
$reviewedRoutesSha = "0b3feddaaddb38adce196f5f74bb239c365ce1e3723f1a70e58e5b215e9c9a27"
$reviewedBootstrapSha = "00f4b7d78384441cf8b42ca89e44e31af26875f9bf5ea8aaffe2f717de08aac4"
$reviewedGuardSha = "dbedbd3d1be5778b56bf367509e4373d06c4de776f958a766a289d3d275e0ae4"

$sources = [ordered]@{
  "scripts/remote/cpa-admission.py" = ""
  "scripts/remote/cpa-admission.json" = ""
  "scripts/remote/cpa_provider_routes.json" = ""
  "scripts/remote/cpa_admission_bootstrap.py" = ""
  "scripts/remote/cpa_request_guard.py" = ""
  "scripts/remote/cpa-admission-integrity-pin.txt" = ""
}
foreach ($relative in $sources.Keys) {
  $status = @(git -C $repoRoot status --porcelain=v1 -- $relative 2>&1)
  if ($LASTEXITCODE -ne 0) { throw "git status failed for $relative." }
  if ($status.Count -gt 0) { throw "Source is dirty; reviewed deploy requires a clean tree: $relative" }
}

function Read-LfNormalizedBytes([string]$Relative) {
  $path = Join-Path $repoRoot ($Relative -replace "/", "\")
  $text = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($path)).
    Replace("`r`n", "`n").Replace("`r", "`n")
  return [Text.Encoding]::UTF8.GetBytes($text)
}

$admissionBytes = Read-LfNormalizedBytes "scripts/remote/cpa-admission.py"
$newPin = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($admissionBytes)).ToLowerInvariant()
if ($newPin -ne $reviewedAdmissionSha) {
  throw "Admission candidate sha drifted from the reviewed generation: $newPin"
}
$pinFileValue = (Get-Content -LiteralPath (Join-Path $repoRoot "scripts/remote/cpa-admission-integrity-pin.txt") -Raw).Trim()
if ($pinFileValue -ne $newPin) {
  throw "Repo integrity pin does not match the candidate admission source."
}
$admissionText = [Text.UTF8Encoding]::new($false, $true).GetString($admissionBytes)
foreach ($marker in @("workbuddy-intl-state-file", "workbuddy-intl-oauth")) {
  if (-not $admissionText.Contains($marker)) {
    throw "Admission candidate is missing marker: $marker"
  }
}

$configBytes = Read-LfNormalizedBytes "scripts/remote/cpa-admission.json"
$configSha = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($configBytes)).ToLowerInvariant()
if ($configSha -ne $reviewedConfigSha) {
  throw "Config candidate sha drifted from the reviewed generation: $configSha"
}
$configText = [Text.UTF8Encoding]::new($false, $true).GetString($configBytes)
foreach ($marker in @("workbuddy-intl-oauth", "zero_cost_models")) {
  if (-not $configText.Contains($marker)) {
    throw "Config candidate is missing marker: $marker"
  }
}

$routesBytes = Read-LfNormalizedBytes "scripts/remote/cpa_provider_routes.json"
$routesSha = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($routesBytes)).ToLowerInvariant()
if ($routesSha -ne $reviewedRoutesSha) {
  throw "Routes manifest candidate sha drifted from the reviewed generation: $routesSha"
}
$routesText = [Text.UTF8Encoding]::new($false, $true).GetString($routesBytes)
foreach ($marker in @("deepseek-v4.1-flash-intl", "workbuddy-intl-oauth")) {
  if (-not $routesText.Contains($marker)) {
    throw "Routes manifest candidate is missing marker: $marker"
  }
}

$bootstrapBytes = Read-LfNormalizedBytes "scripts/remote/cpa_admission_bootstrap.py"
$bootstrapSha = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($bootstrapBytes)).ToLowerInvariant()
if ($bootstrapSha -ne $reviewedBootstrapSha) {
  throw "Bootstrap candidate sha drifted from the reviewed generation: $bootstrapSha"
}
$bootstrapText = [Text.UTF8Encoding]::new($false, $true).GetString($bootstrapBytes)
if (-not $bootstrapText.Contains("--workbuddy-intl-state-file")) {
  throw "Bootstrap candidate is missing the INTL state file argument."
}

# The request guard embeds the admission generation it serves; the bootstrap
# refuses to start when sha256(live admission) != guard.SOURCE_SHA256.
$guardTemplateText = [Text.UTF8Encoding]::new($false, $true).GetString(
  (Read-LfNormalizedBytes "scripts/remote/cpa_request_guard.py"))
if (-not $guardTemplateText.Contains("__SOURCE_SHA256__")) {
  throw "Request guard template is missing the __SOURCE_SHA256__ placeholder."
}
$guardDeployedText = $guardTemplateText.Replace("__SOURCE_SHA256__", $newPin)
$guardBytes = [Text.Encoding]::UTF8.GetBytes($guardDeployedText)
$guardSha = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($guardBytes)).ToLowerInvariant()
if ($guardSha -ne $reviewedGuardSha) {
  throw "Request guard deployed sha drifted from the reviewed generation: $guardSha"
}

$payload = [Convert]::ToBase64String($admissionBytes)
$configPayload = [Convert]::ToBase64String($configBytes)
$routesPayload = [Convert]::ToBase64String($routesBytes)
$bootstrapPayload = [Convert]::ToBase64String($bootstrapBytes)
$guardPayload = [Convert]::ToBase64String($guardBytes)

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
RISK_DIR=/var/lib/cpa-admission
INTL_STATE=$RISK_DIR/workbuddy-intl-risk.json
STAGED_LIVE=$DIR/.cpa-admission.py.new-cpintl-20261117.$$
STAGED_CONFIG=$DIR/.cpa-admission.json.new-cpintl-20261117.$$
STAGED_ROUTES=$DIR/.cpa_provider_routes.json.new-cpintl-20261117.$$
STAGED_BOOTSTRAP=/usr/local/libexec/.cpa_admission_bootstrap.py.new-cpintl-20261117.$$
STAGED_GUARD=/usr/local/libexec/.cpa_request_guard.py.new-cpintl-20261117.$$
OLD_PIN=__OLD_PIN__
NEW_PIN=__NEW_PIN__
OLD_CONFIG_SHA=__OLD_CONFIG_SHA__
NEW_CONFIG_SHA=__NEW_CONFIG_SHA__
OLD_ROUTES_SHA=__OLD_ROUTES_SHA__
NEW_ROUTES_SHA=__NEW_ROUTES_SHA__
OLD_BOOTSTRAP_SHA=__OLD_BOOTSTRAP_SHA__
NEW_BOOTSTRAP_SHA=__NEW_BOOTSTRAP_SHA__
NEW_GUARD_SHA=__NEW_GUARD_SHA__
MODE=__MODE__
BACKUP_PARENT=/opt/cliproxyapi/backups
BK=$BACKUP_PARENT/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-cpintl-split-20261117
TRAFFIC=/run/cpa-admission/requests.lock
MUTATED=0
INTL_STATE_CREATED=0

cleanup_stage() {
  rm -f -- "$STAGED_LIVE" "$STAGED_CONFIG" "$STAGED_ROUTES" \
    "$STAGED_BOOTSTRAP" "$STAGED_GUARD" 2>/dev/null || true
}
trap cleanup_stage EXIT

printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED_LIVE"
printf '%s' '__CONFIG_PAYLOAD__' | base64 -d > "$STAGED_CONFIG"
printf '%s' '__ROUTES_PAYLOAD__' | base64 -d > "$STAGED_ROUTES"
printf '%s' '__BOOTSTRAP_PAYLOAD__' | base64 -d > "$STAGED_BOOTSTRAP"
printf '%s' '__GUARD_PAYLOAD__' | base64 -d > "$STAGED_GUARD"
chmod --reference="$LIVE" "$STAGED_LIVE"
chown --reference="$LIVE" "$STAGED_LIVE"
chmod --reference="$CONFIG" "$STAGED_CONFIG"
chown --reference="$CONFIG" "$STAGED_CONFIG"
chmod --reference="$ROUTES" "$STAGED_ROUTES"
chown --reference="$ROUTES" "$STAGED_ROUTES"
chmod --reference="$BOOTSTRAP" "$STAGED_BOOTSTRAP"
chown --reference="$BOOTSTRAP" "$STAGED_BOOTSTRAP"
chmod --reference="$GUARD" "$STAGED_GUARD"
chown --reference="$GUARD" "$STAGED_GUARD"

[ "$(sha256sum "$STAGED_LIVE" | awk '{print $1}')" = "$NEW_PIN" ] || {
  echo 'REFUSE staged_admission_sha_mismatch'; exit 20; }
[ "$(sha256sum "$STAGED_CONFIG" | awk '{print $1}')" = "$NEW_CONFIG_SHA" ] || {
  echo 'REFUSE staged_config_sha_mismatch'; exit 20; }
[ "$(sha256sum "$STAGED_ROUTES" | awk '{print $1}')" = "$NEW_ROUTES_SHA" ] || {
  echo 'REFUSE staged_routes_sha_mismatch'; exit 20; }
[ "$(sha256sum "$STAGED_BOOTSTRAP" | awk '{print $1}')" = "$NEW_BOOTSTRAP_SHA" ] || {
  echo 'REFUSE staged_bootstrap_sha_mismatch'; exit 20; }
[ "$(sha256sum "$STAGED_GUARD" | awk '{print $1}')" = "$NEW_GUARD_SHA" ] || {
  echo 'REFUSE staged_guard_sha_mismatch'; exit 20; }
grep -q "SOURCE_SHA256 = \"$NEW_PIN\"" "$STAGED_GUARD" || {
  echo 'REFUSE staged_guard_generation_mismatch'; exit 20; }

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }

check_idle() {
  python3 - "$@" <<'PY'
import json, sys, urllib.request
expected_lanes = sorted(sys.argv[1:])
try:
    with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
        health = json.load(response)
except (OSError, ValueError) as exc:
    raise SystemExit('REFUSE healthz_unavailable: ' + str(exc))
assert health.get('status') == 'ok', 'REFUSE healthz_not_ok'
lanes = health.get('lanes')
if not isinstance(lanes, dict) or sorted(lanes) != expected_lanes:
    raise SystemExit(
        'REFUSE healthz_lane_contract_missing expected=%s got=%s'
        % (expected_lanes, sorted(lanes) if isinstance(lanes, dict) else None)
    )
for name, lane in lanes.items():
    state = lane.get('state') if isinstance(lane, dict) else None
    if not isinstance(state, dict):
        raise SystemExit('REFUSE healthz_state_missing lane=' + name)
    inflight = state.get('inflight')
    pending = state.get('pending')
    if type(inflight) is not int or type(pending) is not int:
        raise SystemExit('REFUSE healthz_traffic_fields_missing lane=' + name)
    if inflight != 0 or pending != 0:
        raise SystemExit(
            'REFUSE active_requests_not_drained lane=%s inflight=%d pending=%d'
            % (name, inflight, pending)
        )
print('ADMISSION_IDLE_OK')
PY
}

current_live_pin="$(sha256sum "$LIVE" | awk '{print $1}')"
current_installed_pin="$(tr -d '\r\n' < "$PIN")"
if [ "$current_live_pin" = "$NEW_PIN" ] &&
   [ "$current_installed_pin" = "$NEW_PIN" ] &&
   [ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$NEW_CONFIG_SHA" ] &&
   [ "$(sha256sum "$ROUTES" | awk '{print $1}')" = "$NEW_ROUTES_SHA" ] &&
   [ "$(sha256sum "$BOOTSTRAP" | awk '{print $1}')" = "$NEW_BOOTSTRAP_SHA" ] &&
   grep -q "SOURCE_SHA256 = \"$NEW_PIN\"" "$GUARD"; then
  runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
  check_idle antigravity-oauth chatgpt-oauth workbuddy-intl-oauth workbuddy-oauth zhipu-coding-plan >/dev/null
  echo "ALREADY_APPLIED mode=$MODE pin=$NEW_PIN no_swap=true"
  exit 0
fi

[ "$current_live_pin" = "$OLD_PIN" ] || {
  echo "REFUSE live_admission_baseline_drift live=$current_live_pin want=$OLD_PIN"; exit 20; }
[ "$current_installed_pin" = "$OLD_PIN" ] || {
  echo 'REFUSE pin_baseline_drift'; exit 20; }
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$OLD_CONFIG_SHA" ] || {
  echo 'REFUSE config_baseline_drift'; exit 20; }
[ "$(sha256sum "$ROUTES" | awk '{print $1}')" = "$OLD_ROUTES_SHA" ] || {
  echo 'REFUSE routes_baseline_drift'; exit 20; }
[ "$(sha256sum "$BOOTSTRAP" | awk '{print $1}')" = "$OLD_BOOTSTRAP_SHA" ] || {
  echo 'REFUSE bootstrap_baseline_drift'; exit 20; }
grep -q "SOURCE_SHA256 = \"$OLD_PIN\"" "$GUARD" || {
  echo 'REFUSE live_guard_generation_drift'; exit 20; }

python3 - "$STAGED_LIVE" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')
PY
python3 - "$STAGED_BOOTSTRAP" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')
PY
runuser -u nobody -- python3 - "$STAGED_LIVE" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')
PY

if [ "$MODE" = 'dry-run' ]; then
  exec 8<"$TRAFFIC"
  flock -n -x 8 || {
    echo 'DRY_RUN_BUSY live_traffic_active=true no_swap=true'
    exec 8<&-
    exit 0
  }
  check_idle antigravity-oauth chatgpt-oauth workbuddy-oauth zhipu-coding-plan || {
    echo 'DRY_RUN_BUSY healthz_has_inflight_or_pending no_swap=true'
    exec 8<&-
    exit 75
  }
  runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
  exec 8<&-
  echo "DRY_RUN_OK baseline=$OLD_PIN candidate=$NEW_PIN no_swap=true"
  exit 0
fi

echo 'PREFLIGHT_OK'
exec 8<"$TRAFFIC"
acquired=0
for attempt in $(seq 1 240); do
  if flock -x -w 1 8; then
    acquired=1
    break
  fi
  if [ $((attempt % 15)) -eq 0 ]; then
    echo "DRAIN_WAIT attempt=$attempt live_streams_still_open=true"
  fi
done
if [ "$acquired" != 1 ]; then
  echo 'REFUSE live_traffic_active lock_not_acquired no_swap=true'
  exit 75
fi
echo 'REQUEST_GUARD_EXCLUSIVE_OK'
check_idle antigravity-oauth chatgpt-oauth workbuddy-oauth zhipu-coding-plan

state_status=$(python3 - "$RISK_DIR" "$INTL_STATE" <<'PY'
import json, os, pwd, grp, stat, sys, tempfile
from pathlib import Path
directory = Path(sys.argv[1])
path = Path(sys.argv[2])
if directory.is_symlink() or not directory.is_dir():
    raise SystemExit('risk_state_directory_invalid')
owner = pwd.getpwnam('nobody').pw_uid
group = grp.getgrnam('nogroup').gr_gid
directory_stat = directory.stat()
if (
    directory_stat.st_uid != owner
    or directory_stat.st_gid != group
    or stat.S_IMODE(directory_stat.st_mode) != 0o700
):
    raise SystemExit('risk_state_directory_permissions_drift')
if path.exists() or path.is_symlink():
    if path.is_symlink() or not path.is_file():
        raise SystemExit('intl_state_not_regular_file')
    file_stat = path.stat()
    if (
        file_stat.st_uid != owner
        or file_stat.st_gid != group
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise SystemExit('intl_state_permissions_drift')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise SystemExit('intl_state_invalid_json')
    if (
        not isinstance(value, dict)
        or value.get('version') != 1
        or type(value.get('not_before')) not in (int, float)
        or (
            'hold_reason' in value
            and value['hold_reason'] is not None
            and not isinstance(value['hold_reason'], str)
        )
    ):
        raise SystemExit('intl_state_schema_drift')
    print('EXISTING')
else:
    payload = json.dumps(
        {'version': 1, 'not_before': 0, 'hold_reason': None},
        separators=(',', ':'),
    )
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(directory))
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, owner, group)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if fd != -1:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    print('INITIALIZED')
PY
) || { echo 'REFUSE intl_state_prepare_failed'; exit 20; }
case "$state_status" in
  EXISTING) echo 'INTL_RISK_STATE=EXISTING' ;;
  INITIALIZED) INTL_STATE_CREATED=1; echo 'INTL_RISK_STATE=INITIALIZED' ;;
  *) echo "REFUSE intl_state_unknown status=$state_status"; exit 20 ;;
esac

mkdir -p "$BACKUP_PARENT"
mkdir -m 700 "$BK"
systemctl cat cpa-admission.service > "$BK/cpa-admission.service.txt"
systemctl show cpa-admission.service --no-pager > "$BK/cpa-admission.service.show.txt"
systemctl is-enabled cpa-admission.service > "$BK/cpa-admission.service.enabled.txt"
systemctl is-active cpa-admission.service > "$BK/cpa-admission.service.active.txt"
cp -a "$LIVE" "$CONFIG" "$ROUTES" "$BOOTSTRAP" "$GUARD" "$PIN" "$BK/"
echo "BACKUP_READY path=$BK"

rollback() {
  local rc=${1:-$?} failed=0
  trap - EXIT HUP INT TERM
  if [ "$INTL_STATE_CREATED" = 1 ]; then
    rm -f -- "$INTL_STATE" || failed=1
  fi
  if [ "$MUTATED" = 1 ]; then
    cp -a "$BK/cpa-admission.py" "$LIVE" || failed=1
    cp -a "$BK/cpa-admission.json" "$CONFIG" || failed=1
    cp -a "$BK/cpa_provider_routes.json" "$ROUTES" || failed=1
    cp -a "$BK/cpa_admission_bootstrap.py" "$BOOTSTRAP" || failed=1
    cp -a "$BK/cpa_request_guard.py" "$GUARD" || failed=1
    cp -a "$BK/cpa-admission.sha256" "$PIN" || failed=1
    systemctl restart cpa-admission || failed=1
    sleep 2
    runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check || failed=1
    python3 - <<'PY' || failed=1
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    assert json.load(response).get('status') == 'ok'
PY
    if [ "$failed" = 0 ]; then
      echo "ROLLBACK_VERIFIED backup=$BK"
    else
      echo "ROLLBACK_FAILED backup=$BK"
    fi
  fi
  exec 8<&- 2>/dev/null || true
  [ "$rc" != 0 ] || rc=1
  exit "$rc"
}
trap rollback EXIT HUP INT TERM

MUTATED=1
mv -f "$STAGED_LIVE" "$LIVE"
mv -f "$STAGED_CONFIG" "$CONFIG"
mv -f "$STAGED_ROUTES" "$ROUTES"
mv -f "$STAGED_BOOTSTRAP" "$BOOTSTRAP"
mv -f "$STAGED_GUARD" "$GUARD"
printf '%s\n' "$NEW_PIN" > "$PIN.new"
chmod 644 "$PIN.new"
chown root:root "$PIN.new"
mv -f "$PIN.new" "$PIN"
echo 'ADMISSION_PIN_ROTATED old='$OLD_PIN' new='$NEW_PIN
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl restart cpa-admission

deadline=$((SECONDS + 30))
while :; do
  check_idle antigravity-oauth chatgpt-oauth workbuddy-intl-oauth workbuddy-oauth zhipu-coding-plan >/dev/null 2>&1 && break
  if (( SECONDS >= deadline )); then
    echo 'REFUSE readiness_timeout'
    rollback 20
  fi
  sleep 1
done
[ "$(sha256sum "$LIVE" | awk '{print $1}')" = "$NEW_PIN" ]
[ "$(tr -d '\r\n' < "$PIN")" = "$NEW_PIN" ]
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$NEW_CONFIG_SHA" ]
[ "$(sha256sum "$ROUTES" | awk '{print $1}')" = "$NEW_ROUTES_SHA" ]
[ "$(sha256sum "$BOOTSTRAP" | awk '{print $1}')" = "$NEW_BOOTSTRAP_SHA" ]
grep -q "SOURCE_SHA256 = \"$NEW_PIN\"" "$GUARD"
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
systemctl is-active --quiet cpa-admission

if ! python3 - "$LIVE" <<'PY_CONTRACT'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("cpa_admission_live", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit("candidate_import_failed")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

cfg = module.load_config(Path("/opt/cliproxyapi/cpa-admission.json"))
assert len(cfg["lanes"]) == 5, len(cfg["lanes"])
model_lanes = cfg["model_lanes"]
assert model_lanes["deepseek-v4.1-flash"] == "workbuddy-oauth", model_lanes
assert model_lanes["deepseek-v4.1-flash-intl"] == "workbuddy-intl-oauth", model_lanes
assert model_lanes["deepseek-v4.1-flash-sg"] == "workbuddy-intl-oauth", model_lanes
assert model_lanes["hy4free"] == "workbuddy-intl-oauth", model_lanes
cn = next(item for item in cfg["lanes"] if item["name"] == "workbuddy-oauth")
intl = next(item for item in cfg["lanes"] if item["name"] == "workbuddy-intl-oauth")
assert set(cn["models"]) == {"deepseek-v4.1-flash"}, cn["models"]
assert set(intl["models"]) == {
    "deepseek-v4.1-flash-intl", "deepseek-v4.1-flash-sg", "hy4free"
}, intl["models"]
assert "hy4free" in intl["zero_cost_models"], intl.get("zero_cost_models")
assert intl["max_inflight"] == 1 and cn["max_inflight"] == 1
assert intl["requests_per_minute"] == 18 and cn["requests_per_minute"] == 18
assert module.ADMISSION_MAX_INFLIGHT_BY_LANE.get("workbuddy-intl-oauth") == 1
assert module.WORKBUDDY_6004_MAX_COOLDOWN_SECONDS == 300
print("CP_INTL_SPLIT_20261117_CONTRACT_OK")
PY_CONTRACT
then
  echo 'REFUSE post_swap_contract_failed'
  rollback 20
fi

MUTATED=0
trap - EXIT HUP INT TERM
(
  find "$BACKUP_PARENT" -mindepth 1 -maxdepth 1 -type d \
    -name '*-admission-cpintl-split-20261117' -printf '%T@ %p\n' \
    | sort -rn | tail -n +9 | cut -d' ' -f2-
) | while IFS= read -r old_backup; do
  rm -rf -- "$old_backup"
done
exec 8<&- 2>/dev/null || true
echo "DEPLOY_OK pin=$NEW_PIN backup=$BK intl_state=$state_status"
'@

$remote = $remote.
  Replace("__OLD_PIN__", $ExpectedLivePin).
  Replace("__NEW_PIN__", $newPin).
  Replace("__OLD_CONFIG_SHA__", $ExpectedLiveConfigSha).
  Replace("__NEW_CONFIG_SHA__", $configSha).
  Replace("__OLD_ROUTES_SHA__", $ExpectedLiveRoutesSha).
  Replace("__NEW_ROUTES_SHA__", $routesSha).
  Replace("__OLD_BOOTSTRAP_SHA__", $ExpectedLiveBootstrapSha).
  Replace("__NEW_BOOTSTRAP_SHA__", $bootstrapSha).
  Replace("__NEW_GUARD_SHA__", $guardSha).
  Replace("__MODE__", $mode).
  Replace("__PAYLOAD__", $payload).
  Replace("__CONFIG_PAYLOAD__", $configPayload).
  Replace("__ROUTES_PAYLOAD__", $routesPayload).
  Replace("__BOOTSTRAP_PAYLOAD__", $bootstrapPayload).
  Replace("__GUARD_PAYLOAD__", $guardPayload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled admission cpintl transaction placeholder."
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 300 -HardTimeoutSeconds 900 `
  -RemoteTempPrefix "admission-cpintl-split-20261117"

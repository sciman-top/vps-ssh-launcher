#requires -Version 7
# Reviewed narrow projection for the WorkBuddy burst ceiling after the
# queue-stream admission bundle: requests_per_minute 18 -> 12. No CPA image,
# plugin, provider config, credential, risk state or cooldown is changed.
param(
  [switch]$DryRun,
  [switch]$ForceRestart,
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedLiveConfig =
    "7f53e6c7fc33423060f0027f7d2121d626faae0e75b1356932d086a375d3e89f"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts/lib/project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

$candidatePath = Join-Path $repoRoot "scripts/remote/cpa-admission.json"
$candidateText = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($candidatePath)).
  Replace("`r`n", "`n").Replace("`r", "`n")
$candidateBytes = [Text.Encoding]::UTF8.GetBytes($candidateText)
$newHash = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData($candidateBytes)
).ToLowerInvariant()
$payload = [Convert]::ToBase64String($candidateBytes)
if ($DryRun -and $ForceRestart) {
  throw "-DryRun and -ForceRestart are mutually exclusive."
}
$mode = if ($DryRun) {
  "dry-run"
} elseif ($ForceRestart) {
  "apply-force"
} else {
  "apply"
}

$remote = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
CONFIG=/opt/cliproxyapi/cpa-admission.json
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
TRAFFIC=/run/cpa-admission/requests.lock
OLD_HASH=__OLD_HASH__
NEW_HASH=__NEW_HASH__
MODE=__MODE__
STAGED=/opt/cliproxyapi/.cpa-admission.json.burst-limit-20261101.$$
BK=/opt/cliproxyapi/backups/$(date -u +%Y%m%dT%H%M%S.%NZ)-admission-burst-limit-20261101
MUTATED=0

exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'UPDATE_ALREADY_RUNNING'; exit 75; }
trap 'rm -f -- "$STAGED"' EXIT
printf '%s' '__PAYLOAD__' | base64 -d > "$STAGED"
chmod 644 "$STAGED"
chown root:root "$STAGED"

[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$OLD_HASH" ] || {
  echo "REFUSE baseline_drift config=$(sha256sum "$CONFIG" | awk '{print $1}')"
  exit 20
}
[ "$(sha256sum "$STAGED" | awk '{print $1}')" = "$NEW_HASH" ] || {
  echo 'REFUSE staged_hash_mismatch'; exit 20;
}
/usr/local/libexec/cpa-admission-integrity-check

if [ "$MODE" = 'dry-run' ]; then
  echo "DRY_RUN_OK config=$NEW_HASH no_restart=true"
  exit 0
fi

if [ "$MODE" = 'apply-force' ]; then
  echo "FORCED_RESTART user_requested_at=$(TZ=Asia/Shanghai date +%FT%T%z)"
else
  exec 8<"$TRAFFIC"
  flock -x 8
  deadline=$((SECONDS + 90))
  while :; do
    busy=$(python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    health = json.load(response)
busy = [name for name, lane in health['lanes'].items()
        if lane['state']['inflight'] or lane['state']['pending']]
print(','.join(sorted(busy)) or 'none')
PY
)
    [ "$busy" = 'none' ] && break
    if (( SECONDS >= deadline )); then
      echo "REFUSE active_requests_did_not_drain busy=$busy"
      exec 8<&-
      exit 20
    fi
    echo "DRAIN_WAIT busy=$busy"
    sleep 2
  done
  echo 'ADMISSION_DRAIN_OK'
fi

mkdir -m 700 "$BK"
cp -a "$CONFIG" "$BK/"
rollback() {
  local rc=${1:-$?} failed=0
  trap - EXIT HUP INT TERM
  if [ "$MUTATED" = 1 ]; then
    cp -a "$BK/cpa-admission.json" "$CONFIG" || failed=1
    systemctl restart cpa-admission || failed=1
    sleep 2
    python3 - <<'PY' || failed=1
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=5) as response:
    assert json.load(response)['status'] == 'ok'
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
mv -f "$STAGED" "$CONFIG"
systemctl restart cpa-admission
deadline=$((SECONDS + 30))
while :; do
  if python3 - <<'PY'
import json, urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8318/healthz', timeout=2) as response:
        health = json.load(response)
except Exception:
    raise SystemExit(1)
assert health.get('status') == 'ok'
assert health['lanes']['workbuddy-oauth']['requests_per_minute'] == 12
assert all(lane['state']['hold_reason'] is None for lane in health['lanes'].values())
PY
  then
    break
  fi
  if (( SECONDS >= deadline )); then
    echo 'REFUSE readiness_timeout'
    rollback 20
  fi
  sleep 1
done
/usr/local/libexec/cpa-admission-integrity-check
systemctl is-active --quiet cpa-admission
[ "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$NEW_HASH" ]
MUTATED=0
trap - EXIT HUP INT TERM
exec 8<&-
echo "DEPLOY_OK config=$NEW_HASH backup=$BK"
'@

$remote = $remote.Replace("__OLD_HASH__", $ExpectedLiveConfig).
  Replace("__NEW_HASH__", $newHash).
  Replace("__MODE__", $mode).
  Replace("__PAYLOAD__", $payload)
if ($remote -match "__[A-Z_]+__") {
  throw "Unfilled admission config transaction placeholder."
}

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot `
  -Config $config -Profile "bwg" -Command $remote `
  -IdleTimeoutSeconds 120 -HardTimeoutSeconds 600 -RemoteTempPrefix "admission-burst-limit-20261101"

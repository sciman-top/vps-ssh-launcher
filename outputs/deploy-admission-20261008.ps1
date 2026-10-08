#requires -Version 7
# 2026-10-08 BWG admission float-fix narrow deploy (no providers.env on this
# host, so cpa_bwg_guardrails -Apply is unavailable here): stage
# scripts/remote/cpa-admission.py, then swap it under the shared maintenance
# lock with backup + rollback rails, mirroring the 2026-10-07 admission
# transaction. All assertions live in the remote scripts; the driver only
# sequences them and fails hard on any non-zero exit.
param(
  [string]$Profile = "bwg",
  [Parameter(Mandatory = $true)][string]$ExpectOldSha,
  [Parameter(Mandatory = $true)][string]$ExpectNewSha
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") {
  throw "This admission deployment is restricted to the bwg profile."
}
if ($ExpectOldSha -notmatch '^(?i:[0-9a-f]{64})$' -or $ExpectNewSha -notmatch '^(?i:[0-9a-f]{64})$') {
  throw "Expected source hashes must be 64 hexadecimal characters."
}
if ($ExpectOldSha -eq $ExpectNewSha) {
  throw "Old and new hashes must differ for an admission deployment."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot
$srcPath = Join-Path $repoRoot "scripts\remote\cpa-admission.py"

$srcText = (Get-Content -LiteralPath $srcPath -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
$payloadB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($srcText))
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$stageDir = "/root/.cpa-adm-stage-$stamp"
$backupDir = "/root/cpa-admission-floatfix-backup-$stamp"

Write-Host "== step 1: probe =="
$probe = @'
set -Eeuo pipefail
D=/opt/cliproxyapi
echo "OLD_SHA=$(sha256sum "$D/cpa-admission.py" | cut -d" " -f1)"
echo "OLD_MODE=$(stat -c %a "$D/cpa-admission.py")"
echo "SVC=$(systemctl is-active cpa-admission.service)"
echo "PID=$(systemctl show cpa-admission.service -p MainPID --value)"
echo "PY=$(python3 -V 2>&1)"
python3 -c 'import math; math.ulp; print("MATH_ULP=ok")'
curl -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
print("STATUS=" + str(h.get("status")))
print("RETIRED_READERS=" + str(h.get("retired_readers")))
for name, lane in sorted(h.get("lanes", {}).items()):
    s = lane.get("state", {})
    print("LANE", name, "inflight=", s.get("inflight"), "pending=", s.get("pending"), "cooldown=", s.get("cooldown_active"))
'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $probe

Write-Host "== step 2: stage new file =="
$stage = @"
set -Eeuo pipefail
umask 077
mkdir -m 700 '$stageDir'
printf '%s' '$payloadB64' > '$stageDir/payload.b64'
base64 -d '$stageDir/payload.b64' > '$stageDir/cpa-admission.py.new'
rm -f '$stageDir/payload.b64'
chmod 600 '$stageDir/cpa-admission.py.new'
GOT=`$(sha256sum '$stageDir/cpa-admission.py.new' | cut -d' ' -f1)
echo "STAGED_SHA=`$GOT"
[ "`$GOT" = '$ExpectNewSha' ] || { echo 'REFUSE staged sha mismatch'; exit 2; }
python3 -m py_compile '$stageDir/cpa-admission.py.new' && echo 'STAGED_COMPILE=ok'
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $stage

Write-Host "== step 3: transaction swap =="
$txn = @"
set -Eeuo pipefail
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'REFUSE maintenance lock held'; exit 75; }
D=/opt/cliproxyapi
TARGET="`$D/cpa-admission.py"
STAGE='$stageDir/cpa-admission.py.new'
BACKUP='$backupDir'
EXPECT_OLD='$ExpectOldSha'
EXPECT_NEW='$ExpectNewSha'

CUR=`$(sha256sum "`$TARGET" | cut -d' ' -f1)
[ "`$CUR" = "`$EXPECT_OLD" ] || { echo "REFUSE current_sha=`$CUR expected=`$EXPECT_OLD"; exit 2; }
[ -f "`$STAGE" ] || { echo 'REFUSE staged file missing'; exit 2; }

curl -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
assert h.get("status") == "ok", "status not ok"
assert int(h.get("retired_readers", 0)) == 0, "retired_readers busy"
for name, lane in h.get("lanes", {}).items():
    s = lane.get("state", {})
    assert int(s.get("inflight", 0)) == 0, name + " inflight"
    assert int(s.get("pending", 0)) == 0, name + " pending"
    assert not s.get("cooldown_active"), name + " cooling"
    assert not s.get("half_open_probe"), name + " half-open"
    assert int(s.get("failure_streak", 0)) == 0, name + " failures"
    assert int(s.get("server_retry_after_remaining", 0)) == 0, name + " Retry-After"
print("LANES_IDLE=ok")
'

mkdir -m 700 "`$BACKUP"
cp -a "`$TARGET" "`$BACKUP/cpa-admission.py.old"
cat > "`$BACKUP/rollback.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
if [ "`$#" -gt 0 ]; then
  if [ "`$#" -ne 1 ] || [ "`$1" != --dry-run ]; then
    echo 'REFUSE unknown rollback arguments' >&2
    exit 2
  fi
  sha256sum '$backupDir/cpa-admission.py.old' | cut -d' ' -f1 | grep -qx '$ExpectOldSha'
  echo ROLLBACK_DRY_RUN_OK
  exit 0
fi
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || exit 75
cp -a '$backupDir/cpa-admission.py.old' '/opt/cliproxyapi/cpa-admission.py'
systemctl restart cpa-admission.service
sleep 2
curl -fsS --max-time 5 http://127.0.0.1:8318/healthz >/dev/null && echo ROLLBACK_OK
EOF
chmod 700 "`$BACKUP/rollback.sh"
sha256sum "`$BACKUP/cpa-admission.py.old" | cut -d' ' -f1 | grep -qx "`$EXPECT_OLD" || { echo 'REFUSE backup sha mismatch'; exit 2; }
bash -n "`$BACKUP/rollback.sh" || { echo 'REFUSE rollback syntax'; exit 2; }
bash "`$BACKUP/rollback.sh" --dry-run || { echo 'REFUSE rollback preflight'; exit 2; }
echo "BACKUP_DIR=`$BACKUP"

restore() {
  cp -a "`$BACKUP/cpa-admission.py.old" "`$TARGET"
  systemctl restart cpa-admission.service
  sleep 2
  if curl -fsS --max-time 5 http://127.0.0.1:8318/healthz >/dev/null 2>&1; then
    echo 'ROLLBACK_OK'
  else
    echo 'ROLLBACK_FAILED'
  fi
}

cp "`$STAGE" "`$D/.cpa-admission.py.tmp"
chown --reference="`$TARGET" "`$D/.cpa-admission.py.tmp"
chmod --reference="`$TARGET" "`$D/.cpa-admission.py.tmp"
mv -f "`$D/.cpa-admission.py.tmp" "`$TARGET"
GOT=`$(sha256sum "`$TARGET" | cut -d' ' -f1)
[ "`$GOT" = "`$EXPECT_NEW" ] || { echo "REFUSE post-install sha=`$GOT"; restore; exit 3; }

if ! systemctl restart cpa-admission.service; then
  echo 'RESTART_FAILED'
  restore
  exit 3
fi
OK=0
for _ in `$(seq 1 20); do
  if curl -fsS --max-time 3 http://127.0.0.1:8318/healthz >/dev/null 2>&1; then OK=1; break; fi
  sleep 1
done
if [ "`$OK" != 1 ]; then
  echo 'HEALTHZ_TIMEOUT'
  restore
  exit 3
fi
NEWPID=`$(systemctl show cpa-admission.service -p MainPID --value)
echo "DEPLOY_OK sha=`$GOT pid=`$NEWPID backup=`$BACKUP"
rm -rf '$stageDir'
echo 'STAGE_CLEANED'
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $txn
Write-Host "DRIVER_RESULT=PASS backup=$backupDir"

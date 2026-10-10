#requires -Version 7
# 2026-10-10 BWG admission generation rotation (modelstreak): project the
# per-model streak on lane-scoped capacity refusals -- one file changes
# (cpa-admission.py) plus its integrity pin; cpa-admission.json and
# cpa_policy.py are untouched this generation.
#
# Same reviewed transaction shape as outputs/deploy-admission-20261010.ps1:
# stage, verify, swap under the shared maintenance lock with backup + rollback
# rails, restart, verify. `-Apply` stays unavailable on this host (no
# providers.env); this driver is the reviewed narrow transaction.
param(
  [string]$Profile = "bwg"
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") {
  throw "This admission deployment is restricted to the bwg profile."
}

$oldAdmission = "183fdd1f68d7a2b8de4fc5dd478ea81c7965dcb49d807e995e54c6bb682fd2c6"
$newAdmission = "1ae590d821bb6fa94275ae0e5bf0b85d9394021f6e3eda253efa45f2bf8cd21d"
$oldPin = "183fdd1f68d7a2b8de4fc5dd478ea81c7965dcb49d807e995e54c6bb682fd2c6"
$newPin = "1ae590d821bb6fa94275ae0e5bf0b85d9394021f6e3eda253efa45f2bf8cd21d"
$keepConfig = "942f6f2aef4f5f9223f337db25d2416d6c4fed0d7ef912ca6c4dcc9a472de5d0"
$keepPolicy = "f7906ac89d5200246aa8a3ad80b1a9664b53da50cbd53317da84e2230e46ff7b"

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\project_environment.ps1")
$python = Resolve-ProjectPython -ProjectRoot $repoRoot
$config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot

function Get-LfPayload {
  param([Parameter(Mandatory = $true)][string]$RelativePath)
  $path = Join-Path $repoRoot ($RelativePath -replace '/', '\')
  $text = (Get-Content -LiteralPath $path -Raw).Replace("`r`n", "`n").Replace("`r", "`n")
  return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text))
}

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$stageDir = "/root/.cpa-adm-stage-$stamp"
$backupDir = "/root/cpa-admission-modelstreak-backup-$stamp"

Write-Host "== step 1: preflight probe =="
$probe = @'
set -Eeuo pipefail
D=/opt/cliproxyapi
echo "OLD_ADMISSION=$(sha256sum "$D/cpa-admission.py" | cut -d' ' -f1)"
echo "KEEP_CONFIG=$(sha256sum "$D/cpa-admission.json" | cut -d' ' -f1)"
echo "KEEP_POLICY=$(sha256sum "$D/cpa_policy.py" | cut -d' ' -f1)"
echo "OLD_PIN=$(awk 'NF {print $1; exit}' /etc/vps-ssh-launcher/cpa-admission.sha256)"
echo "SVC=$(systemctl is-active cpa-admission.service)"
echo "PID=$(systemctl show cpa-admission.service -p MainPID --value)"
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
print("STATUS=" + str(h.get("status")))
for name, lane in sorted(h.get("lanes", {}).items()):
    s = lane.get("state", {})
    print("LANE", name, "inflight=", s.get("inflight"),
          "pending=", s.get("pending"),
          "cooldown_remaining=", s.get("cooldown_remaining"),
          "cooldown_scope=", s.get("cooldown_scope"),
          "model_cooldowns=", s.get("model_cooldowns"))
'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $probe

Write-Host "== step 0: remove any stray staging directory =="
$cleanup = @"
set -Eeuo pipefail
rm -rf '$stageDir' 2>/dev/null || true
echo 'STAGE_PRE_CLEAN=ok'
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $cleanup

Write-Host "== step 2: stage new generation =="
$payload = Get-LfPayload "scripts/remote/cpa-admission.py"
$stage = @"
set -Eeuo pipefail
umask 077
mkdir -m 700 -p '$stageDir'
printf '%s' '$payload' > '$stageDir/cpa-admission.py.b64'
base64 -d '$stageDir/cpa-admission.py.b64' > '$stageDir/cpa-admission.py'
rm -f '$stageDir/cpa-admission.py.b64'
chmod 600 '$stageDir/cpa-admission.py'
GOT=`$(sha256sum '$stageDir/cpa-admission.py' | cut -d' ' -f1)
[ "`$GOT" = '$newAdmission' ] || { echo "REFUSE staged sha mismatch got=`$GOT"; exit 2; }
echo "STAGED=cpa-admission.py sha=`$GOT"
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $stage

Write-Host "== step 3: py_compile staged generation =="
$compileCheck = @'
set -Eeuo pipefail
python3 -m py_compile '__STAGEDIR__/cpa-admission.py' && echo 'COMPILE=cpa-admission.py ok'
'@
$compileCheck = $compileCheck.Replace('__STAGEDIR__', $stageDir)
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $compileCheck

Write-Host "== step 4: transaction swap under the maintenance lock =="
$txnTemplate = @'
set -Eeuo pipefail
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo 'REFUSE maintenance lock held'; exit 75; }

D=/opt/cliproxyapi
STAGE='__STAGE__'
BACKUP='__BACKUP__'
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
CHECK=/usr/local/libexec/cpa-admission-integrity-check
DROPIN=/etc/systemd/system/cpa-admission.service.d/10-integrity.conf

ADMISSION="$D/cpa-admission.py"
CONFIG="$D/cpa-admission.json"
POLICY="$D/cpa_policy.py"

OLD_ADMISSION='__OLD_ADMISSION__'
NEW_ADMISSION='__NEW_ADMISSION__'
KEEP_CONFIG='__KEEP_CONFIG__'
KEEP_POLICY='__KEEP_POLICY__'
OLD_PIN='__OLD_PIN__'
NEW_PIN='__NEW_PIN__'

cur_of() { sha256sum "$1" | cut -d' ' -f1; }

# Preconditions: the deployed generation is exactly the one this transaction
# was reviewed against, and the untouched files still match their reviewed
# hashes (this generation rotates only cpa-admission.py + the pin).
[ -x "$CHECK" ] && [ -f "$DROPIN" ] || { echo 'REFUSE admission integrity guard missing'; exit 2; }
[ -f "$PIN" ] || { echo 'REFUSE admission integrity pin missing'; exit 2; }
[ "$(awk 'NF {print $1; exit}' "$PIN")" = "$OLD_PIN" ] || { echo 'REFUSE pin is not the old generation'; exit 2; }
[ "$(cur_of "$ADMISSION")" = "$OLD_ADMISSION" ] || { echo "REFUSE current admission sha=$(cur_of "$ADMISSION")"; exit 2; }
[ "$(cur_of "$CONFIG")" = "$KEEP_CONFIG" ] || { echo 'REFUSE config is not the reviewed keep-generation'; exit 2; }
[ "$(cur_of "$POLICY")" = "$KEEP_POLICY" ] || { echo 'REFUSE policy is not the reviewed keep-generation'; exit 2; }
[ -f "$STAGE/cpa-admission.py" ] || { echo 'REFUSE staged file missing'; exit 2; }

# A restart drops in-flight upstream turns, so wait briefly for free lanes and
# refuse rather than cutting a live desktop turn.
IDLE=0
for _ in $(seq 1 20); do
  if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
if h.get("status") != "ok":
    raise SystemExit(1)
for name, lane in h.get("lanes", {}).items():
    s = lane.get("state", {})
    if int(s.get("inflight", 0)) or int(s.get("pending", 0)):
        raise SystemExit(1)
print("LANES_IDLE=ok")
' 2>/dev/null; then
    IDLE=1
    break
  fi
  sleep 3
done
[ "$IDLE" = 1 ] || { echo 'REFUSE lanes busy; retry when the gateway is idle'; exit 2; }

mkdir -m 700 "$BACKUP"
cp -a "$ADMISSION" "$BACKUP/cpa-admission.py.old"
cp -a "$CONFIG" "$BACKUP/cpa-admission.json.old"
cp -a "$POLICY" "$BACKUP/cpa_policy.py.old"
cp -a "$PIN" "$BACKUP/cpa-admission.sha256.old"
sha256sum "$BACKUP"/*.old > "$BACKUP/backup.sha256"

cat > "$BACKUP/rollback.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
if [ "$#" -gt 0 ]; then
  if [ "$#" -ne 1 ] || [ "$1" != --dry-run ]; then
    echo 'REFUSE unknown rollback arguments' >&2
    exit 2
  fi
  sha256sum '__BACKUP__'/cpa-admission.py.old | cut -d' ' -f1 | grep -qx '__OLD_ADMISSION__'
  sha256sum '__BACKUP__'/cpa-admission.json.old | cut -d' ' -f1 | grep -qx '__KEEP_CONFIG__'
  sha256sum '__BACKUP__'/cpa_policy.py.old | cut -d' ' -f1 | grep -qx '__KEEP_POLICY__'
  grep -qx '__OLD_PIN__' '__BACKUP__'/cpa-admission.sha256.old
  echo ROLLBACK_DRY_RUN_OK
  exit 0
fi
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || exit 75
cp -a '__BACKUP__'/cpa-admission.py.old /opt/cliproxyapi/cpa-admission.py
cp -a '__BACKUP__'/cpa-admission.sha256.old /etc/vps-ssh-launcher/cpa-admission.sha256
systemctl restart cpa-admission.service
sleep 2
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz >/dev/null && echo ROLLBACK_OK
EOF
chmod 700 "$BACKUP/rollback.sh"
bash -n "$BACKUP/rollback.sh" || { echo 'REFUSE rollback syntax'; exit 2; }
bash "$BACKUP/rollback.sh" --dry-run || { echo 'REFUSE rollback preflight'; exit 2; }
echo "BACKUP_DIR=$BACKUP"

restore() {
  cp -a "$BACKUP/cpa-admission.py.old" "$ADMISSION"
  cp -a "$BACKUP/cpa-admission.sha256.old" "$PIN"
  systemctl restart cpa-admission.service || true
  sleep 2
  if curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz >/dev/null 2>&1; then
    echo 'ROLLBACK_OK'
  else
    echo 'ROLLBACK_FAILED'
  fi
}

target=$ADMISSION
cp "$STAGE/cpa-admission.py" "$target.new"
chown --reference="$target" "$target.new"
chmod 755 "$target.new"
mv -f "$target.new" "$target"
got=$(cur_of "$target")
[ "$got" = "$NEW_ADMISSION" ] || { echo "REFUSE post-install sha mismatch got=$got"; restore; exit 3; }
echo "INSTALLED path=$target sha=$got mode=755"

# The pin is the ExecStartPre contract for the service, so it rotates in the
# same transaction and is verified before the restart.
printf '%s\n' "$NEW_PIN" > "$PIN.tmp"
chmod 644 "$PIN.tmp"
mv -f "$PIN.tmp" "$PIN"
[ "$(awk 'NF {print $1; exit}' "$PIN")" = "$NEW_PIN" ] || { echo 'REFUSE pin rotation mismatch'; restore; exit 3; }
"$CHECK" || { echo 'REFUSE integrity check after pin rotation'; restore; exit 3; }

if ! python3 "$POLICY" "$D/config.yaml" >/tmp/cpa-policy-modelstreak.log 2>&1; then
  echo 'REFUSE semantic policy check failed'
  tail -n 5 /tmp/cpa-policy-modelstreak.log
  restore
  exit 3
fi
echo 'SEMANTIC_POLICY=OK'

if ! systemctl restart cpa-admission.service; then
  echo 'RESTART_FAILED'
  restore
  exit 3
fi
OK=0
for _ in $(seq 1 20); do
  if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8318/healthz >/dev/null 2>&1; then OK=1; break; fi
  sleep 1
done
if [ "$OK" != 1 ]; then
  echo 'HEALTHZ_TIMEOUT'
  restore
  exit 3
fi

# The live generation must still expose the per-model breaker fields.
if ! curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
assert h.get("status") == "ok", "status"
lanes = h.get("lanes") or {}
assert set(lanes) == {"chatgpt-oauth", "zhipu-coding-plan", "deepseek-official"}, lanes
for name, lane in lanes.items():
    s = lane.get("state") or {}
    assert s.get("cooldown_scope") in {"lane", "model", "none"}, name
    assert isinstance(s.get("model_cooldowns"), dict), name
print("GENERATION_FIELDS=ok")
'; then
  echo 'REFUSE live generation missing model-scope fields'
  restore
  exit 3
fi

NEWPID=$(systemctl show cpa-admission.service -p MainPID --value)
echo "DEPLOY_OK admission=$(cur_of "$ADMISSION") pin=$(awk 'NF {print $1; exit}' "$PIN") pid=$NEWPID backup=$BACKUP"
rm -rf "$STAGE"
echo 'STAGE_CLEANED'
'@

$txn = $txnTemplate.Replace('__STAGE__', $stageDir)
$txn = $txn.Replace('__BACKUP__', $backupDir)
$txn = $txn.Replace('__OLD_ADMISSION__', $oldAdmission)
$txn = $txn.Replace('__NEW_ADMISSION__', $newAdmission)
$txn = $txn.Replace('__KEEP_CONFIG__', $keepConfig)
$txn = $txn.Replace('__KEEP_POLICY__', $keepPolicy)
$txn = $txn.Replace('__OLD_PIN__', $oldPin)
$txn = $txn.Replace('__NEW_PIN__', $newPin)

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $txn
Write-Host "DRIVER_RESULT=PASS backup=$backupDir"

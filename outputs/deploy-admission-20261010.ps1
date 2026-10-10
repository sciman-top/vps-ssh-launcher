#requires -Version 7
# 2026-10-10 BWG admission generation rotation: project the model-scoped
# capacity breaker (cpa-admission.py + cpa-admission.json + cpa_policy.py and
# the admission integrity pin) in one reviewed transaction.
#
# `cpa_bwg_guardrails -Apply` is unavailable on this host because
# %APPDATA%\vps-ssh-launcher\providers.env (the provider slot credentials it
# projects) is not present, and the apply script deliberately refuses to rotate
# an admission generation itself ("rotate admission code with a reviewed deploy
# transaction, not with -Apply"). This driver is that transaction: it mirrors
# outputs/deploy-admission-20261008.ps1 - stage, verify, swap under the shared
# maintenance lock with backup + rollback rails, restart, verify.
param(
  [string]$Profile = "bwg"
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") {
  throw "This admission deployment is restricted to the bwg profile."
}

$oldAdmission = "183fdd1f68d7a2b8de4fc5dd478ea81c7965dcb49d807e995e54c6bb682fd2c6"
$newAdmission = "1ae590d821bb6fa94275ae0e5bf0b85d9394021f6e3eda253efa45f2bf8cd21d"
$oldConfig = "942f6f2aef4f5f9223f337db25d2416d6c4fed0d7ef912ca6c4dcc9a472de5d0"
$newConfig = "942f6f2aef4f5f9223f337db25d2416d6c4fed0d7ef912ca6c4dcc9a472de5d0"
$oldPolicy = "f7906ac89d5200246aa8a3ad80b1a9664b53da50cbd53317da84e2230e46ff7b"
$newPolicy = "f7906ac89d5200246aa8a3ad80b1a9664b53da50cbd53317da84e2230e46ff7b"
$oldPin = "183fdd1f68d7a2b8de4fc5dd478ea81c7965dcb49d807e995e54c6bb682fd2c6"
$newPin = "1ae590d821bb6fa94275ae0e5bf0b85d9394021f6e3eda253efa45f2bf8cd21d"

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
$backupDir = "/root/cpa-admission-modelscope-backup-$stamp"

Write-Host "== step 1: preflight probe =="
$probe = @'
set -Eeuo pipefail
D=/opt/cliproxyapi
echo "OLD_ADMISSION=$(sha256sum "$D/cpa-admission.py" | cut -d' ' -f1)"
echo "OLD_CONFIG=$(sha256sum "$D/cpa-admission.json" | cut -d' ' -f1)"
echo "OLD_POLICY=$(sha256sum "$D/cpa_policy.py" | cut -d' ' -f1)"
echo "OLD_PIN=$(awk 'NF {print $1; exit}' /etc/vps-ssh-launcher/cpa-admission.sha256)"
echo "SVC=$(systemctl is-active cpa-admission.service)"
echo "PID=$(systemctl show cpa-admission.service -p MainPID --value)"
echo "PY=$(python3 -V 2>&1)"
echo "HOSTNAME=$(hostname)"
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
print("STATUS=" + str(h.get("status")))
for name, lane in sorted(h.get("lanes", {}).items()):
    s = lane.get("state", {})
    print("LANE", name, "inflight=", s.get("inflight"),
          "pending=", s.get("pending"),
          "cooldown_remaining=", s.get("cooldown_remaining"),
          "failure_streak=", s.get("failure_streak"),
          "cooldown_scope=", s.get("cooldown_scope"),
          "model_cooldowns=", s.get("model_cooldowns"))
'
'@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $probe

Write-Host "== step 0: remove any stray staging directory =="
$cleanup = @"
set -Eeuo pipefail
rm -rf '/root/__STAGE__' 2>/dev/null || true
rm -rf '$stageDir' 2>/dev/null || true
echo 'STAGE_PRE_CLEAN=ok'
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $cleanup

Write-Host "== step 2: stage new generation =="
# Payloads are injected one file at a time to keep each remote command small.
$stageCommands = @(
  @("cpa-admission.py", (Get-LfPayload "scripts/remote/cpa-admission.py"), $newAdmission),
  @("cpa-admission.json", (Get-LfPayload "scripts/remote/cpa-admission.json"), $newConfig),
  @("cpa_policy.py", (Get-LfPayload "scripts/remote/cpa_policy.py"), $newPolicy)
)
foreach ($entry in $stageCommands) {
  $name = $entry[0]
  $payload = $entry[1]
  $expected = $entry[2]
  $stage = @"
set -Eeuo pipefail
umask 077
mkdir -m 700 -p '$stageDir'
printf '%s' '$payload' > '$stageDir/$name.b64'
base64 -d '$stageDir/$name.b64' > '$stageDir/$name'
rm -f '$stageDir/$name.b64'
chmod 600 '$stageDir/$name'
GOT=`$(sha256sum '$stageDir/$name' | cut -d' ' -f1)
[ "`$GOT" = '$expected' ] || { echo "REFUSE staged sha mismatch $name got=`$GOT"; exit 2; }
echo "STAGED=$name sha=`$GOT"
"@
  Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $stage
}

Write-Host "== step 3: py_compile staged generation =="
$compileCheck = @'
set -Eeuo pipefail
python3 -m py_compile '__STAGEDIR__/cpa-admission.py' && echo 'COMPILE=cpa-admission.py ok'
python3 -m py_compile '__STAGEDIR__/cpa_policy.py' && echo 'COMPILE=cpa_policy.py ok'
python3 -c 'import json,sys; json.load(open("__STAGEDIR__/cpa-admission.json")); print("COMPILE=cpa-admission.json ok")'
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
OLD_CONFIG='__OLD_CONFIG__'
NEW_CONFIG='__NEW_CONFIG__'
OLD_POLICY='__OLD_POLICY__'
NEW_POLICY='__NEW_POLICY__'
OLD_PIN='__OLD_PIN__'
NEW_PIN='__NEW_PIN__'

cur_of() { sha256sum "$1" | cut -d' ' -f1; }

# Preconditions: the deployed generation is exactly the one this transaction
# was reviewed against, and the integrity guard that pins it is installed.
[ -x "$CHECK" ] && [ -f "$DROPIN" ] || { echo 'REFUSE admission integrity guard missing'; exit 2; }
[ -f "$PIN" ] || { echo 'REFUSE admission integrity pin missing'; exit 2; }
[ "$(awk 'NF {print $1; exit}' "$PIN")" = "$OLD_PIN" ] || { echo 'REFUSE admission integrity pin is not the old generation'; exit 2; }
[ "$(cur_of "$ADMISSION")" = "$OLD_ADMISSION" ] || { echo "REFUSE current admission sha=$(cur_of "$ADMISSION")"; exit 2; }
[ "$(cur_of "$CONFIG")" = "$OLD_CONFIG" ] || { echo "REFUSE current config sha=$(cur_of "$CONFIG")"; exit 2; }
[ "$(cur_of "$POLICY")" = "$OLD_POLICY" ] || { echo "REFUSE current policy sha=$(cur_of "$POLICY")"; exit 2; }
for name in cpa-admission.py cpa-admission.json cpa_policy.py; do
  [ -f "$STAGE/$name" ] || { echo "REFUSE staged file missing $name"; exit 2; }
done

# A restart drops in-flight upstream turns, so wait briefly for free lanes and
# refuse rather than cutting a live desktop turn. Only in-flight work is a
# refusal reason: the OAuth lane is currently carrying a stuck breaker state
# (cooldown_active with failure_streak 5) that this generation replaces, and the
# restart clears that in-memory state.
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
  sha256sum '__BACKUP__'/cpa-admission.json.old | cut -d' ' -f1 | grep -qx '__OLD_CONFIG__'
  sha256sum '__BACKUP__'/cpa_policy.py.old | cut -d' ' -f1 | grep -qx '__OLD_POLICY__'
  grep -qx '__OLD_PIN__' '__BACKUP__'/cpa-admission.sha256.old
  echo ROLLBACK_DRY_RUN_OK
  exit 0
fi
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || exit 75
cp -a '__BACKUP__'/cpa-admission.py.old /opt/cliproxyapi/cpa-admission.py
cp -a '__BACKUP__'/cpa-admission.json.old /opt/cliproxyapi/cpa-admission.json
cp -a '__BACKUP__'/cpa_policy.py.old /opt/cliproxyapi/cpa_policy.py
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
  cp -a "$BACKUP/cpa-admission.json.old" "$CONFIG"
  cp -a "$BACKUP/cpa_policy.py.old" "$POLICY"
  cp -a "$BACKUP/cpa-admission.sha256.old" "$PIN"
  systemctl restart cpa-admission.service || true
  sleep 2
  if curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz >/dev/null 2>&1; then
    echo 'ROLLBACK_OK'
  else
    echo 'ROLLBACK_FAILED'
  fi
}

install_one() {
  target=$1
  staged=$2
  expected=$3
  mode=$4
  cp "$staged" "$target.new"
  chown --reference="$target" "$target.new"
  chmod "$mode" "$target.new"
  mv -f "$target.new" "$target"
  got=$(cur_of "$target")
  [ "$got" = "$expected" ] || { echo "REFUSE post-install sha mismatch $target got=$got"; restore; exit 3; }
  echo "INSTALLED path=$target sha=$got mode=$mode"
}

install_one "$ADMISSION" "$STAGE/cpa-admission.py" "$NEW_ADMISSION" 755 || exit 3
install_one "$CONFIG" "$STAGE/cpa-admission.json" "$NEW_CONFIG" 644 || exit 3
install_one "$POLICY" "$STAGE/cpa_policy.py" "$NEW_POLICY" 644 || exit 3

# The pin is the ExecStartPre contract for the service, so it rotates in the
# same transaction and is verified before the restart.
printf '%s\n' "$NEW_PIN" > "$PIN.tmp"
chmod 644 "$PIN.tmp"
mv -f "$PIN.tmp" "$PIN"
[ "$(awk 'NF {print $1; exit}' "$PIN")" = "$NEW_PIN" ] || { echo 'REFUSE pin rotation mismatch'; restore; exit 3; }
"$CHECK" || { echo 'REFUSE integrity check after pin rotation'; restore; exit 3; }

if ! python3 "$POLICY" "$D/config.yaml" >/tmp/cpa-policy-modelscope.log 2>&1; then
  echo 'REFUSE semantic policy check failed'
  tail -n 5 /tmp/cpa-policy-modelscope.log
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

# The live generation must expose the per-model breaker, otherwise the
# projected script is not the generation that is running.
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
echo "DEPLOY_OK admission=$(cur_of "$ADMISSION") config=$(cur_of "$CONFIG") policy=$(cur_of "$POLICY") pin=$(awk 'NF {print $1; exit}' "$PIN") pid=$NEWPID backup=$BACKUP"
rm -rf "$STAGE"
echo 'STAGE_CLEANED'
'@

$txn = $txnTemplate.Replace('__STAGE__', $stageDir)
$txn = $txn.Replace('__BACKUP__', $backupDir)
$txn = $txn.Replace('__OLD_ADMISSION__', $oldAdmission)
$txn = $txn.Replace('__NEW_ADMISSION__', $newAdmission)
$txn = $txn.Replace('__OLD_CONFIG__', $oldConfig)
$txn = $txn.Replace('__NEW_CONFIG__', $newConfig)
$txn = $txn.Replace('__OLD_POLICY__', $oldPolicy)
$txn = $txn.Replace('__NEW_POLICY__', $newPolicy)
$txn = $txn.Replace('__OLD_PIN__', $oldPin)
$txn = $txn.Replace('__NEW_PIN__', $newPin)

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $txn
Write-Host "DRIVER_RESULT=PASS backup=$backupDir"

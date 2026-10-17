#requires -Version 7
# BWG-only reviewed OAuth risk deployment: admission + pin + policy + payload.
param(
  [string]$Profile = "bwg"
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") {
  throw "This admission deployment is restricted to the bwg profile."
}

$oldAdmission = "2c0394ee40a6be1725e7ea4748225138b15a06e9acc818a2885d8f589461c75c"
$newAdmission = "e2735c1d8b0e9ab3e1bfe1f7647d32bcd1a0ba604bd3286fb0f9661cf9cb3549"
$oldPin = "2c0394ee40a6be1725e7ea4748225138b15a06e9acc818a2885d8f589461c75c"
$newPin = "e2735c1d8b0e9ab3e1bfe1f7647d32bcd1a0ba604bd3286fb0f9661cf9cb3549"
$keepConfig = "fed4c0b5685e330a60bf3db3b89757cd0ed8631d724feb604193498689d8229f"
$keepPolicy = "464fb2c3c10577d8885d9f78b6a53b2c01d349117c0689ae8953c545e46e0739"

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
$backupDir = "/root/cpa-admission-oauth-risk-backup-$stamp"

Write-Host "== step 1: preflight probe =="
$probe = @'
set -Eeuo pipefail
D=/opt/cliproxyapi
echo "OLD_ADMISSION=$(sha256sum "$D/cpa-admission.py" | cut -d' ' -f1)"
echo "KEEP_CONFIG=$(sha256sum "$D/cpa-admission.json" | cut -d' ' -f1)"
echo "KEEP_POLICY=$(sha256sum "$D/cpa_policy.py" | cut -d' ' -f1)"
echo "LIVE_CONFIG=$(sha256sum "$D/config.yaml" | cut -d' ' -f1)"
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
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $probe -HardTimeoutSeconds 60

Write-Host "== step 2: stage new generation =="
$payload = Get-LfPayload "scripts/remote/cpa-admission.py"
$payloadPolicy = Get-LfPayload "scripts/remote/cpa_policy.py"
$newPolicy = "c312cca5af35f3e1a261ec19be772d429dd47727295b2d3b2ea86586fd6f7513"
$stage = @"
set -Eeuo pipefail
umask 077
mkdir -m 700 '$stageDir'
printf '%s' '$payloadPolicy' | base64 -d > '$stageDir/cpa_policy.py'
[ "`$(sha256sum '$stageDir/cpa_policy.py' | cut -d' ' -f1)" = '$newPolicy' ]
printf '%s' '$payload' > '$stageDir/cpa-admission.py.b64'
base64 -d '$stageDir/cpa-admission.py.b64' > '$stageDir/cpa-admission.py'
rm -f '$stageDir/cpa-admission.py.b64'
chmod 600 '$stageDir/cpa-admission.py'
GOT=`$(sha256sum '$stageDir/cpa-admission.py' | cut -d' ' -f1)
[ "`$GOT" = '$newAdmission' ] || { echo "REFUSE staged sha mismatch got=`$GOT"; exit 2; }
echo "STAGED=cpa-admission.py sha=`$GOT"
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $stage -HardTimeoutSeconds 60

Write-Host "== step 3: py_compile staged generation =="
$compileCheck = @'
set -Eeuo pipefail
python3 -m py_compile '__STAGEDIR__/cpa-admission.py' && echo 'COMPILE=cpa-admission.py ok'
'@
$compileCheck = $compileCheck.Replace('__STAGEDIR__', $stageDir)
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $compileCheck -HardTimeoutSeconds 60

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
NEW_POLICY='__NEW_POLICY__'
OLD_PIN='__OLD_PIN__'
NEW_PIN='__NEW_PIN__'

cur_of() { sha256sum "$1" | cut -d' ' -f1; }

# Preconditions: the deployed generation is exactly the one this transaction
# was reviewed against, and the untouched files still match their reviewed
# hashes; all four changed files are backed up and restored together.
[ -x "$CHECK" ] && [ -f "$DROPIN" ] || { echo 'REFUSE admission integrity guard missing'; exit 2; }
[ -f "$PIN" ] || { echo 'REFUSE admission integrity pin missing'; exit 2; }
[ "$(awk 'NF {print $1; exit}' "$PIN")" = "$OLD_PIN" ] || { echo 'REFUSE pin is not the old generation'; exit 2; }
[ "$(cur_of "$ADMISSION")" = "$OLD_ADMISSION" ] || { echo "REFUSE current admission sha=$(cur_of "$ADMISSION")"; exit 2; }
[ "$(cur_of "$CONFIG")" = "$KEEP_CONFIG" ] || { echo 'REFUSE config is not the reviewed keep-generation'; exit 2; }
[ "$(cur_of "$POLICY")" = "$KEEP_POLICY" ] || { echo 'REFUSE policy is not the reviewed keep-generation'; exit 2; }
[ "$(cur_of "$D/config.yaml")" = "30a4f62699bbfbf828683c8d93686510c5f2bdfb4f2b7f5d3d47590f5e5ff8b8" ] || { echo "REFUSE live config drift"; exit 2; }
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
cp -a "$D/config.yaml" "$BACKUP/config.yaml.old"
sha256sum "$BACKUP"/*.old > "$BACKUP/backup.sha256"

cp "$D/cpa_provider_routes.json" "$STAGE/cpa_provider_routes.json"
cp "$CONFIG" "$STAGE/cpa-admission.json"
python3 - "$STAGE" "$D/config.yaml" <<'PY'
import runpy, sys
from pathlib import Path
import yaml
stage = Path(sys.argv[1])
policy = runpy.run_path(str(stage / "cpa_policy.py"))
config = yaml.safe_load(Path(sys.argv[2]).read_text())
config.setdefault("requests", {})["payload"] = policy["EXPECTED_PAYLOAD_TOOL_RULES"]
issues = policy["validate_config"](config)
if issues:
    raise SystemExit("REFUSE candidate semantic policy: " + "; ".join(issues))
(stage / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
print("CANDIDATE_POLICY=OK")
PY

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
  sha256sum -c '__BACKUP__'/backup.sha256 --status
  echo ROLLBACK_DRY_RUN_OK
  exit 0
fi
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || exit 75
D=/opt/cliproxyapi
BACKUP='__BACKUP__'
ADMISSION="$D/cpa-admission.py"
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
POLICY="$D/cpa_policy.py"
restore() {
  local rollback_failed=0
  cp -a "$BACKUP/cpa-admission.py.old" "$ADMISSION" || rollback_failed=1
  cp -a "$BACKUP/cpa-admission.sha256.old" "$PIN" || rollback_failed=1
  cp -a "$BACKUP/cpa_policy.py.old" "$POLICY" || rollback_failed=1
  cat "$BACKUP/config.yaml.old" > "$D/config.yaml" || rollback_failed=1
  cmp -s "$BACKUP/cpa-admission.py.old" "$ADMISSION" || rollback_failed=1
  cmp -s "$BACKUP/cpa-admission.sha256.old" "$PIN" || rollback_failed=1
  cmp -s "$BACKUP/cpa_policy.py.old" "$POLICY" || rollback_failed=1
  cmp -s "$BACKUP/config.yaml.old" "$D/config.yaml" || rollback_failed=1
  timeout 45s docker restart cli-proxy-api >/dev/null || rollback_failed=1
  timeout 30s systemctl restart cpa-admission.service || rollback_failed=1
  sleep 2
  curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz >/dev/null 2>&1 || rollback_failed=1
  if [ "$rollback_failed" = 0 ]; then
    echo 'ROLLBACK_OK'
  else
    echo 'ROLLBACK_FAILED'
  fi
  return "$rollback_failed"
}

restore
EOF
chmod 700 "$BACKUP/rollback.sh"
bash -n "$BACKUP/rollback.sh" || { echo 'REFUSE rollback syntax'; exit 2; }
bash "$BACKUP/rollback.sh" --dry-run || { echo 'REFUSE rollback preflight'; exit 2; }
echo "BACKUP_DIR=$BACKUP"

restore() {
  local rollback_failed=0
  cp -a "$BACKUP/cpa-admission.py.old" "$ADMISSION" || rollback_failed=1
  cp -a "$BACKUP/cpa-admission.sha256.old" "$PIN" || rollback_failed=1
  cp -a "$BACKUP/cpa_policy.py.old" "$POLICY" || rollback_failed=1
  cat "$BACKUP/config.yaml.old" > "$D/config.yaml" || rollback_failed=1
  cmp -s "$BACKUP/cpa-admission.py.old" "$ADMISSION" || rollback_failed=1
  cmp -s "$BACKUP/cpa-admission.sha256.old" "$PIN" || rollback_failed=1
  cmp -s "$BACKUP/cpa_policy.py.old" "$POLICY" || rollback_failed=1
  cmp -s "$BACKUP/config.yaml.old" "$D/config.yaml" || rollback_failed=1
  timeout 45s docker restart cli-proxy-api >/dev/null || rollback_failed=1
  timeout 30s systemctl restart cpa-admission.service || rollback_failed=1
  sleep 2
  curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz >/dev/null 2>&1 || rollback_failed=1
  if [ "$rollback_failed" = 0 ]; then
    echo 'ROLLBACK_OK'
  else
    echo 'ROLLBACK_FAILED'
  fi
  return "$rollback_failed"
}

trap 'rc=$?; trap - EXIT; if [ "$rc" -ne 0 ]; then restore || true; fi; exit "$rc"' EXIT
target=$ADMISSION
cp "$STAGE/cpa-admission.py" "$target.new"
chown --reference="$target" "$target.new"
chmod 755 "$target.new"
mv -f "$target.new" "$target"
got=$(cur_of "$target")
[ "$got" = "$NEW_ADMISSION" ] || { echo "REFUSE post-install sha mismatch got=$got"; exit 3; }
echo "INSTALLED path=$target sha=$got mode=755"

cp "$STAGE/cpa_policy.py" "$POLICY.new"
chown --reference="$POLICY" "$POLICY.new"
chmod --reference="$POLICY" "$POLICY.new"
mv -f "$POLICY.new" "$POLICY"
[ "$(cur_of "$POLICY")" = "$NEW_POLICY" ]
cat "$STAGE/config.yaml" > "$D/config.yaml"

# The pin is the ExecStartPre contract for the service, so it rotates in the
# same transaction and is verified before the restart.
printf '%s\n' "$NEW_PIN" > "$PIN.tmp"
chmod 644 "$PIN.tmp"
mv -f "$PIN.tmp" "$PIN"
[ "$(awk 'NF {print $1; exit}' "$PIN")" = "$NEW_PIN" ] || { echo 'REFUSE pin rotation mismatch'; exit 3; }
"$CHECK" || { echo 'REFUSE integrity check after pin rotation'; exit 3; }

timeout 45s docker restart cli-proxy-api >/dev/null
if ! python3 "$POLICY" "$D/config.yaml" >/tmp/cpa-policy-oauth-risk.log 2>&1; then
  echo 'REFUSE semantic policy check failed'
  tail -n 5 /tmp/cpa-policy-oauth-risk.log
  exit 3
fi
echo 'SEMANTIC_POLICY=OK'

READY=0
for _ in $(seq 1 5); do
  if timeout 10s python3 "$D/cpa-health.py" readiness >/dev/null 2>&1; then READY=1; break; fi
  sleep 1
done
[ "$READY" = 1 ] || { echo 'REFUSE CPA readiness after restart'; exit 3; }
echo 'CPA_READINESS=OK'

if ! timeout 30s systemctl restart cpa-admission.service; then
  echo 'RESTART_FAILED'
  exit 3
fi
OK=0
for _ in $(seq 1 20); do
  if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8318/healthz >/dev/null 2>&1; then OK=1; break; fi
  sleep 1
done
if [ "$OK" != 1 ]; then
  echo 'HEALTHZ_TIMEOUT'
  exit 3
fi

# The live generation must still expose the per-model breaker fields.
if ! curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
assert h.get("status") == "ok", "status"
lanes = h.get("lanes") or {}
assert set(lanes) == {"chatgpt-oauth", "zhipu-coding-plan", "antigravity-oauth", "claude-oauth"}, lanes
for name, lane in lanes.items():
    s = lane.get("state") or {}
    assert s.get("cooldown_scope") in {"lane", "model", "none"}, name
    assert isinstance(s.get("model_cooldowns"), dict), name
print("GENERATION_FIELDS=ok")
'; then
  echo 'REFUSE live generation missing model-scope fields'
  exit 3
fi

NEWPID=$(systemctl show cpa-admission.service -p MainPID --value)
echo "DEPLOY_OK admission=$(cur_of "$ADMISSION") pin=$(awk 'NF {print $1; exit}' "$PIN") pid=$NEWPID backup=$BACKUP"
trap - EXIT
[ "$STAGE" = "__STAGE__" ] && [ "${STAGE#/root/.cpa-adm-stage-}" != "$STAGE" ]
rm -rf -- "$STAGE"
echo 'STAGE_CLEANED'
'@

$txn = $txnTemplate.Replace('__STAGE__', $stageDir)
$txn = $txn.Replace('__BACKUP__', $backupDir)
$txn = $txn.Replace('__OLD_ADMISSION__', $oldAdmission)
$txn = $txn.Replace('__NEW_ADMISSION__', $newAdmission)
$txn = $txn.Replace('__KEEP_CONFIG__', $keepConfig)
$txn = $txn.Replace('__KEEP_POLICY__', $keepPolicy)
$txn = $txn.Replace('__NEW_POLICY__', $newPolicy)
$txn = $txn.Replace('__OLD_PIN__', $oldPin)
$txn = $txn.Replace('__NEW_PIN__', $newPin)

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $txn -IdleTimeoutSeconds 120 -HardTimeoutSeconds 360
Write-Host "DRIVER_RESULT=PASS backup=$backupDir"

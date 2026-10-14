#requires -Version 7
# 2026-10-14 BWG admission generation rotation (antigravity): add the
# antigravity-oauth shared subscription lane (Google AI Pro / Gemini). Five
# files change (cpa-admission.py + its integrity pin + cpa-admission.json +
# cpa_policy.py + cpa_provider_routes.json) and config.yaml receives the
# derived api-keys.codex[*].excluded-models appends in the same transaction,
# because the new policy refuses the old config the moment the new manifest
# lands. Same reviewed transaction shape as
# outputs/deploy-admission-modelstreak-20261010.ps1: stage, verify, swap under
# the shared maintenance lock with backup + rollback rails, restart, verify.
param(
  [string]$Profile = "bwg"
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") {
  throw "This admission deployment is restricted to the bwg profile."
}

$oldAdmission = "fbaeb6314c083c410059aa7c3cf84c5046234666a4fe8e00b7613ffcb552748c"
$newAdmission = "efda0da9325a181c77c5845a9c4a361179227996bcecc86cd2e3a6f848a29219"
$oldPin = "fbaeb6314c083c410059aa7c3cf84c5046234666a4fe8e00b7613ffcb552748c"
$newPin = "efda0da9325a181c77c5845a9c4a361179227996bcecc86cd2e3a6f848a29219"
$oldConfig = "97ec5a5050041fe720fdf57672c24767f27b6c1a04bee415f3e746e717b80c20"
$oldPolicy = "7523f049a642e269073789bee76878bba00e86e7a0bfaaf75ba0937980c03268"
$oldManifest = "8dc1c356b6b8159f159cae5e143b9f9560d72fb5143905f137fd4104ef8d04e5"
$newConfigJson = "d630171b3ba6653446304c8f3a1cf1c3dd69af6dbbe8639b745ccfdb1f5c0db3"
$newPolicy = "241027a5de26ec70cd706182f68f15b45c91ba5ccc2830b7c7c079eb22c6efe8"
$newManifest = "43a1aeddaddae1e650b0163e4eff0df5995206c0046074148b5e63e743cb7fe4"

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
$backupDir = "/root/cpa-admission-antigravity-backup-$stamp"

Write-Host "== step 1: preflight probe =="
$probe = @'
set -Eeuo pipefail
D=/opt/cliproxyapi
echo "OLD_ADMISSION=$(sha256sum "$D/cpa-admission.py" | cut -d' ' -f1)"
echo "OLD_CONFIG=$(sha256sum "$D/cpa-admission.json" | cut -d' ' -f1)"
echo "OLD_POLICY=$(sha256sum "$D/cpa_policy.py" | cut -d' ' -f1)"
echo "OLD_MANIFEST=$(sha256sum "$D/cpa_provider_routes.json" | cut -d' ' -f1)"
echo "OLD_PIN=$(awk 'NF {print $1; exit}' /etc/vps-ssh-launcher/cpa-admission.sha256)"
echo "SVC=$(systemctl is-active cpa-admission.service)"
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
print("STATUS=" + str(h.get("status")))
print("LANES", sorted(h.get("lanes", {}).keys()))
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
$payloadAdmission = Get-LfPayload "scripts/remote/cpa-admission.py"
$payloadConfigJson = Get-LfPayload "scripts/remote/cpa-admission.json"
$payloadPolicy = Get-LfPayload "scripts/remote/cpa_policy.py"
$payloadManifest = Get-LfPayload "scripts/remote/cpa_provider_routes.json"
$stage = @"
set -Eeuo pipefail
umask 077
mkdir -m 700 -p '$stageDir'
printf '%s' '$payloadAdmission' > '$stageDir/cpa-admission.py.b64'
base64 -d '$stageDir/cpa-admission.py.b64' > '$stageDir/cpa-admission.py'
rm -f '$stageDir/cpa-admission.py.b64'
printf '%s' '$payloadConfigJson' > '$stageDir/cpa-admission.json.b64'
base64 -d '$stageDir/cpa-admission.json.b64' > '$stageDir/cpa-admission.json'
rm -f '$stageDir/cpa-admission.json.b64'
printf '%s' '$payloadPolicy' > '$stageDir/cpa_policy.py.b64'
base64 -d '$stageDir/cpa_policy.py.b64' > '$stageDir/cpa_policy.py'
rm -f '$stageDir/cpa_policy.py.b64'
printf '%s' '$payloadManifest' > '$stageDir/cpa_provider_routes.json.b64'
base64 -d '$stageDir/cpa_provider_routes.json.b64' > '$stageDir/cpa_provider_routes.json'
rm -f '$stageDir/cpa_provider_routes.json.b64'
chmod 600 '$stageDir'/*
GOT=`$(sha256sum '$stageDir'/cpa-admission.py | cut -d' ' -f1)
[ "`$GOT" = '$newAdmission' ] || { echo "REFUSE staged admission mismatch got=`$GOT"; exit 2; }
echo "STAGED cpa-admission.py sha=`$GOT"
GOT=`$(sha256sum '$stageDir'/cpa-admission.json | cut -d' ' -f1)
[ "`$GOT" = '$newConfigJson' ] || { echo "REFUSE staged admission-json mismatch got=`$GOT"; exit 2; }
echo "STAGED cpa-admission.json sha=`$GOT"
GOT=`$(sha256sum '$stageDir'/cpa_policy.py | cut -d' ' -f1)
[ "`$GOT" = '$newPolicy' ] || { echo "REFUSE staged policy mismatch got=`$GOT"; exit 2; }
echo "STAGED cpa_policy.py sha=`$GOT"
GOT=`$(sha256sum '$stageDir'/cpa_provider_routes.json | cut -d' ' -f1)
[ "`$GOT" = '$newManifest' ] || { echo "REFUSE staged manifest mismatch got=`$GOT"; exit 2; }
echo "STAGED cpa_provider_routes.json sha=`$GOT"
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $stage

Write-Host "== step 3: compile and json checks on staged generation =="
$compileCheck = @'
set -Eeuo pipefail
python3 -m py_compile '__STAGEDIR__/cpa-admission.py' && echo 'COMPILE=cpa-admission.py ok'
python3 -m py_compile '__STAGEDIR__/cpa_policy.py' && echo 'COMPILE=cpa_policy.py ok'
python3 -c "import json;json.load(open('__STAGEDIR__/cpa-admission.json'))" && echo 'JSON=cpa-admission.json ok'
python3 -c "import json;json.load(open('__STAGEDIR__/cpa_provider_routes.json'))" && echo 'JSON=cpa_provider_routes.json ok'
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
MANIFEST="$D/cpa_provider_routes.json"
LIVE_CONFIG="$D/config.yaml"

OLD_ADMISSION='__OLD_ADMISSION__'
NEW_ADMISSION='__NEW_ADMISSION__'
OLD_CONFIG='__OLD_CONFIG__'
NEW_CONFIG='__NEW_CONFIG__'
OLD_POLICY='__OLD_POLICY__'
NEW_POLICY='__NEW_POLICY__'
OLD_MANIFEST='__OLD_MANIFEST__'
NEW_MANIFEST='__NEW_MANIFEST__'
OLD_PIN='__OLD_PIN__'
NEW_PIN='__NEW_PIN__'

cur_of() { sha256sum "$1" | cut -d' ' -f1; }

# Preconditions: the deployed generation is exactly the one this transaction
# was reviewed against.
[ -x "$CHECK" ] && [ -f "$DROPIN" ] || { echo 'REFUSE admission integrity guard missing'; exit 2; }
[ -f "$PIN" ] || { echo 'REFUSE admission integrity pin missing'; exit 2; }
[ "$(awk 'NF {print $1; exit}' "$PIN")" = "$OLD_PIN" ] || { echo 'REFUSE pin is not the old generation'; exit 2; }
[ "$(cur_of "$ADMISSION")" = "$OLD_ADMISSION" ] || { echo "REFUSE current admission sha=$(cur_of "$ADMISSION")"; exit 2; }
[ "$(cur_of "$CONFIG")" = "$OLD_CONFIG" ] || { echo "REFUSE current admission config sha=$(cur_of "$CONFIG")"; exit 2; }
[ "$(cur_of "$POLICY")" = "$OLD_POLICY" ] || { echo "REFUSE current policy sha=$(cur_of "$POLICY")"; exit 2; }
[ "$(cur_of "$MANIFEST")" = "$OLD_MANIFEST" ] || { echo "REFUSE current manifest sha=$(cur_of "$MANIFEST")"; exit 2; }
for f in cpa-admission.py cpa-admission.json cpa_policy.py cpa_provider_routes.json; do
  [ -f "$STAGE/$f" ] || { echo "REFUSE staged $f missing"; exit 2; }
done

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
cp -a "$MANIFEST" "$BACKUP/cpa_provider_routes.json.old"
cp -a "$LIVE_CONFIG" "$BACKUP/config.yaml.old"
cp -a "$PIN" "$BACKUP/cpa-admission.sha256.old"
sha256sum "$BACKUP"/*.old > "$BACKUP/backup.sha256"

restore() {
  cp -a "$BACKUP/cpa-admission.py.old" "$ADMISSION"
  cp -a "$BACKUP/cpa-admission.json.old" "$CONFIG"
  cp -a "$BACKUP/cpa_policy.py.old" "$POLICY"
  cp -a "$BACKUP/cpa_provider_routes.json.old" "$MANIFEST"
  cp -a "$BACKUP/config.yaml.old" "$LIVE_CONFIG"
  cp -a "$BACKUP/cpa-admission.sha256.old" "$PIN"
  systemctl restart cpa-admission.service || true
  sleep 2
  if curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz >/dev/null 2>&1; then
    echo 'ROLLBACK_OK'
  else
    echo 'ROLLBACK_FAILED'
  fi
}

# Rotate the four D=/ files atomically (temp + mv), preserving owner/mode.
install_file() {
  src="$1"; dst="$2"
  cp "$src" "$dst.new"
  chown --reference="$dst" "$dst.new"
  chmod --reference="$dst" "$dst.new"
  mv -f "$dst.new" "$dst"
}
install_file "$STAGE/cpa-admission.py" "$ADMISSION"
install_file "$STAGE/cpa-admission.json" "$CONFIG"
install_file "$STAGE/cpa_policy.py" "$POLICY"
install_file "$STAGE/cpa_provider_routes.json" "$MANIFEST"
[ "$(cur_of "$ADMISSION")" = "$NEW_ADMISSION" ] || { echo 'REFUSE post-install admission mismatch'; restore; exit 3; }
[ "$(cur_of "$CONFIG")" = "$NEW_CONFIG" ] || { echo 'REFUSE post-install config mismatch'; restore; exit 3; }
[ "$(cur_of "$POLICY")" = "$NEW_POLICY" ] || { echo 'REFUSE post-install policy mismatch'; restore; exit 3; }
[ "$(cur_of "$MANIFEST")" = "$NEW_MANIFEST" ] || { echo 'REFUSE post-install manifest mismatch'; restore; exit 3; }
echo "INSTALLED admission=$(cur_of "$ADMISSION") config=$(cur_of "$CONFIG") policy=$(cur_of "$POLICY") manifest=$(cur_of "$MANIFEST")"

# config.yaml: append the manifest's codex_api_key_exclusions to every
# api-keys.codex[*].excluded-models group, exactly the derivation -Apply
# performs, so the new policy's bare-route-overlap check passes against the
# live config in the same generation.
python3 - "$LIVE_CONFIG" <<'PY'
import sys
import tempfile
import yaml

path = sys.argv[1]
new_names = [
    "gemini-3-flash",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash-high",
    "claude-opus-4-6-thinking",
    "claude-sonnet-4-6",
    "gpt-oss-120b-medium",
]
cfg = yaml.safe_load(open(path, encoding="utf-8"))
api_keys = cfg.get("api-keys") if isinstance(cfg, dict) else None
codex = api_keys.get("codex") if isinstance(api_keys, dict) else None
if not isinstance(codex, list) or not codex:
    raise SystemExit("REFUSE api-keys.codex groups missing")
changed = 0
for group in codex:
    if not isinstance(group, dict):
        continue
    exclusions = group.get("excluded-models")
    if not isinstance(exclusions, list):
        exclusions = []
        group["excluded-models"] = exclusions
    normalized = {str(m).strip().lower() for m in exclusions}
    for name in new_names:
        if name not in normalized:
            exclusions.append(name)
            normalized.add(name)
            changed += 1
if changed == 0:
    print("CONFIG_PATCH=already-ok")
    raise SystemExit(0)
import os
fd, tmp = tempfile.mkstemp(prefix="config.yaml.txn-", dir=os.path.dirname(path) or ".")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    yaml.safe_dump(cfg, handle, sort_keys=False, allow_unicode=True)
os.replace(tmp, path)
os.chmod(path, 0o600)
print("CONFIG_PATCHED names=%d" % changed)
PY
if ! python3 - "$LIVE_CONFIG" <<'PY'
import sys
import yaml

cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
required = {
    "gemini-3-flash",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash-high",
    "claude-opus-4-6-thinking",
    "claude-sonnet-4-6",
    "gpt-oss-120b-medium",
}
codex = (cfg.get("api-keys") or {}).get("codex") or []
for group in codex:
    if not isinstance(group, dict):
        continue
    got = {str(m).strip().lower() for m in group.get("excluded-models", [])}
    if not required <= got:
        raise SystemExit("missing %s" % sorted(required - got))
print("CONFIG_VERIFY=ok")
PY
then
  echo 'REFUSE config patch verification failed'
  restore
  exit 3
fi

# The pin is the ExecStartPre contract for the service, so it rotates in the
# same transaction and is verified before the restart.
printf '%s\n' "$NEW_PIN" > "$PIN.tmp"
chmod 644 "$PIN.tmp"
mv -f "$PIN.tmp" "$PIN"
[ "$(awk 'NF {print $1; exit}' "$PIN")" = "$NEW_PIN" ] || { echo 'REFUSE pin rotation mismatch'; restore; exit 3; }
"$CHECK" || { echo 'REFUSE integrity check after pin rotation'; restore; exit 3; }

if ! python3 "$POLICY" "$LIVE_CONFIG" >/tmp/cpa-policy-antigravity.log 2>&1; then
  echo 'REFUSE semantic policy check failed'
  tail -n 8 /tmp/cpa-policy-antigravity.log
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

# The live generation must expose the three reviewed lanes and the per-model
# breaker fields.
if ! curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
assert h.get("status") == "ok", "status"
lanes = h.get("lanes") or {}
assert set(lanes) == {"chatgpt-oauth", "zhipu-coding-plan", "antigravity-oauth"}, lanes
for name, lane in lanes.items():
    s = lane.get("state") or {}
    assert s.get("cooldown_scope") in {"lane", "model", "none"}, name
    assert isinstance(s.get("model_cooldowns"), dict), name
print("GENERATION_FIELDS=ok")
'; then
  echo 'REFUSE live generation missing antigravity lane or model-scope fields'
  restore
  exit 3
fi

NEWPID=$(systemctl show cpa-admission.service -p MainPID --value)
echo "DEPLOY_OK admission=$(cur_of "$ADMISSION") pin=$(awk 'NF {print $1; exit}' "$PIN") policy=$(cur_of "$POLICY") manifest=$(cur_of "$MANIFEST") pid=$NEWPID backup=$BACKUP"
rm -rf "$STAGE"
echo 'STAGE_CLEANED'
'@

$txn = $txnTemplate.Replace('__STAGE__', $stageDir)
$txn = $txn.Replace('__BACKUP__', $backupDir)
$txn = $txn.Replace('__OLD_ADMISSION__', $oldAdmission)
$txn = $txn.Replace('__NEW_ADMISSION__', $newAdmission)
$txn = $txn.Replace('__OLD_CONFIG__', $oldConfig)
$txn = $txn.Replace('__NEW_CONFIG__', $newConfigJson)
$txn = $txn.Replace('__OLD_POLICY__', $oldPolicy)
$txn = $txn.Replace('__NEW_POLICY__', $newPolicy)
$txn = $txn.Replace('__OLD_MANIFEST__', $oldManifest)
$txn = $txn.Replace('__NEW_MANIFEST__', $newManifest)
$txn = $txn.Replace('__OLD_PIN__', $oldPin)
$txn = $txn.Replace('__NEW_PIN__', $newPin)

Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $txn
Write-Host "DRIVER_RESULT=PASS backup=$backupDir"

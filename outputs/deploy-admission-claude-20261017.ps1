#requires -Version 7
# 2026-10-17 BWG admission generation rotation (claude): add the claude-oauth
# shared subscription lane (Anthropic Pro, claims claude-opus-5-5 /
# claude-sonnet-5-5 / claude-haiku-5-5; 11 tombstones derived into
# oauth.excluded-models.claude) and retire the zen claude API-key route
# (claude_api_key_routes emptied; the projected api-keys.claude group and the
# three claude-haiku payload rules leave config.yaml in the same transaction,
# because the new policy refuses the old config the moment the new manifest
# lands). Same reviewed transaction shape as
# outputs/deploy-admission-antigravity-20261014.ps1: stage, verify, swap under
# the shared maintenance lock with backup + rollback rails, restart, verify.
param(
  [string]$Profile = "bwg"
)
$ErrorActionPreference = "Stop"
if ($Profile -cne "bwg") {
  throw "This admission deployment is restricted to the bwg profile."
}

$oldAdmission = "5af934a2e6d3880dd019864d2794d9256e9c75003d553950f26861f2b911884b"
$newAdmission = "f048dfde531b767f2cab9758b0056e7f7184e342d411876a0b4dca72cd30d39e"
$oldPin = "5af934a2e6d3880dd019864d2794d9256e9c75003d553950f26861f2b911884b"
$newPin = "f048dfde531b767f2cab9758b0056e7f7184e342d411876a0b4dca72cd30d39e"
$oldConfig = "d630171b3ba6653446304c8f3a1cf1c3dd69af6dbbe8639b745ccfdb1f5c0db3"
$oldPolicy = "4f80d02c940b92918f7fb810b1154b7e69345ac7487f5841bd8f1a642842d53c"
$oldManifest = "5376e8699ed8cd3f215de4e6207352f38542c41963597010574b3a9e8ecc1646"
$newConfigJson = "f8a9950354c26580e9470577ebed03ecf907e9fb50bf6e4b4c262120cb02f1ac"
$newPolicy = "30fef3bf5561f2f6a34b0f1062d91a5fae442a9b185f052c39fa9ee56b94de5d"
$newManifest = "791393599e97ccda65fd07be4cbf2aea2bcfde4e6b101ced4a2c7d846079bc9d"

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
$backupDir = "/root/cpa-admission-claude-backup-$stamp"

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

# config.yaml: three text-level edits in one generation —
#   (1) drop the three claude-haiku payload rules (zen route retired),
#   (2) drop the api-keys.claude group (same reason),
#   (3) replace the fail-close wildcard with the 11 derived tombstones.
python3 - "$LIVE_CONFIG" <<'PY'
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").read().splitlines(keepends=False)

# (1) remove claude-haiku payload rule entries: an override entry that names
# claude-haiku-5-5 with a thinking exist-gate, and two filter entries naming
# claude-haiku-5-5. Entries live at 4-space indent inside payload lists.
out = []
i = 0
removed = []
while i < len(lines):
    l = lines[i]
    if (
        l.rstrip() == "    - models:"
        and i + 1 < len(lines)
        and lines[i + 1].strip() == "- name: claude-haiku-5-5"
    ):
        j = i + 1
        while j < len(lines) and (
            lines[j].startswith("      ") or lines[j].strip() == "- name: claude-haiku-5-5"
        ):
            j += 1
        removed.append("payload:" + lines[i + 1].strip())
        i = j
        continue
    out.append(l)
    i += 1
if len(removed) != 3:
    raise SystemExit("REFUSE expected 3 claude payload rules, matched %d" % len(removed))

# (2) remove the api-keys.claude group block (2-space indent key at top of its
# own line, ends at the next 2-space-indent sibling or top-level key).
out2 = []
i = 0
removed_claude_group = 0
while i < len(out):
    l = out[i]
    if l.rstrip() == "  claude:" and any(
        x.strip().startswith("base-url: https://opencode.ai/zen/go") for x in out[i + 1 : i + 4]
    ):
        j = i + 1
        while j < len(out) and (
            out[j].startswith("    ")
            or out[j].startswith("  - ")
            or out[j].strip() == ""
        ):
            j += 1
        removed_claude_group = j - i
        i = j
        continue
    out2.append(l)
    i += 1
if removed_claude_group == 0:
    raise SystemExit("REFUSE api-keys.claude zen group not found")

# (3) swap the fail-close wildcard for the derived tombstones.
out3 = []
i = 0
swapped = 0
while i < len(out2):
    l = out2[i]
    if l.strip() == "- claude-*":
        indent = l[: len(l) - len(l.lstrip())]
        for name in [
            "claude-fable-5",
            "claude-fable-5-1",
            "claude-haiku-4-5-20251001",
            "claude-opus-4-5-20251101",
            "claude-opus-4-6",
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-opus-5",
            "claude-sonnet-4-5-20250929",
            "claude-sonnet-4-6",
            "claude-sonnet-5",
        ]:
            out3.append(indent + "- " + name)
        swapped += 1
        i += 1
        continue
    out3.append(l)
    i += 1
if swapped != 1:
    raise SystemExit("REFUSE expected exactly one claude-* wildcard, found %d" % swapped)

open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out3) + "\n")
print("CONFIG_PATCHED payload_rules=%d claude_group_lines=%d tombstones=11" % (len(removed), removed_claude_group))
PY
if ! python3 - "$LIVE_CONFIG" <<'PY'
import sys, yaml

cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
payload = (cfg.get("requests") or {}).get("payload") or {}
blob = yaml.safe_dump(payload, sort_keys=False)
if "claude-haiku-5-5" in blob:
    raise SystemExit("claude-haiku payload rules still present")
if "claude" in (cfg.get("api-keys") or {}):
    raise SystemExit("api-keys.claude still present")
got = ((cfg.get("oauth") or {}).get("excluded-models") or {}).get("claude") or []
required = {
    "claude-fable-5", "claude-fable-5-1", "claude-haiku-4-5-20251001",
    "claude-opus-4-5-20251101", "claude-opus-4-6", "claude-opus-4-7",
    "claude-opus-4-8", "claude-opus-5", "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-6", "claude-sonnet-5",
}
if set(got) != required or len(got) != 11:
    raise SystemExit("claude exclusions mismatch: %s" % sorted(got))
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

if ! python3 "$POLICY" "$LIVE_CONFIG" >/tmp/cpa-policy-claude.log 2>&1; then
  echo 'REFUSE semantic policy check failed'
  tail -n 8 /tmp/cpa-policy-claude.log
  restore
  exit 3
fi
echo 'SEMANTIC_POLICY=OK'

if ! systemctl restart cpa-admission.service; then
  echo 'RESTART_FAILED'
  restore
  exit 3
fi
sleep 2
if ! curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8318/healthz | python3 -c '
import json, sys
h = json.load(sys.stdin)
lanes = sorted(h.get("lanes", {}).keys())
assert lanes == ["antigravity-oauth", "chatgpt-oauth", "claude-oauth", "zhipu-coding-plan"], lanes
print("ADMISSION_LANES", lanes)
'; then
  echo 'REFUSE admission healthz after restart'
  restore
  exit 3
fi

# Reload the gateway generation (payload + api-keys changes) and verify the
# new claude aliases are live in the loopback catalog.
if ! docker restart cli-proxy-api >/dev/null; then
  echo 'DOCKER_RESTART_FAILED'
  restore
  exit 3
fi
sleep 4
python3 - "$LIVE_CONFIG" <<'PY'
import json, sys, urllib.request
import yaml
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
key = cfg["api-keys"]["openai-compatibility"][0]["keys"][0]["api-key"]
req = urllib.request.Request(
    "http://127.0.0.1:8317/v1/models",
    headers={"Authorization": "Bearer " + key},
)
with urllib.request.urlopen(req, timeout=15) as r:
    ids = sorted(m["id"] for m in json.load(r).get("data", []))
claude = [m for m in ids if m.startswith("claude-")]
assert sorted(claude) == [
    "claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-5-5"
], claude
print("CATALOG_CLAUDE", claude)
print("CATALOG_TOTAL", len(ids))
PY
if ! python3 "$D/cpa-health.py" readiness; then
  echo 'REFUSE cpa readiness after reload'
  restore
  exit 3
fi
echo "DEPLOY_OK backup=$BACKUP"
'@
$txn = $txnTemplate.Replace('__STAGE__', $stageDir).Replace('__BACKUP__', $backupDir)
$txn = $txn.Replace('__OLD_ADMISSION__', $oldAdmission).Replace('__NEW_ADMISSION__', $newAdmission)
$txn = $txn.Replace('__OLD_CONFIG__', $oldConfig).Replace('__NEW_CONFIG__', $newConfigJson)
$txn = $txn.Replace('__OLD_POLICY__', $oldPolicy).Replace('__NEW_POLICY__', $newPolicy)
$txn = $txn.Replace('__OLD_MANIFEST__', $oldManifest).Replace('__NEW_MANIFEST__', $newManifest)
$txn = $txn.Replace('__OLD_PIN__', $oldPin).Replace('__NEW_PIN__', $newPin)

# The swap idles through lane-drain sleeps and service restarts, past the
# 60s transport idle timeout. Push the transaction as a script file and run
# it detached (setsid); poll the log from short-lived sessions instead.
$txnB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($txn))
$launch = @"
set -Eeuo pipefail
printf '%s' '$txnB64' > /root/.cpa-claude-txn.b64
base64 -d /root/.cpa-claude-txn.b64 > /root/.cpa-claude-txn.sh
rm -f /root/.cpa-claude-txn.b64
chmod 700 /root/.cpa-claude-txn.sh
rm -f /root/.cpa-claude-txn.log
setsid nohup bash /root/.cpa-claude-txn.sh > /root/.cpa-claude-txn.log 2>&1 < /dev/null &
echo "LAUNCHED pid=`$!"
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $launch

Write-Host "== step 4b: poll transaction log =="
$terminal = $false
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 5
  $log = Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command "cat /root/.cpa-claude-txn.log 2>/dev/null; echo POLL_TICK"
  $logText = ($log | Out-String)
  if ($logText -match 'DEPLOY_OK') { $terminal = $true; Write-Host $logText; break }
  if ($logText -match 'ROLLBACK_OK|ROLLBACK_FAILED|REFUSE') { Write-Host $logText; break }
}
if (-not $terminal) {
  throw "Transaction did not reach DEPLOY_OK within the polling window; inspect /root/.cpa-claude-txn.log on bwg before any retry."
}

Write-Host "== step 5: cleanup staging directory =="
$cleanup2 = @"
set -Eeuo pipefail
rm -rf '$stageDir' 2>/dev/null || true
echo 'STAGE_CLEANED=ok'
"@
Invoke-LauncherRemoteCommand -Python $python -ProjectRoot $repoRoot -Config $config -Profile $Profile -Command $cleanup2

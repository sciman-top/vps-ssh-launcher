#requires -Version 7
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg")]
  [string]$Profile,
  [switch]$Apply,
  [switch]$PinClaudeWebIPv4,
  [string]$GeneratorSha256,
  [string]$RemoteApplyScript = "/etc/v2ray-agent/reapply-google-ipv4-routing.sh",
  [string]$RemoteApplySha256
)

$ErrorActionPreference = "Stop"
$Profile = $Profile.Trim()
if ($Profile -ne "bwg") {
  throw "Google IPv4 routing is an Xray-only bwg operation; refusing Profile=$Profile."
}
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")
. (Join-Path $PSScriptRoot "lib\proxy_google_ipv4_domains.ps1")

# The read lane and the post-apply verification must assert exactly this
# host's shared per-profile list, so both remote payloads receive it as a
# pre-rendered literal instead of a second hand-copied python list.
$profileDomains = @($GoogleIpv4DomainsByProfile[$Profile])
if ($profileDomains.Count -lt 1) {
  throw "Profile $Profile has no Google IPv4 domain list in scripts/lib/proxy_google_ipv4_domains.ps1."
}
$expectedDomainCount = $profileDomains.Count
$expectedDomainListBlock = ($profileDomains | ForEach-Object { '    "domain:' + $_ + '",' }) -join "`n"

function Assert-SafeRemoteApplyScript {
  param([string]$Path)

  if (-not $Path.StartsWith("/")) {
    throw "RemoteApplyScript must be an absolute Linux path."
  }
  if ($Path -match "(^|/)\.\.(/|$)") {
    throw "RemoteApplyScript must not contain parent-directory segments."
  }
  if ($Path -notmatch "^[A-Za-z0-9_./-]+$") {
    throw "RemoteApplyScript contains unsupported shell characters."
  }

  return $Path
}

Initialize-WindowsProcessEnvironment
$py = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher

$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config

if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}
if ($Apply -and $RemoteApplySha256 -notmatch '^(?i:[0-9a-f]{64})$') {
  throw "-Apply requires a 64-hex -RemoteApplySha256 pin for the delegated routing script."
}
if ($PinClaudeWebIPv4 -and -not $Apply) {
  throw "-PinClaudeWebIPv4 requires -Apply."
}
if ($PinClaudeWebIPv4 -and $GeneratorSha256 -notmatch '^(?i:[0-9a-f]{64})$') {
  throw "-PinClaudeWebIPv4 requires a 64-hex -GeneratorSha256 pre-image pin."
}
if (-not $PinClaudeWebIPv4 -and $GeneratorSha256) {
  throw "-GeneratorSha256 is only valid with -PinClaudeWebIPv4."
}
$expectedRemoteApplySha256 = if ($RemoteApplySha256) {
  $RemoteApplySha256.ToLowerInvariant()
} else {
  ""
}
$expectedGeneratorSha256 = if ($GeneratorSha256) {
  $GeneratorSha256.ToLowerInvariant()
} else {
  ""
}

$checkCommand = @'
set -e
echo "==xray-version=="
if [ -x /etc/v2ray-agent/xray/xray ]; then
  /etc/v2ray-agent/xray/xray version | head -1 || true
else
  echo xray-missing
fi
echo "==service=="
systemctl is-active xray || true
echo "==google-ipv4-dropin=="
cat /etc/systemd/system/xray.service.d/20-google-ipv4-routing.conf 2>/dev/null || echo missing
echo "==google-ipv4-scripts=="
ls -l /etc/v2ray-agent/apply-google-ipv4-routing-config.sh /etc/v2ray-agent/reapply-google-ipv4-routing.sh 2>/dev/null || true
echo "==google-ipv4-routing=="
for config_file in /etc/v2ray-agent/xray/conf/09_routing.json /etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json; do
  if grep -q 'gemini\|google_ipv4_out\|ForceIPv4\|googleapis\|gstatic' "$config_file" 2>/dev/null; then
    echo "$config_file: marker-present"
  else
    echo "$config_file: marker-missing"
  fi
done
echo "==google-claude-ipv4-contract=="
python3 - <<'PY'
import json
from pathlib import Path

expected = [
__EXPECTED_DOMAIN_LIST__
]
route_data = json.loads(Path("/etc/v2ray-agent/xray/conf/09_routing.json").read_text(encoding="utf-8"))
outbound_data = json.loads(Path("/etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json").read_text(encoding="utf-8"))
routes = []
forced_outbounds = []

def walk_routes(value):
    if isinstance(value, dict):
        if value.get("outboundTag") == "google_ipv4_out" and isinstance(value.get("domain"), list):
            routes.append(value["domain"])
        for child in value.values():
            walk_routes(child)
    elif isinstance(value, list):
        for child in value:
            walk_routes(child)

def walk_outbounds(value):
    if isinstance(value, dict):
        settings = value.get("settings")
        if (
            value.get("tag") == "google_ipv4_out"
            and value.get("protocol") == "freedom"
            and isinstance(settings, dict)
            and settings.get("domainStrategy") == "ForceIPv4"
        ):
            forced_outbounds.append(value["tag"])
        for child in value.values():
            walk_outbounds(child)
    elif isinstance(value, list):
        for child in value:
            walk_outbounds(child)

walk_routes(route_data)
walk_outbounds(outbound_data)
if routes != [expected] or forced_outbounds != ["google_ipv4_out"]:
    raise SystemExit("ROUTE_CONTRACT=FAIL")
print("ROUTE_CONTRACT=GOOGLE_AND_CLAUDE_IPV4_ONLY_DOMAINS")
PY
echo "==xray-config-test=="
if [ -x /etc/v2ray-agent/xray/xray ] && [ -d /etc/v2ray-agent/xray/conf ]; then
  if config_test_output="$(/etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf 2>&1)"; then
    echo config-ok
  else
    printf '%s\n' "$config_test_output"
    exit 1
  fi
else
  echo xray-missing
fi
echo "==public-egress=="
(curl -4 -sS --max-time 8 https://api.ipify.org || true); echo
(curl -6 -sS --max-time 8 https://api64.ipify.org || true); echo
'@
$checkCommand = $checkCommand.Replace("__EXPECTED_DOMAIN_LIST__", $expectedDomainListBlock)

if ($Apply) {
  $safeRemoteApplyScript = Assert-SafeRemoteApplyScript -Path $RemoteApplyScript
  # The delegated script is treated as a versioned remote artifact. The
  # wrapper owns the shared lock, verifies its exact content, snapshots every
  # known routing artifact, and restores those artifacts if postconditions fail.
  $applyCommand = @"
set -e
set -o pipefail
apply_script="$safeRemoteApplyScript"
expected_generator_sha256=""
expected_apply_sha256="$expectedRemoteApplySha256"
if [ ! -x "`$apply_script" ]; then
  echo "missing executable apply script: `$apply_script" >&2
  exit 2
fi
actual_apply_sha256="`$(sha256sum "`$apply_script" | awk '{print `$1}')"
if [ "`$actual_apply_sha256" != "`$expected_apply_sha256" ]; then
  echo "REMOTE_APPLY_SCRIPT_HASH_MISMATCH expected=`$expected_apply_sha256 actual=`$actual_apply_sha256" >&2
  exit 47
fi
exec 9>/run/vps-ssh-launcher-maintenance.lock
flock -n 9 || { echo "REFUSE busy vps-ssh-launcher-maintenance.lock held" >&2; exit 75; }
BK=/var/backups/google-ipv4-routing-`$(date -u +%Y%m%dT%H%M%S.%NZ)
generator_script=/etc/v2ray-agent/apply-google-ipv4-routing-config.sh
if [ ! -f "`$generator_script" ] || [ -L "`$generator_script" ]; then
  echo "GENERATOR_NOT_REGULAR_FILE path=`$generator_script" >&2
  exit 48
fi
generator_sha256_initial="`$(sha256sum "`$generator_script" | awk '{print `$1}')"
if [ -n "`$expected_generator_sha256" ] && [ "`$generator_sha256_initial" != "`$expected_generator_sha256" ]; then
  echo "GENERATOR_SHA256_MISMATCH expected=`$expected_generator_sha256 actual=`$generator_sha256_initial" >&2
  exit 48
fi
mkdir -m 700 "`$BK"
for f in \
  "`$generator_script" \
  /etc/systemd/system/xray.service.d/20-google-ipv4-routing.conf \
  /etc/v2ray-agent/xray/conf/09_routing.json \
  /etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json \
  "`$apply_script"; do
  if [ -f "`$f" ]; then
    cp -a "`$f" "`$BK/"
  else
    : > "`$BK/`$(basename "`$f").missing"
  fi
done
chmod 700 "`$BK"
generator_sha256_before="`$generator_sha256_initial"
generator_backup_sha256="`$(sha256sum "`$BK/apply-google-ipv4-routing-config.sh" | awk '{print `$1}')"
if [ "`$generator_backup_sha256" != "`$generator_sha256_before" ]; then
  echo "GENERATOR_BACKUP_SHA256_MISMATCH expected=`$generator_sha256_before actual=`$generator_backup_sha256" >&2
  exit 52
fi
echo "BACKUP_DIR=`$BK"
echo "GENERATOR_BACKUP_SHA256=`$generator_backup_sha256"
restore_known_state() {
  set +e
  restore_failed=0
  restore_file() {
    path="`$1"
    name="`$2"
    if [ -f "`$BK/`$name.missing" ]; then
      rm -f "`$path" || restore_failed=1
    else
      cp -a "`$BK/`$name" "`$path" || restore_failed=1
    fi
  }
  generator_backup="`$BK/apply-google-ipv4-routing-config.sh"
  generator_restore_stage="`$generator_script.restore.`$`$"
  if [ -f "`$BK/apply-google-ipv4-routing-config.sh.missing" ] ||
     [ "`$(sha256sum "`$generator_backup" | awk '{print `$1}')" != "`$generator_sha256_before" ] ||
     ! cp -a "`$generator_backup" "`$generator_restore_stage" ||
     ! mv -f -- "`$generator_restore_stage" "`$generator_script"; then
    rm -f -- "`$generator_restore_stage"
    restore_failed=1
  fi
  restored_generator_sha256="`$(sha256sum "`$generator_script" | awk '{print `$1}')"
  [ "`$restored_generator_sha256" = "`$generator_sha256_before" ] || restore_failed=1
  restore_file /etc/systemd/system/xray.service.d/20-google-ipv4-routing.conf 20-google-ipv4-routing.conf
  restore_file /etc/v2ray-agent/xray/conf/09_routing.json 09_routing.json
  restore_file /etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json 98_google_ipv4_outbound.json
  restore_file "`$apply_script" reapply-google-ipv4-routing.sh
  if [ "`$restore_failed" -ne 0 ]; then
    set -e
    return 1
  fi
  systemctl daemon-reload >/dev/null 2>&1 || restore_failed=1
  systemctl restart xray >/dev/null 2>&1 || restore_failed=1
  systemctl is-active --quiet xray || restore_failed=1
  /etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf >/dev/null 2>&1 || restore_failed=1
  set -e
  return "`$restore_failed"
}
if [ -n "`$expected_generator_sha256" ]; then
  actual_generator_sha256="`$(sha256sum "`$generator_script" | awk '{print `$1}')"
  [ "`$actual_generator_sha256" = "`$expected_generator_sha256" ] || exit 48
  generator_stage="`$generator_script.staged.`$`$"
  cp -a "`$generator_script" "`$generator_stage"
  if ! python3 - "`$generator_stage" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
data = path.read_bytes()
before = b'          "domain:anthropic.com",\n          "domain:claude.ai"\n'
after = (
    b'          "domain:anthropic.com",\n'
    b'          "domain:claude.ai",\n'
    b'          "domain:claude.com",\n'
    b'          "domain:platform.claude.com"\n'
)
if data.count(before) != 1:
    raise SystemExit("GENERATOR_ROUTE_ANCHOR_MISMATCH")
path.write_bytes(data.replace(before, after, 1))
PY
  then
    rm -f -- "`$generator_stage"
    echo "GENERATOR_STAGE_EDIT_FAILED no_live_change"
    exit 49
  fi
  if ! bash -n "`$generator_stage" ||
     [ "`$(grep -Ec '^[[:space:]]*"domain:' "`$generator_stage")" -ne $expectedDomainCount ]; then
    echo "GENERATOR_STAGE_VALIDATION_FAILED" >&2
    rm -f -- "`$generator_stage"
    echo "GENERATOR_STAGE_DISCARDED no_live_change"
    exit 50
  fi
  if ! generator_stage_sha256="`$(sha256sum "`$generator_stage" | awk '{print `$1}')" ||
     [[ ! "`$generator_stage_sha256" =~ ^[[:xdigit:]]{64}$ ]]; then
    echo "GENERATOR_STAGE_SHA256_FAILED" >&2
    rm -f -- "`$generator_stage"
    echo "GENERATOR_STAGE_DISCARDED no_live_change"
    exit 50
  fi
  if ! mv -f -- "`$generator_stage" "`$generator_script"; then
    echo "GENERATOR_ATOMIC_REPLACE_FAILED" >&2
    rm -f -- "`$generator_stage"
    if current_generator_sha256="`$(sha256sum "`$generator_script" 2>/dev/null | awk '{print `$1}')"; then
      if [ "`$current_generator_sha256" = "`$expected_generator_sha256" ]; then
        echo "GENERATOR_REPLACE_FAILED no_live_change"
      elif restore_known_state; then
        echo "ROLLBACK_VERIFIED"
      else
        echo "ROLLBACK_FAILED" >&2
      fi
    elif restore_known_state; then
      echo "ROLLBACK_VERIFIED"
    else
      echo "ROLLBACK_FAILED" >&2
    fi
    exit 51
  fi
  if ! generator_sha256_applied="`$(sha256sum "`$generator_script" | awk '{print `$1}')" ||
     [ "`$generator_sha256_applied" != "`$generator_stage_sha256" ]; then
    echo "GENERATOR_POST_REPLACE_SHA256_MISMATCH expected=`$generator_stage_sha256 actual=`$generator_sha256_applied" >&2
    if restore_known_state; then echo "ROLLBACK_VERIFIED"; else echo "ROLLBACK_FAILED" >&2; fi
    exit 52
  fi
  echo "GENERATOR_APPLIED_SHA256=`$generator_sha256_applied"
  echo "GENERATOR_ROUTE_CONTRACT=${expectedDomainCount}_DOMAINS"
fi
if ! "`$apply_script"; then
  echo "REMOTE_APPLY_FAILED" >&2
  if restore_known_state; then
    echo "ROLLBACK_VERIFIED"
  else
    echo "ROLLBACK_FAILED" >&2
  fi
  exit 10
fi
# Each routing artifact must carry the marker on its own: `grep -Eq ... f1 f2`
# succeeds when *any* file matches, so a single-file write used to read as a
# clean postcondition. Keep this in step with the read-only probe above, which
# already reports per file.
marker_missing=0
for config_file in /etc/v2ray-agent/xray/conf/09_routing.json /etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json; do
  if ! grep -Eq 'gemini|google_ipv4_out|ForceIPv4|googleapis|gstatic' "`$config_file" 2>/dev/null; then
    echo "POST_APPLY_MARKER_MISSING file=`$config_file" >&2
    marker_missing=1
  fi
done
if ! python3 - <<'PY'
import json
from pathlib import Path

expected = [
__EXPECTED_DOMAIN_LIST__
]
route_data = json.loads(Path("/etc/v2ray-agent/xray/conf/09_routing.json").read_text(encoding="utf-8"))
outbound_data = json.loads(Path("/etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json").read_text(encoding="utf-8"))
routes = []
forced_outbounds = []

def walk_routes(value):
    if isinstance(value, dict):
        if value.get("outboundTag") == "google_ipv4_out" and isinstance(value.get("domain"), list):
            routes.append(value["domain"])
        for child in value.values():
            walk_routes(child)
    elif isinstance(value, list):
        for child in value:
            walk_routes(child)

def walk_outbounds(value):
    if isinstance(value, dict):
        settings = value.get("settings")
        if (
            value.get("tag") == "google_ipv4_out"
            and value.get("protocol") == "freedom"
            and isinstance(settings, dict)
            and settings.get("domainStrategy") == "ForceIPv4"
        ):
            forced_outbounds.append(value["tag"])
        for child in value.values():
            walk_outbounds(child)
    elif isinstance(value, list):
        for child in value:
            walk_outbounds(child)

walk_routes(route_data)
walk_outbounds(outbound_data)
if routes != [expected] or forced_outbounds != ["google_ipv4_out"]:
    raise SystemExit("POST_APPLY_ROUTE_CONTRACT_FAILED")
print("POST_APPLY_ROUTE_CONTRACT=GOOGLE_AND_CLAUDE_IPV4_ONLY_DOMAINS")
PY
then
  marker_missing=1
fi
if ! systemctl is-active --quiet xray ||
   ! /etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf >/dev/null 2>&1 ||
   [ "`$marker_missing" -ne 0 ]; then
  echo "POST_APPLY_VERIFICATION_FAILED" >&2
  if restore_known_state; then
    echo "ROLLBACK_VERIFIED"
  else
    echo "ROLLBACK_FAILED" >&2
  fi
  exit 11
fi
while IFS= read -r old_backup; do
  [ "`$old_backup" = "`$BK" ] && continue
  rm -rf -- "`$old_backup" || echo "BACKUP_PRUNE_FAILED path=`$old_backup" >&2
done < <(
  find /var/backups -mindepth 1 -maxdepth 1 -type d \
    -name 'google-ipv4-routing-*' -printf '%T@ %p\n' |
    sort -rn | tail -n +9 | cut -d' ' -f2-
) || true
echo "BACKUP_PRUNE scope=google_ipv4 policy=keep_8"
echo "APPLY_VERIFIED"
"@
  $applyCommand = $applyCommand.Replace("__EXPECTED_DOMAIN_LIST__", $expectedDomainListBlock)
if ($PinClaudeWebIPv4) {
    $generatorHashPlaceholder = 'expected_generator_sha256=""'
    if ([regex]::Matches($applyCommand, [regex]::Escape($generatorHashPlaceholder)).Count -ne 1) {
      throw "Unable to bind the generator SHA-256 pin into the remote transaction."
    }
    $applyCommand = $applyCommand.Replace(
      $generatorHashPlaceholder,
      "expected_generator_sha256=`"$expectedGeneratorSha256`"")
  }
  Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $applyCommand -IdleTimeoutSeconds 300 -HardTimeoutSeconds 360
}

Invoke-LauncherRemoteCommand -Python $py -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $checkCommand -IdleTimeoutSeconds 120 -HardTimeoutSeconds 180

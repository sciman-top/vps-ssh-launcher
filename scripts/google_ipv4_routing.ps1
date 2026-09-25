#requires -Version 7
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [string]$Profile,
  [switch]$Apply,
  [string]$RemoteApplyScript = "/etc/v2ray-agent/reapply-google-ipv4-routing.sh",
  [string]$RemoteApplySha256
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

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
$expectedRemoteApplySha256 = if ($RemoteApplySha256) {
  $RemoteApplySha256.ToLowerInvariant()
} else {
  ""
}

function Invoke-RemoteCommand {
  param(
    [string]$Command,
    [int]$IdleTimeoutSeconds = 120,
    [int]$HardTimeoutSeconds = 180
  )

  $exitCode = Invoke-LauncherPython -Python $py -ProjectRoot $repoRoot -LauncherArgs @(
    "--config", $Config,
    "--profile", $Profile,
    "--strict-host-key-checking",
    "run",
    "--command-timeout", "$IdleTimeoutSeconds",
    "--command-hard-timeout", "$HardTimeoutSeconds",
    "--command", $Command
  )
  if ($exitCode -ne 0) {
    throw "Remote command failed with exit code $exitCode."
  }
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

if ($Apply) {
  $safeRemoteApplyScript = Assert-SafeRemoteApplyScript -Path $RemoteApplyScript
  # The delegated script is treated as a versioned remote artifact. The
  # wrapper owns the shared lock, verifies its exact content, snapshots every
  # known routing artifact, and restores those artifacts if postconditions fail.
  $applyCommand = @"
set -e
apply_script="$safeRemoteApplyScript"
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
mkdir -m 700 "`$BK"
for f in \
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
echo "BACKUP_DIR=`$BK"
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
  restore_file /etc/systemd/system/xray.service.d/20-google-ipv4-routing.conf 20-google-ipv4-routing.conf
  restore_file /etc/v2ray-agent/xray/conf/09_routing.json 09_routing.json
  restore_file /etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json 98_google_ipv4_outbound.json
  restore_file "`$apply_script" reapply-google-ipv4-routing.sh
  systemctl daemon-reload >/dev/null 2>&1 || restore_failed=1
  systemctl restart xray >/dev/null 2>&1 || restore_failed=1
  systemctl is-active --quiet xray || restore_failed=1
  /etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf >/dev/null 2>&1 || restore_failed=1
  set -e
  return "`$restore_failed"
}
if ! "`$apply_script"; then
  echo "REMOTE_APPLY_FAILED" >&2
  if restore_known_state; then
    echo "ROLLBACK_VERIFIED"
  else
    echo "ROLLBACK_FAILED" >&2
  fi
  exit 10
fi
if ! systemctl is-active --quiet xray ||
   ! /etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf >/dev/null 2>&1 ||
   ! grep -Eq 'gemini|google_ipv4_out|ForceIPv4|googleapis|gstatic' /etc/v2ray-agent/xray/conf/09_routing.json /etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json 2>/dev/null; then
  echo "POST_APPLY_VERIFICATION_FAILED" >&2
  if restore_known_state; then
    echo "ROLLBACK_VERIFIED"
  else
    echo "ROLLBACK_FAILED" >&2
  fi
  exit 11
fi
echo "APPLY_VERIFIED"
"@
  Invoke-RemoteCommand -Command $applyCommand -IdleTimeoutSeconds 300 -HardTimeoutSeconds 360
}

Invoke-RemoteCommand -Command $checkCommand

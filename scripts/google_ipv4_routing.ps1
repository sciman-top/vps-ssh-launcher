param(
  [string]$Config,
  [string]$Profile = "bwg",
  [switch]$Apply,
  [string]$RemoteApplyScript = "/etc/v2ray-agent/reapply-google-ipv4-routing.sh"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$sshTool = Join-Path $repoRoot "ssh_tool.py"
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

if (-not $Config) {
  if (-not $env:APPDATA) {
    throw "APPDATA is not set. Pass -Config explicitly."
  }
  $Config = Join-Path $env:APPDATA "vps-ssh-launcher\target.json"
}

if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}

function Invoke-RemoteCommand {
  param([string]$Command)

  Push-Location $repoRoot
  try {
    & $py.Exe @($py.Args + @($sshTool, "--config", $Config, "--profile", $Profile, "run", "--command", $Command))
    if ($LASTEXITCODE -ne 0) {
      throw "Remote command failed with exit code $LASTEXITCODE."
    }
  } finally {
    Pop-Location
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
grep -n 'gemini\|google_ipv4_out\|ForceIPv4\|googleapis\|gstatic' /etc/v2ray-agent/xray/conf/09_routing.json /etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json 2>/dev/null || true
echo "==xray-config-test=="
if [ -x /etc/v2ray-agent/xray/xray ] && [ -d /etc/v2ray-agent/xray/conf ]; then
  /etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf >/tmp/xray-google-ipv4-test.out 2>&1 && echo config-ok || (cat /tmp/xray-google-ipv4-test.out; exit 1)
else
  echo xray-missing
fi
echo "==public-egress=="
(curl -4 -sS --max-time 8 https://api.ipify.org || true); echo
(curl -6 -sS --max-time 8 https://api64.ipify.org || true); echo
'@

if ($Apply) {
  $safeRemoteApplyScript = Assert-SafeRemoteApplyScript -Path $RemoteApplyScript
  $applyCommand = @"
set -e
apply_script="$safeRemoteApplyScript"
if [ ! -x "`$apply_script" ]; then
  echo "missing executable apply script: `$apply_script" >&2
  exit 2
fi
"`$apply_script"
"@
  Invoke-RemoteCommand -Command $applyCommand
}

Invoke-RemoteCommand -Command $checkCommand

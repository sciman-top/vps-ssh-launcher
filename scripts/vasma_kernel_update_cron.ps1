param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [string]$Profile,
  [Parameter(Mandatory = $true)]
  [ValidateSet("xray", "sing-box")]
  [string]$Kernel,
  [string]$Schedule = "0 14 * * 5",
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$sshTool = Join-Path $repoRoot "ssh_tool.py"

function Initialize-WindowsProcessEnvironment {
  if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) {
    return
  }

  $windowsRoot = $env:SYSTEMROOT
  if (-not $windowsRoot) {
    $windowsRoot = $env:WINDIR
  }
  if (-not $windowsRoot -and (Test-Path -LiteralPath "C:\Windows")) {
    $windowsRoot = "C:\Windows"
  }

  if ($windowsRoot) {
    if (-not $env:SYSTEMROOT) {
      $env:SYSTEMROOT = $windowsRoot
    }
    if (-not $env:WINDIR) {
      $env:WINDIR = $windowsRoot
    }
    $cmdExe = Join-Path $windowsRoot "System32\cmd.exe"
    if ((-not $env:COMSPEC) -and (Test-Path -LiteralPath $cmdExe)) {
      $env:COMSPEC = $cmdExe
    }
  }

  if ((-not $env:USERPROFILE) -and $HOME -and (Test-Path -LiteralPath $HOME)) {
    $env:USERPROFILE = $HOME
  }
  if ($env:USERPROFILE) {
    $roamingAppData = Join-Path $env:USERPROFILE "AppData\Roaming"
    if ((-not $env:APPDATA) -and (Test-Path -LiteralPath $roamingAppData)) {
      $env:APPDATA = $roamingAppData
    }
    $localAppData = Join-Path $env:USERPROFILE "AppData\Local"
    if ((-not $env:LOCALAPPDATA) -and (Test-Path -LiteralPath $localAppData)) {
      $env:LOCALAPPDATA = $localAppData
    }
  }
  if ((-not $env:PROGRAMDATA) -and (Test-Path -LiteralPath "C:\ProgramData")) {
    $env:PROGRAMDATA = "C:\ProgramData"
  }
}

function Resolve-ProjectPython {
  $candidates = @()
  if ($env:VPS_SSH_LAUNCHER_PYTHON) {
    if (-not (Test-Path -LiteralPath $env:VPS_SSH_LAUNCHER_PYTHON)) {
      throw "VPS_SSH_LAUNCHER_PYTHON is set but the file does not exist: $env:VPS_SSH_LAUNCHER_PYTHON"
    }
    $candidates += @{ Exe = $env:VPS_SSH_LAUNCHER_PYTHON; Args = @(); Source = "env" }
  }

  $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    $candidates += @{ Exe = $venvPython; Args = @(); Source = "venv" }
  }

  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate.Exe) {
      return $candidate
    }
  }

  if (Get-Command python -ErrorAction SilentlyContinue) {
    return @{ Exe = "python"; Args = @(); Source = "path:python" }
  }
  if (Get-Command py -ErrorAction SilentlyContinue) {
    return @{ Exe = "py"; Args = @("-3"); Source = "path:py" }
  }
  throw "Python not found. Install Python 3 or set VPS_SSH_LAUNCHER_PYTHON."
}

function Assert-CronSchedule {
  param([string]$Value)

  if ($Value -notmatch '^[0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+ [0-9*,/\-]+$') {
    throw "Schedule must be a five-field cron expression."
  }
}

function Invoke-RemoteCommand {
  param([string]$Command)

  Push-Location $repoRoot
  try {
    & $script:Python.Exe @($script:Python.Args + @($sshTool, "--config", $Config, "--profile", $Profile, "run", "--command", $Command))
    if ($LASTEXITCODE -ne 0) {
      throw "Remote command failed with exit code $LASTEXITCODE."
    }
  } finally {
    Pop-Location
  }
}

Initialize-WindowsProcessEnvironment
Assert-CronSchedule -Value $Schedule
$script:Python = Resolve-ProjectPython

if (-not $Config) {
  if (-not $env:APPDATA) {
    throw "APPDATA is not set. Pass -Config explicitly."
  }
  $Config = Join-Path $env:APPDATA "vps-ssh-launcher\target.json"
}
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}

$applyValue = if ($Apply) { "1" } else { "0" }

$remoteCommand = @"
set -Eeuo pipefail
kernel='$Kernel'
schedule='$Schedule'
apply='$applyValue'

xray_script='/etc/v2ray-agent/auto_update_xray.sh'
singbox_script='/etc/v2ray-agent/auto_update_singbox.sh'

require_vasma() {
  if [ ! -x /usr/bin/vasma ]; then
    echo 'missing executable /usr/bin/vasma' >&2
    exit 2
  fi
}

write_xray_wrapper() {
  cat > "`$xray_script" <<'EOF'
#!/usr/bin/env bash
# Auto-update Xray core through v2ray-agent/vasma.
# Menu path: 16.core管理 -> 1.Xray-core -> 1.升级Xray-core -> y when same version.
# Manual trigger rule: run this wrapper as the only remote command, verify in a
# second SSH command, and never trigger multiple VPS kernel updates in parallel.
set -Eeuo pipefail
LOG="/etc/v2ray-agent/crontab_xray_update.log"
LOCK_FILE="/run/v2ray-agent-maint.lock"

log() { echo "[`$(date '+%Y-%m-%d %H:%M:%S')] `$*" >> "`$LOG"; }

recover_on_error() {
  rc="`$?"
  log "ERROR: vasma Xray-core update failed with exit=`$rc; checking xray state"
  if ! systemctl is-active --quiet xray; then
    log "WARN: xray inactive after failure; trying systemctl start xray"
    systemctl start xray >> "`$LOG" 2>&1 || true
  fi
  exit "`$rc"
}
trap recover_on_error ERR

exec 9>"`$LOCK_FILE"
if ! flock -n 9; then
  log "INFO: another maintenance/update job is already running; exit"
  exit 0
fi

if [ ! -x /usr/bin/vasma ]; then
  log "ERROR: /usr/bin/vasma not executable"
  exit 1
fi

log "========== vasma Xray-core update start =========="
printf '16\n1\n1\ny\n' | /usr/bin/vasma >> "`$LOG" 2>&1
systemctl is-active --quiet xray
/etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf >> "`$LOG" 2>&1
log "========== vasma Xray-core update done =========="
EOF
  chmod 755 "`$xray_script"
}

write_singbox_wrapper() {
  cat > "`$singbox_script" <<'EOF'
#!/usr/bin/env bash
# Auto-update sing-box core through v2ray-agent/vasma.
# Menu path: 16.core管理 -> 2.sing-box -> 1.升级 sing-box.
# Manual trigger rule: run this wrapper as the only remote command, verify in a
# second SSH command, and never trigger multiple VPS kernel updates in parallel.
set -Eeuo pipefail
LOG="/etc/v2ray-agent/crontab_singbox_update.log"
LOCK_FILE="/run/v2ray-agent-maint.lock"

log() { echo "[`$(date '+%Y-%m-%d %H:%M:%S')] `$*" >> "`$LOG"; }

recover_on_error() {
  rc="`$?"
  log "ERROR: vasma sing-box update failed with exit=`$rc; checking sing-box state"
  if ! systemctl is-active --quiet sing-box; then
    log "WARN: sing-box inactive after failure; trying systemctl start sing-box"
    systemctl start sing-box >> "`$LOG" 2>&1 || true
  fi
  exit "`$rc"
}
trap recover_on_error ERR

exec 9>"`$LOCK_FILE"
if ! flock -n 9; then
  log "INFO: another maintenance/update job is already running; exit"
  exit 0
fi

if [ ! -x /usr/bin/vasma ]; then
  log "ERROR: /usr/bin/vasma not executable"
  exit 1
fi

log "========== vasma sing-box update start =========="
printf '16\n2\n1\n' | /usr/bin/vasma >> "`$LOG" 2>&1
systemctl is-active --quiet sing-box
/etc/v2ray-agent/sing-box/sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json >> "`$LOG" 2>&1
log "========== vasma sing-box update done =========="
EOF
  chmod 755 "`$singbox_script"
}

install_cron() {
  tmp="`$(mktemp)"
  crontab -l 2>/dev/null | grep -v -E '/etc/v2ray-agent/auto_update_(xray|singbox)\.sh' > "`$tmp" || true
  if [ "`$kernel" = 'xray' ]; then
    echo "`$schedule /bin/bash `$xray_script" >> "`$tmp"
  else
    echo "`$schedule /bin/bash `$singbox_script" >> "`$tmp"
  fi
  crontab "`$tmp"
  rm -f "`$tmp"
}

require_vasma

if [ "`$apply" = '1' ]; then
  if [ "`$kernel" = 'xray' ]; then
    write_xray_wrapper
    rm -f "`$singbox_script"
  else
    write_singbox_wrapper
    rm -f "`$xray_script"
  fi
  install_cron
fi

echo '==vasma=='
ls -l /usr/bin/vasma /etc/v2ray-agent/install.sh 2>/dev/null || true
echo '==selected-kernel=='
echo "`$kernel"
echo '==cron=='
crontab -l 2>/dev/null | grep -E 'auto_update_(xray|singbox)\.sh' || true
echo '==scripts=='
for f in "`$xray_script" "`$singbox_script"; do
  echo "--`$f--"
  if [ -e "`$f" ]; then
    ls -l "`$f"
    grep -nE 'vasma|printf|github|wget|curl|REPO=|Xray-core|sing-box|Menu path' "`$f" || true
    bash -n "`$f"
    echo syntax-ok
  else
    echo missing
  fi
done
"@

Invoke-RemoteCommand -Command $remoteCommand

#requires -Version 7

# cpa_status_deploy.ps1 - deploy/audit/rollback the on-host CPA status page.
#
# The status page daemon serves the cpa_status.py view in-process on the VPS
# (loopback 127.0.0.1:8319), behind the existing CPAMC nginx shell on 28317
# (TLS + basic auth + rate limit are inherited from that server block).
#
# Modes:
#   (no switches)     audit: compare local/remote sha256, service state,
#                     nginx fragment presence. Read-only.
#   -Deploy           push scripts/cpa_status.py -> /opt/cpa-status/ (backup
#                     first, sha256 verified), install cpa-status.service
#                     (root + hardening, loopback-only bind), enable+start,
#                     health-check the loopback endpoint.
#   -ConfigureNginx   insert the /cpa-status/ location into the existing
#                     cpamc-management.conf (backup -> nginx -t gate ->
#                     reload; automatic restore on gate failure). Idempotent.
#   -Rollback         disable+remove the service and /opt/cpa-status, restore
#                     the nginx backup if present.
#
# Security contract: the page binds loopback only; the operator-facing gate
# is nginx's existing basic auth (cpamc.htpasswd). No secrets are embedded
# in this script; the management key stays in /root/.cpa-management-key and
# is read by the daemon on the host only.

[CmdletBinding()]
param(
  [string]$Profile = "bwg",
  [string]$Config = "",
  [switch]$Deploy,
  [switch]$ConfigureNginx,
  [switch]$Rollback
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot
$sourcePath = Join-Path $repoRoot "scripts\cpa_status.py"
$remoteDir = "/opt/cpa-status"
$remoteFile = "$remoteDir/cpa_status.py"
$unitPath = "/etc/systemd/system/cpa-status.service"
$nginxConf = "/etc/nginx/conf.d/cpamc-management.conf"
$servicePort = 8319

function Invoke-Remote {
  param([string]$Command, [switch]$Capture)
  $transport = @{
    Python = $script:Python
    ProjectRoot = $repoRoot
    Profile = $Profile
    Command = $Command
    RemoteTempPrefix = "cpa-status"
    IdleTimeoutSeconds = 60
  }
  if (-not [string]::IsNullOrWhiteSpace($Config)) { $transport.Config = $Config }
  if ($Capture) { $transport.CaptureOutput = $true }
  return Invoke-LauncherRemoteCommand @transport
}

function Get-LocalSha256 {
  return (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLower()
}

function Send-StatusSource {
  # Backup, push (chunked mode-600 transport), verify sha256.
  $localSha = Get-LocalSha256
  $prepare = @"
umask 022; mkdir -p '$remoteDir';
if [ -f '$remoteFile' ]; then cp -a '$remoteFile' '$remoteFile.bak.`$(date +%Y%m%d%H%M%S)'; fi;
exit 0
"@
  Invoke-Remote -Command $prepare | Out-Null
  $sourceText = [IO.File]::ReadAllText($sourcePath)
  $sourceSha = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($sourceText))
  $push = (
    "umask 022; printf %s '$sourceSha' | base64 -d > '$remoteFile' && " +
    "sha256sum '$remoteFile' | cut -d' ' -f1"
  )
  $result = Invoke-Remote -Command $push -Capture
  $remoteSha = ($result.Lines | Select-Object -Last 1)
  if ($result.ExitCode -ne 0 -or $remoteSha -ne $localSha) {
    throw "sha256 mismatch after push: local=$localSha remote=$remoteSha exit=$($result.ExitCode)"
  }
  Write-Output "CPA_STATUS_SOURCE_PUSHED=$localSha"
}

function Install-StatusUnit {
  $unit = @"
[Unit]
Description=CPA status page (read-only per-model/channel view)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# Root is required to read /root/.cpa-management-key (0600) and the
# admission state files; the listener is loopback-only and the public
# gate is nginx's existing basic auth on 28317.
ExecStart=/usr/bin/python3 $remoteFile --remote-serve --port $servicePort
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
"@
  $unitB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($unit))
  $install = (
    "umask 022; printf %s '$unitB64' | base64 -d > '$unitPath' && " +
    "systemctl daemon-reload && systemctl enable --now cpa-status.service"
  )
  Invoke-Remote -Command $install | Out-Null
  Start-Sleep -Seconds 2
  $health = Invoke-Remote -Capture -Command (
    "systemctl is-active cpa-status.service && " +
    "curl -fsS http://127.0.0.1:$servicePort/json | head -c 40"
  )
  $state = ($health.Lines | Select-Object -First 1)
  if ($health.ExitCode -ne 0) {
    throw "service health check failed: $($health.Lines -join '; ')"
  }
  Write-Output "CPA_STATUS_SERVICE_ACTIVE=$state"
}

function Add-NginxLocation {
  $pythonSnippet = @'
import pathlib, re, sys, time
path = pathlib.Path("/etc/nginx/conf.d/cpamc-management.conf")
text = path.read_text(encoding="utf-8")
fragment = (
    "    location = /cpa-status { return 301 /cpa-status/; }\n\n"
    "    location /cpa-status/ {\n"
    "        limit_req zone=cpamc_rl burst=20 nodelay;\n"
    '        auth_basic "CPAMC";\n'
    "        auth_basic_user_file /etc/nginx/cpamc.htpasswd;\n"
    "        proxy_pass http://127.0.0.1:8319/;\n"
    "        proxy_http_version 1.1;\n"
    '        proxy_set_header Connection "";\n'
    "        proxy_set_header Host $host;\n"
    "        proxy_buffering off;\n"
    "        proxy_read_timeout 60s;\n"
    "    }\n"
)
backup = path.with_suffix(".conf.bak." + time.strftime("%Y%m%d%H%M%S"))
if "/cpa-status/" in text:
    # Idempotent REPLACE: correct an earlier-generation fragment in place
    # (proxy_pass trailing slash strips the prefix; = redirect covers the
    # slash-less spelling).
    pattern = re.compile(
        r"(    location = /cpa-status \{ return 301 /cpa-status/; \}\n\n)?"
        r"    location /cpa-status/ \{.*?\n    \}\n",
        re.S,
    )
    new_text, count = pattern.subn(fragment, text, count=1)
    if count != 1:
        print("FRAGMENT_SHAPE_UNKNOWN")
        sys.exit(1)
    backup.write_text(text, encoding="utf-8", newline="\n")
    path.write_text(new_text, encoding="utf-8", newline="\n")
    print("REPLACED")
else:
    marker = "    # default deny"
    if marker not in text:
        print("MARKER_MISSING")
        sys.exit(1)
    backup.write_text(text, encoding="utf-8", newline="\n")
    path.write_text(text.replace(marker, fragment + "\n" + marker, 1), encoding="utf-8", newline="\n")
    print("INSERTED")
'@
  $snippetB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pythonSnippet))
  $insert = "printf %s '$snippetB64' | base64 -d | python3 -"
  $result = Invoke-Remote -Command $insert -Capture
  $outcome = ($result.Lines | Select-Object -Last 1)
  if ($outcome -ne "INSERTED" -and $outcome -ne "REPLACED") {
    Write-Output "CPA_STATUS_NGINX=$outcome"
    return
  }
  if ($result.ExitCode -ne 0) {
    throw "nginx fragment insertion failed: $($result.Lines -join '; ')"
  }
  $gate = Invoke-Remote -Capture -Command (
    "if nginx -t 2>/dev/null; then systemctl reload nginx && echo RELOADED; " +
    "else LATEST=`$(ls -1t /etc/nginx/conf.d/cpamc-management.conf.bak.* 2>/dev/null | head -1); " +
    "if [ -n `"`$LATEST`" ]; then cp -a `"`$LATEST`" $nginxConf; fi; nginx -t && systemctl reload nginx; " +
    "echo RESTORED; fi"
  )
  $verdict = ($gate.Lines | Select-Object -Last 1)
  if ($verdict -ne "RELOADED") {
    throw "nginx gate failed and fragment was restored: $($gate.Lines -join '; ')"
  }
  $probe = Invoke-Remote -Capture -Command (
    "curl -k -s -o /dev/null -w '%{http_code}' -u cpa-audit:wrong " +
    "-H 'Host: fq.sciman.top' https://127.0.0.1:28317/cpa-status/ && echo && " +
    "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$servicePort/"
  )
  Write-Output "CPA_STATUS_NGINX_GATE=$($probe.Lines -join ' ')"
}

function Invoke-StatusRollback {
  Invoke-Remote -Command (
    "systemctl disable --now cpa-status.service 2>/dev/null; " +
    "rm -f '$unitPath'; systemctl daemon-reload; " +
    "LATEST=`$(ls -1t /etc/nginx/conf.d/cpamc-management.conf.bak.* 2>/dev/null | head -1); " +
    "if [ -n `"`$LATEST`" ]; then cp -a `"`$LATEST`" $nginxConf; nginx -t && systemctl reload nginx; fi; " +
    "rm -rf '$remoteDir'; echo ROLLED_BACK"
  ) | Out-Null
  Write-Output "CPA_STATUS_ROLLBACK=DONE"
}

function Invoke-StatusAudit {
  $localSha = Get-LocalSha256
  $probe = Invoke-Remote -Capture -Command (
    "if [ -f '$remoteFile' ]; then sha256sum '$remoteFile' | cut -d' ' -f1; " +
    "else echo REMOTE_MISSING; fi; " +
    "systemctl is-active cpa-status.service 2>/dev/null || echo inactive; " +
    "grep -q '/cpa-status/' $nginxConf && echo NGINX_PRESENT || echo NGINX_ABSENT"
  )
  $lines = @($probe.Lines)
  $remoteSha = if ($lines.Count -gt 0) { $lines[0] } else { "unknown" }
  $serviceState = if ($lines.Count -gt 1) { $lines[1] } else { "unknown" }
  $nginxState = if ($lines.Count -gt 2) { $lines[2] } else { "unknown" }
  $sync = if ($remoteSha -eq $localSha) { "MATCH" } else { "MISMATCH" }
  Write-Output "CPA_STATUS_AUDIT local=$localSha remote=$remoteSha sync=$sync"
  Write-Output "CPA_STATUS_AUDIT service=$serviceState nginx=$nginxState"
}

if ($Rollback) {
  Invoke-StatusRollback
  return
}
if ($Deploy) {
  Send-StatusSource
  Install-StatusUnit
}
if ($ConfigureNginx) {
  Add-NginxLocation
}
if (-not $Deploy -and -not $ConfigureNginx) {
  Invoke-StatusAudit
}

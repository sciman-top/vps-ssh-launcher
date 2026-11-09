#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [int]$Port = 8444,
  [string]$ObfsPassword,
  [string]$ObfsPasswordFile,
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

Initialize-WindowsProcessEnvironment
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}
if ($Port -lt 1024 -or $Port -gt 65535) {
  throw "-Port must be an unprivileged UDP port (1024..65535)."
}
if ($ObfsPassword -and $ObfsPasswordFile) {
  throw "-ObfsPassword and -ObfsPasswordFile are mutually exclusive."
}

function Set-Hysteria2ObfsSecretAcl {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  # The file contains a live transport secret. Remove inheritance and retain
  # only LocalSystem, Administrators and the current user. Fail closed if the
  # resulting ACL cannot be projected and verified.
  $secretSids = @(
    [System.Security.Principal.SecurityIdentifier]::new("S-1-5-18").Value
    [System.Security.Principal.SecurityIdentifier]::new("S-1-5-32-544").Value
    [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  )
  $grantSpecs = @($secretSids | ForEach-Object { "*${_}:F" })
  # Set-Acl attempts to round-trip the SACL and can require SeSecurityPrivilege
  # even though this file only needs a DACL. icacls changes the DACL without
  # that privilege; the explicit post-check below remains the authority.
  $icaclsOutput = & icacls $Path /inheritance:r /grant:r @grantSpecs 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to set private ACL on $Path`: $icaclsOutput"
  }
  $verifiedSecretAcl = Get-Acl -LiteralPath $Path
  $actualSids = @(
    foreach ($secretRule in @($verifiedSecretAcl.Access)) {
      $secretRule.IdentityReference.Translate(
        [System.Security.Principal.SecurityIdentifier]
      ).Value
    }
  )
  $accessRulesValid = @($verifiedSecretAcl.Access).Count -eq 3
  foreach ($secretRule in @($verifiedSecretAcl.Access)) {
    if ($secretRule.IsInherited -or
        $secretRule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
        $secretRule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl) {
      $accessRulesValid = $false
    }
  }
  if ($verifiedSecretAcl.AreAccessRulesProtected -ne $true -or
      -not $accessRulesValid -or
      (($actualSids | Sort-Object) -join ',') -ne (($secretSids | Sort-Object) -join ',')) {
    throw "Refusing to retain obfs password file with an unverifiable private ACL: $Path"
  }
}

function New-Hysteria2ObfsSecretStage {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$Value
  )

  $directory = Split-Path -Parent $Path
  if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
  }
  if (Test-Path -LiteralPath $Path -PathType Leaf) {
    $destination = Get-Item -LiteralPath $Path -Force
    if (($destination.Attributes -band [IO.FileAttributes]::ReadOnly) -ne 0) {
      throw "Refusing to replace read-only obfs password file: $Path"
    }
  }
  $leaf = Split-Path -Leaf $Path
  $staged = Join-Path $directory ".$leaf.$([guid]::NewGuid().ToString('N')).tmp"
  $stream = $null
  try {
    # Create the file first, protect it, then write the secret. The plaintext
    # never exists under an inherited or broad ACL even for a short window.
    $stream = [IO.File]::Open(
      $staged,
      [IO.FileMode]::CreateNew,
      [IO.FileAccess]::Write,
      [IO.FileShare]::None
    )
    $stream.Dispose()
    $stream = $null
    Set-Hysteria2ObfsSecretAcl -Path $staged
    $stream = [IO.File]::Open(
      $staged,
      [IO.FileMode]::Open,
      [IO.FileAccess]::Write,
      [IO.FileShare]::None
    )
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush($true)
    return $staged
  }
  catch {
    if ($null -ne $stream) {
      $stream.Dispose()
      $stream = $null
    }
    if (Test-Path -LiteralPath $staged) {
      Remove-Item -LiteralPath $staged -Force
    }
    throw
  }
  finally {
    if ($null -ne $stream) { $stream.Dispose() }
  }
}

function Publish-Hysteria2ObfsSecretStage {
  param(
    [Parameter(Mandatory = $true)]
    [string]$StagedPath,
    [Parameter(Mandatory = $true)]
    [string]$DestinationPath
  )

  # Both paths are in the same directory, so Move is the final atomic commit.
  [IO.File]::Move($StagedPath, $DestinationPath, $true)
  Set-Hysteria2ObfsSecretAcl -Path $DestinationPath
}

$configDir = Split-Path -Parent $Config
if (-not $ObfsPasswordFile) {
  $ObfsPasswordFile = Join-Path $configDir "$Profile-hysteria2-obfs-password-v2.txt"
} else {
  $ObfsPasswordFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ObfsPasswordFile)
}
$passwordGenerated = $false
if (-not $ObfsPassword -and (Test-Path -LiteralPath $ObfsPasswordFile -PathType Leaf)) {
  $ObfsPassword = (Get-Content -LiteralPath $ObfsPasswordFile -Raw).Trim()
}
if (-not $ObfsPassword -and $Apply) {
  $passwordBytes = [byte[]]::new(32)
  [Security.Cryptography.RandomNumberGenerator]::Fill($passwordBytes)
  $ObfsPassword = [Convert]::ToBase64String($passwordBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
  $passwordGenerated = $true
}

$applyLiteral = if ($Apply) { "1" } else { "0" }
$obfsPasswordLiteral = if ($ObfsPassword) {
  if ($ObfsPassword -notmatch '^[A-Za-z0-9._-]{1,64}$') {
    throw "-ObfsPassword must be 1..64 chars of [A-Za-z0-9._-]."
  }
  $ObfsPassword
} else { "" }

# Adds a second Hysteria2 inbound that negotiates obfs (salamander) on its own
# UDP port, alongside the existing (un-obfuscated) Hysteria2 lane. The original
# inbound and its vasma subscription are untouched, so existing clients keep
# working; the obfuscated lane is an additional anti-blocking path the operator
# can switch to when a bare QUIC/Hysteria2 handshake starts getting filtered.
# Config is written as a source fragment and re-merged, matching how the fleet
# already layers 99_* fragments, and never edits upstream vasma. Idempotent.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
OBFS_PORT="__PORT__"
OBFS_PASSWORD="__OBFS_PASSWORD__"
LOCK_FILE="${VPS_HY2OBFS_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
SB_BINARY="${VPS_HY2OBFS_SB_BINARY:-/etc/v2ray-agent/sing-box/sing-box}"
SB_CONF_DIR="${VPS_HY2OBFS_SB_CONF_DIR:-/etc/v2ray-agent/sing-box/conf}"
SB_SOURCE_DIR="$SB_CONF_DIR/config"
SB_SERVICE="${VPS_HY2OBFS_SB_SERVICE:-sing-box}"
FRAGMENT="$SB_SOURCE_DIR/98_vps_ssh_launcher_hysteria2_obfs.json"
STATUS_DIR="${VPS_HY2OBFS_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
BACKUP_PARENT="${VPS_HY2OBFS_BACKUP_DIR:-/var/backups}"
STATUS_FILE="$STATUS_DIR/hysteria2-obfs.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=hysteria2-obfs\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

# v2ray-agent is normally deployed on systemd hosts, but the maintenance
# wrappers also support OpenRC. Keep this lane on the same service boundary so
# a non-systemd host fails before a write and can roll back through the same
# manager it used for the apply.
service_manager() {
    if command -v systemctl >/dev/null 2>&1 &&
       systemctl cat "$SB_SERVICE" >/dev/null 2>&1; then
        printf 'systemctl\n'
    elif command -v rc-service >/dev/null 2>&1 &&
         [ -x "/etc/init.d/$SB_SERVICE" ]; then
        printf 'rc-service\n'
    else
        return 1
    fi
}

service_is_active() {
    local manager
    manager="$(service_manager)" || return 1
    if [ "$manager" = 'systemctl' ]; then
        systemctl is-active --quiet "$SB_SERVICE"
    else
        rc-service "$SB_SERVICE" status >/dev/null 2>&1
    fi
}

service_restart() {
    local manager
    manager="$(service_manager)" || return 1
    if [ "$manager" = 'systemctl' ]; then
        systemctl restart "$SB_SERVICE"
    else
        rc-service "$SB_SERVICE" restart
    fi
}

resolve_identity() {
    python3 - "$SB_SOURCE_DIR/06_hysteria2_inbounds.json" <<'PY'
import json
import sys
try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception:
    print('IDENTITY_UNAVAILABLE')
    raise SystemExit(4)
for ib in data.get('inbounds', []) or []:
    if ib.get('type') != 'hysteria2':
        continue
    print('port=' + str(ib.get('listen_port')))
    tls = ib.get('tls') or {}
    users = ib.get('users') or []
    print('server_name=' + str(tls.get('server_name')))
    print('cert=' + str(tls.get('certificate_path')))
    print('key=' + str(tls.get('key_path')))
    print('auth_password=' + str((users[0].get('password') if users else '') or ''))
    raise SystemExit(0)
print('IDENTITY_UNAVAILABLE')
raise SystemExit(4)
PY
}

echo '==hysteria2-obfs-state=='
if [ ! -f "$SB_SOURCE_DIR/06_hysteria2_inbounds.json" ]; then
    echo 'hysteria2_base=absent'
    echo 'obfs_lane=not_applicable'
    write_status noop 0
    exit 0
fi
echo 'hysteria2_base=present'
identity="$(resolve_identity)" || { echo 'IDENTITY_RESOLVE_FAILED' >&2; write_status failed 4; exit 4; }
server_name="$(printf '%s\n' "$identity" | sed -n 's/^server_name=//p')"
base_port="$(printf '%s\n' "$identity" | sed -n 's/^port=//p')"
cert_path="$(printf '%s\n' "$identity" | sed -n 's/^cert=//p')"
key_path="$(printf '%s\n' "$identity" | sed -n 's/^key=//p')"
base_auth_password="$(printf '%s\n' "$identity" | sed -n 's/^auth_password=//p')"
if [ -z "$server_name" ] || [ -z "$base_port" ] || [ -z "$cert_path" ] || [ -z "$key_path" ] || [ -z "$base_auth_password" ] || [ ! -f "$cert_path" ] || [ ! -f "$key_path" ]; then
    echo 'IDENTITY_INCOMPLETE' >&2
    write_status failed 4
    exit 4
fi
echo "obfs_server_name=$server_name obfs_port=$OBFS_PORT"

existing_healthy=0
if [ -f "$FRAGMENT" ]; then
    existing_port="$(python3 -c "import json,sys;print((json.load(open(sys.argv[1])).get('inbounds') or [{}])[0].get('listen_port',''))" "$FRAGMENT" 2>/dev/null || true)"
    existing_obfs="$(python3 -c "import json,sys;print(((json.load(open(sys.argv[1])).get('inbounds') or [{}])[0].get('obfs') or {}).get('type',''))" "$FRAGMENT" 2>/dev/null || true)"
    existing_password="$(python3 -c "import json,sys;print(((json.load(open(sys.argv[1])).get('inbounds') or [{}])[0].get('obfs') or {}).get('password',''))" "$FRAGMENT" 2>/dev/null || true)"
    existing_auth_password="$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));u=((d.get('inbounds') or [{}])[0].get('users') or []);print((u[0].get('password','') if u else ''))" "$FRAGMENT" 2>/dev/null || true)"
    echo "obfs_lane=present frag_port=${existing_port:-?} frag_obfs=${existing_obfs:-?}"
    if [ "$existing_port" = "$OBFS_PORT" ] && [ "$existing_obfs" = 'salamander' ]; then
        if ! service_manager >/dev/null; then
            echo 'SERVICE_MANAGER_UNSUPPORTED' >&2
            write_status failed 3
            exit 3
        fi
        if ! "$SB_BINARY" check -c "$SB_CONF_DIR/config.json" >/dev/null 2>&1; then
            echo 'SINGBOX_CONFIG_INVALID' >&2
            write_status failed 5
            exit 5
        fi
        if ! service_is_active; then
            echo 'SINGBOX_SERVICE_INACTIVE' >&2
            write_status failed 5
            exit 5
        fi
        if ! ss -lun 2>/dev/null | grep -q ":$OBFS_PORT "; then
            echo 'OBFS_PORT_NOT_LISTENING' >&2
            write_status failed 5
            exit 5
        fi
        if ! ss -lun 2>/dev/null | grep -q ":$base_port "; then
            echo 'BASE_HY2_PORT_LOST' >&2
            write_status failed 5
            exit 5
        fi
        runtime_password="$(python3 - "$SB_CONF_DIR/config.json" "$OBFS_PORT" <<'PY'
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception:
    raise SystemExit(3)
port = int(sys.argv[2])
found = [
    ib for ib in data.get('inbounds', []) or []
    if ib.get('type') == 'hysteria2' and ib.get('listen_port') == port
]
if len(found) != 1:
    raise SystemExit(4)
obfs = found[0].get('obfs') or {}
if obfs.get('type') != 'salamander':
    raise SystemExit(4)
print(obfs.get('password') or '')
PY
)" || {
            echo 'OBFS_RUNTIME_CONTRACT_MISSING' >&2
            write_status failed 5
            exit 5
        }
        # A listener on the right UDP port is not enough: it can be a stale
        # merged config whose obfs object was dropped or whose salted password
        # no longer matches the source fragment. Such a lane authenticates as
        # bare Hysteria2 while reporting "healthy", which defeats the purpose
        # of the backup lane.
        if [ "$existing_password" != "$runtime_password" ]; then
            echo 'OBFS_RUNTIME_PASSWORD_DRIFT reason=stale_merge' >&2
            write_status failed 5
            exit 5
        fi
        if [ "$existing_password" = "$existing_auth_password" ]; then
            echo 'obfs_lane=drift reason=key_reuse'
        elif [ "$existing_auth_password" != "$base_auth_password" ]; then
            echo 'obfs_lane=drift reason=auth_password_mismatch'
        elif [ -n "$OBFS_PASSWORD" ] && [ "$runtime_password" != "$OBFS_PASSWORD" ]; then
            echo 'obfs_lane=rotation_pending'
        else
            existing_healthy=1
        fi
    fi
else
    echo 'obfs_lane=absent'
fi

if [ "$existing_healthy" -eq 1 ]; then
    echo 'obfs_lane=already_configured_healthy'
    write_status noop 0
    exit 0
fi

if [ "$APPLY" != '1' ]; then
    # A dry run must not overwrite the last real status: result=success from
    # a read-only pass would mask the previous genuine outcome.
    exit 0
fi
if [ -z "$OBFS_PASSWORD" ]; then
    echo 'REFUSE obfs password is required for -Apply' >&2
    write_status failed 6
    exit 6
fi
if ! service_manager >/dev/null; then
    echo 'SERVICE_MANAGER_UNSUPPORTED' >&2
    write_status failed 3
    exit 3
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/hysteria2-obfs.XXXXXX")"
chmod 700 "$backup_dir"
base_listening_before=0
if ss -lun 2>/dev/null | grep -q ":$base_port "; then base_listening_before=1; fi
obfs_listening_before=0
if [ -n "${existing_port:-}" ] && ss -lun 2>/dev/null | grep -q ":$existing_port "; then obfs_listening_before=1; fi
if [ -f "$SB_CONF_DIR/config.json" ]; then
    cp -a "$SB_CONF_DIR/config.json" "$backup_dir/config.json"
else
    : > "$backup_dir/config.absent"
fi
if [ -f "$FRAGMENT" ]; then cp -a "$FRAGMENT" "$backup_dir/fragment.json"; else : > "$backup_dir/fragment.absent"; fi
service_restarted=0
service_active_before=0
if service_is_active; then service_active_before=1; fi
tmp_fragment=""

rollback() {
    local rc="${1:-1}"
    trap - ERR HUP INT TERM
    set +e
    rollback_failed=0
    if [ -n "${tmp_fragment:-}" ]; then
        rm -f -- "$tmp_fragment" || true
    fi
    if [ -f "$backup_dir/fragment.json" ]; then
        cp -a "$backup_dir/fragment.json" "$FRAGMENT" || rollback_failed=1
    else
        rm -f "$FRAGMENT" || rollback_failed=1
    fi
    if [ -f "$backup_dir/config.json" ]; then
        cp -a "$backup_dir/config.json" "$SB_CONF_DIR/config.json" || rollback_failed=1
    elif [ -f "$backup_dir/config.absent" ]; then
        rm -f "$SB_CONF_DIR/config.json" || rollback_failed=1
    fi
    if [ "$service_restarted" -eq 1 ]; then service_restart >/dev/null 2>&1 || true; fi
    # A running process alone is not enough: verify the restored merged config
    # and compare it to the backup before declaring rollback successful.
    if [ -f "$backup_dir/config.json" ] &&
       ! cmp -s "$backup_dir/config.json" "$SB_CONF_DIR/config.json"; then
        rollback_failed=1
    fi
    if [ -f "$backup_dir/config.json" ] &&
       ! "$SB_BINARY" check -c "$SB_CONF_DIR/config.json" >/dev/null 2>&1; then
        rollback_failed=1
    fi
    # An inactive-before service in the config.absent edge restoring to
    # inactive is a correct rollback; only a service that ran before (or was
    # restarted by the failed apply) must come back active.
    if [ "$service_active_before" -eq 1 ] || [ "$service_restarted" -eq 1 ]; then
        if ! service_is_active; then
            rollback_failed=1
        fi
    fi
    if [ "$base_listening_before" -eq 1 ] &&
       ! ss -lun 2>/dev/null | grep -q ":$base_port "; then
        rollback_failed=1
    fi
    if [ "$obfs_listening_before" -eq 1 ] &&
       ! ss -lun 2>/dev/null | grep -q ":$existing_port "; then
        rollback_failed=1
    fi
    if [ "$rollback_failed" -eq 0 ]; then
        echo "ROLLBACK_VERIFIED backup=$backup_dir"
    else
        echo "ROLLBACK_FAILED backup=$backup_dir" >&2
    fi
    exit "$rc"
}
trap rollback ERR
# HUP rides in on a dropped channel (2026-11-03 orphan evidence); roll back like INT/TERM.
trap 'rollback 129' HUP
trap 'rollback 130' INT
trap 'rollback 143' TERM

tmp_fragment="$(mktemp "$FRAGMENT.XXXXXX")"
python3 - "$tmp_fragment" "$OBFS_PORT" "$OBFS_PASSWORD" "$base_auth_password" "$server_name" "$cert_path" "$key_path" <<'PY'
import json
import sys

out, port, obfs_password, auth_password, server_name, cert, key = sys.argv[1:8]
if not auth_password or not obfs_password or auth_password == obfs_password:
    raise SystemExit('obfs and authentication passwords must be non-empty and distinct')
node = {
    "inbounds": [
        {
            "type": "hysteria2",
            "tag": "HY2Obfs",
            "listen": "::",
            "listen_port": int(port),
            "users": [{"password": auth_password, "name": "hy2-obfs"}],
            "up_mbps": 100,
            "down_mbps": 50,
            "obfs": {"type": "salamander", "password": obfs_password},
            "tls": {
                "enabled": True,
                "server_name": server_name,
                "alpn": ["h3"],
                "certificate_path": cert,
                "key_path": key,
            },
        }
    ]
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(node, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
chmod --reference="$SB_SOURCE_DIR" "$tmp_fragment" 2>/dev/null || chmod 644 "$tmp_fragment"
mv -f -- "$tmp_fragment" "$FRAGMENT"
tmp_fragment=""

if ! "$SB_BINARY" merge "$SB_CONF_DIR/config.json" -C "$SB_SOURCE_DIR/" -D "$SB_CONF_DIR/" >/dev/null 2>&1; then
    echo 'SINGBOX_MERGE_FAILED' >&2
    rollback
fi
if ! "$SB_BINARY" check -c "$SB_CONF_DIR/config.json" >/dev/null 2>&1; then
    echo 'SINGBOX_CHECK_FAILED' >&2
    rollback
fi
service_restarted=1
if ! service_restart; then
    echo 'SERVICE_RESTART_FAILED' >&2
    rollback
fi
sleep 2
if ! service_is_active; then
    echo 'SINGBOX_SERVICE_INACTIVE' >&2
    rollback
fi
if ! ss -lun 2>/dev/null | grep -q ":$OBFS_PORT "; then
    echo 'OBFS_PORT_NOT_LISTENING' >&2
    rollback
fi
if ! ss -lun 2>/dev/null | grep -q ":$base_port "; then
    echo 'BASE_HY2_PORT_LOST' >&2
    rollback
fi
if ! python3 - "$SB_CONF_DIR/config.json" "$OBFS_PORT" "$OBFS_PASSWORD" "$base_auth_password" "$server_name" <<'PY'
import json
import sys

path, port, obfs_password, auth_password, server_name = sys.argv[1:6]
data = json.load(open(path, encoding='utf-8'))
found = [
    ib for ib in data.get('inbounds', []) or []
    if ib.get('type') == 'hysteria2' and ib.get('listen_port') == int(port)
]
if len(found) != 1:
    raise SystemExit(1)
ib = found[0]
obfs = ib.get('obfs') or {}
tls = ib.get('tls') or {}
users = ib.get('users') or []
ok = (
    obfs.get('type') == 'salamander'
    and obfs.get('password') == obfs_password
    and len(users) == 1
    and users[0].get('password') == auth_password
    and obfs_password != auth_password
    and tls.get('enabled') is True
    and tls.get('server_name') == server_name
)
raise SystemExit(0 if ok else 1)
PY
then
    echo 'OBFS_RUNTIME_CONTRACT_FAILED' >&2
    rollback
fi
trap - ERR HUP INT TERM
# Apply-time hygiene: keep the newest 8 lane backups so repeated applies
# cannot grow /var/backups without bound (mirrors the updater keep-8 prune).
find "$BACKUP_PARENT" -mindepth 1 -maxdepth 1 -type d -name 'hysteria2-obfs.*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | tail -n +9 | cut -d' ' -f2- \
    | while IFS= read -r old_backup_dir; do
        rm -rf -- "$old_backup_dir" || echo 'BACKUP_PRUNE_FAILED' >&2
    done
echo 'BACKUP_PRUNE scope=hysteria2-obfs policy=keep_8'
echo "APPLY_BACKUP_DIR=$backup_dir"
echo "FRAGMENT=$FRAGMENT"
echo 'HYSTERIA2_OBFS_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral).Replace("__PORT__", "$Port").Replace("__OBFS_PASSWORD__", $obfsPasswordLiteral)

$stagedSecretPath = $null
$remoteSucceeded = $false
try {
  if ($Apply -and $ObfsPassword) {
    # Prepare and protect the local copy before touching the host. A local
    # permission/disk failure therefore cannot leave the remote lane enabled
    # with no recoverable key. Dry-run never reaches this branch.
    $stagedSecretPath = New-Hysteria2ObfsSecretStage -Path $ObfsPasswordFile -Value $ObfsPassword
  }

  Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "hysteria2-obfs" -ViaTempFile
  $remoteSucceeded = $true

  if ($null -ne $stagedSecretPath) {
    Publish-Hysteria2ObfsSecretStage -StagedPath $stagedSecretPath -DestinationPath $ObfsPasswordFile
    $stagedSecretPath = $null
    Write-Output "obfs_password_file=$ObfsPasswordFile generated=$($passwordGenerated.ToString().ToLowerInvariant())"
  }
}
catch {
  if ($remoteSucceeded -and
      $null -ne $stagedSecretPath -and
      (Test-Path -LiteralPath $stagedSecretPath)) {
    throw "Remote lane applied but local secret publication failed; retained private staging file $stagedSecretPath. Original error: $($_.Exception.Message)"
  }
  throw
}
finally {
  if (-not $remoteSucceeded -and
      $null -ne $stagedSecretPath -and
      (Test-Path -LiteralPath $stagedSecretPath)) {
    Remove-Item -LiteralPath $stagedSecretPath -Force
  }
}

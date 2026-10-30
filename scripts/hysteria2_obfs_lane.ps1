#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [int]$Port = 8444,
  [string]$ObfsPassword,
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
    print('server_name=' + str(tls.get('server_name')))
    print('cert=' + str(tls.get('certificate_path')))
    print('key=' + str(tls.get('key_path')))
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
if [ -z "$server_name" ] || [ -z "$base_port" ] || [ -z "$cert_path" ] || [ -z "$key_path" ] || [ ! -f "$cert_path" ] || [ ! -f "$key_path" ]; then
    echo 'IDENTITY_INCOMPLETE' >&2
    write_status failed 4
    exit 4
fi
echo "obfs_server_name=$server_name obfs_port=$OBFS_PORT"

if [ -f "$FRAGMENT" ]; then
    existing_port="$(python3 -c "import json,sys;print((json.load(open(sys.argv[1])).get('inbounds') or [{}])[0].get('listen_port',''))" "$FRAGMENT" 2>/dev/null || true)"
    existing_obfs="$(python3 -c "import json,sys;print(((json.load(open(sys.argv[1])).get('inbounds') or [{}])[0].get('obfs') or {}).get('type',''))" "$FRAGMENT" 2>/dev/null || true)"
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
        echo 'obfs_lane=already_configured_healthy'
        write_status noop 0
        exit 0
    fi
else
    echo 'obfs_lane=absent'
fi

if [ "$APPLY" != '1' ]; then
    write_status success 0
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

rollback() {
    trap - ERR INT TERM
    set +e
    rollback_failed=0
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
    exit 1
}
trap rollback ERR
trap 'exit 130' INT
trap 'exit 143' TERM

tmp_fragment="$(mktemp "$FRAGMENT.XXXXXX")"
python3 - "$tmp_fragment" "$OBFS_PORT" "$OBFS_PASSWORD" "$server_name" "$cert_path" "$key_path" <<'PY'
import json
import sys

out, port, password, server_name, cert, key = sys.argv[1:7]
node = {
    "inbounds": [
        {
            "type": "hysteria2",
            "tag": "HY2Obfs",
            "listen": "::",
            "listen_port": int(port),
            "users": [{"password": password, "name": "hy2-obfs"}],
            "up_mbps": 100,
            "down_mbps": 50,
            "obfs": {"type": "salamander", "password": password},
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
trap - ERR INT TERM
echo "APPLY_BACKUP_DIR=$backup_dir"
echo "FRAGMENT=$FRAGMENT"
echo 'HYSTERIA2_OBFS_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral).Replace("__PORT__", "$Port").Replace("__OBFS_PASSWORD__", $obfsPasswordLiteral)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "hysteria2-obfs"

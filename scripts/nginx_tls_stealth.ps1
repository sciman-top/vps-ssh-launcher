#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
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

$applyLiteral = if ($Apply) { "1" } else { "0" }

# Two fingerprint fixes that need no service restart, only `nginx -s reload`:
#
# 1. `server_tokens off` stops nginx advertising its exact build
#    (`Server: nginx/1.31.7`), which is a version disclosure no real site
#    operator would volunteer.
# 2. Every REALITY fallback port answers *any* SNI with this host's own
#    certificate. A TLS server whose certificate ignores the requested name is
#    the opposite of how a normal vhost behaves and is trivially observable.
#    A `default_server` with `ssl_reject_handshake on` turns unknown names into
#    a clean handshake failure while the real SNI keeps serving the decoy page.
#
# The ports come from the live REALITY configs (xray target/dest and sing-box
# reality handshake), so bwg (33387) and zz (35622) need no profile table. The
# file is a conf.d drop-in: nginx.conf keeps its distro content and vasma never
# writes this name. Read-only unless -Apply.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
LOCK_FILE="${VPS_STEALTH_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
NGINX_CONF_D="${VPS_STEALTH_CONF_DIR:-/etc/nginx/conf.d}"
TARGET_FILE="$NGINX_CONF_D/vps-launcher-stealth.conf"
XRAY_CONF="${VPS_STEALTH_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
SB_SOURCE_DIR="${VPS_STEALTH_SB_SOURCE_DIR:-/etc/v2ray-agent/sing-box/conf/config}"
STATUS_DIR="${VPS_STEALTH_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
BACKUP_PARENT="${VPS_STEALTH_BACKUP_DIR:-/var/backups}"
STATUS_FILE="$STATUS_DIR/nginx-stealth.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=nginx-stealth\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

echo '==reality-fallback-ports=='
facts="$(python3 - "$XRAY_CONF" "$SB_SOURCE_DIR" <<'PY'
import glob
import json
import os
import sys

xray_dir, sb_dir = sys.argv[1:3]
seen = {}
invalid = False


def add(port, sni, source):
    if not isinstance(port, int) or port <= 0 or not sni:
        return
    previous = seen.get(port)
    if previous and previous != sni:
        print(f"conflict port={port} sni={previous},{sni}", file=sys.stderr)
        raise SystemExit(4)
    seen[port] = sni


def split_port(value):
    value = str(value or '').strip()
    if value.startswith('[') and ']' in value:
        tail = value[value.index(']') + 1:]
        return int(tail.lstrip(':') or 0)
    if value.count(':') == 1:
        try:
            return int(value.split(':', 1)[1])
        except ValueError:
            return 0
    return 0


for path in sorted(glob.glob(os.path.join(xray_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        rs = (ib.get('streamSettings') or {}).get('realitySettings')
        if not rs:
            continue
        target = rs.get('target') or rs.get('dest') or ''
        names = [str(name) for name in (rs.get('serverNames') or []) if name]
        add(split_port(target), names[0] if names else '', 'xray')

for path in sorted(glob.glob(os.path.join(sb_dir, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        tls = ib.get('tls') or {}
        reality = tls.get('reality') or {}
        if not reality or not reality.get('enabled'):
            continue
        port = (reality.get('handshake') or {}).get('server_port')
        add(port, str(tls.get('server_name') or ''), 'sing-box')

if invalid:
    print('facts=invalid')
    raise SystemExit(4)
for port in sorted(seen):
    # One field per line: a single "port=... sni=..." line would need a second
    # split in bash, and an empty value there silently shifts the arrays.
    print(f"port={port}")
    print(f"sni={seen[port]}")
if not seen:
    print('facts=not-applicable')
PY
)" || facts='facts=invalid'
printf '%s\n' "$facts"

ports=()
snies=()
while IFS= read -r line; do
    case "$line" in
        port=*) ports+=("${line#port=}") ;;
        sni=*) snies+=("${line#sni=}") ;;
    esac
done <<<"$facts"

if [ "${#ports[@]}" -eq 0 ]; then
    if printf '%s\n' "$facts" | grep -q '^facts=invalid$'; then
        echo 'stealth=invalid' >&2
        write_status failed 4
        exit 4
    fi
    echo 'stealth=not_applicable'
    write_status noop 0
    exit 0
fi

render_expected() {
    local out="$1" i port sni
    {
        printf '# Managed by vps-ssh-launcher scripts/nginx_tls_stealth.ps1.\n'
        printf '# Do not edit by hand; re-run the lane instead.\n'
        printf 'server_tokens off;\n'
        for i in "${!ports[@]}"; do
            port="${ports[$i]}"
            sni="${snies[$i]:-}"
            printf '\n# REALITY fallback for %s: unknown SNI must fail like a strict vhost.\n' "$sni"
            printf 'server {\n'
            printf '    listen %s ssl default_server;\n' "$port"
            if ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qx "\[::\]:$port"; then
                printf '    listen [::]:%s ssl default_server;\n' "$port"
            fi
            printf '    ssl_reject_handshake on;\n'
            printf '}\n'
        done
    } >"$out"
}

probe_cert() {
    # $1 port, $2 sni -> "cert" when the handshake produced a certificate.
    local out
    out="$(echo | timeout 8 openssl s_client -connect "127.0.0.1:$1" -servername "$2" 2>&1 || true)"
    if printf '%s' "$out" | grep -q 'BEGIN CERTIFICATE'; then
        printf 'cert'
    else
        printf 'none'
    fi
}

server_header_has_version() {
    # $1 port, $2 sni
    local header
    header="$(curl -sk -m 8 -D - -o /dev/null --resolve "$2:$1:127.0.0.1" "https://$2:$1/" 2>/dev/null |
        tr -d '\r' | grep -i '^server:' | head -1)"
    printf '%s' "$header" | grep -qi 'nginx/[0-9]'
}

report_state() {
    local i port sni expected mismatch=0 rejected=0 broken=0 leak=0
    expected="$(mktemp)"
    render_expected "$expected"
    for i in "${!ports[@]}"; do
        port="${ports[$i]}"
        sni="${snies[$i]:-}"
        if [ "$(probe_cert "$port" "$sni")" != 'cert' ]; then
            echo "real_sni_broken port=$port sni=$sni"
            broken=1
        fi
        if [ "$(probe_cert "$port" 'vps-launcher-probe.invalid')" = 'cert' ]; then
            echo "unknown_sni_answered port=$port"
        else
            rejected=$((rejected + 1))
        fi
        if server_header_has_version "$port" "$sni"; then
            echo "server_version_leak port=$port"
            leak=1
        fi
    done
    file_state='absent'
    if [ -f "$TARGET_FILE" ]; then
        if cmp -s "$expected" "$TARGET_FILE"; then
            file_state='match'
        else
            file_state='different'
            mismatch=1
        fi
    fi
    rm -f -- "$expected"
    echo "conf_file=$file_state"
    echo "unknown_sni_rejected=$rejected/${#ports[@]}"
    if [ "$broken" -eq 1 ]; then
        echo 'stealth=broken'
        return 2
    fi
    if [ "$file_state" = 'match' ] && [ "$rejected" -eq "${#ports[@]}" ] && [ "$leak" -eq 0 ]; then
        echo 'stealth=applied'
        return 0
    fi
    if [ "$file_state" = 'absent' ] && [ "$leak" -eq 1 ]; then
        echo 'stealth=absent'
        return 1
    fi
    echo "stealth=drift mismatch=$mismatch leak=$leak"
    return 1
}

echo '==stealth-state=='
state_rc=0
report_state || state_rc=$?
if [ "$state_rc" -eq 2 ]; then
    write_status failed 5
    exit 5
fi

# `nginx -s reload` returns once the master signals the new workers, not once
# they are serving. A probe issued immediately afterwards can still be answered
# by an old worker, so give the reload a few seconds before calling it a
# failure (the first real apply rolled back on exactly that race).
verify_settled() {
    local attempt=1
    while [ "$attempt" -le 6 ]; do
        if report_state >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        attempt=$((attempt + 1))
    done
    return 1
}

if [ "$APPLY" != '1' ]; then
    write_status success 0
    exit 0
fi
if [ "$state_rc" -eq 0 ]; then
    write_status noop 0
    exit 0
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/nginx-stealth.XXXXXX")"
chmod 700 "$backup_dir"
if [ -f "$TARGET_FILE" ]; then
    cp -a "$TARGET_FILE" "$backup_dir/previous.conf"
else
    : > "$backup_dir/previously-absent"
fi

rollback() {
    trap - ERR INT TERM
    set +e
    rollback_failed=0
    if [ -f "$backup_dir/previous.conf" ]; then
        cp -a "$backup_dir/previous.conf" "$TARGET_FILE" || rollback_failed=1
    else
        rm -f -- "$TARGET_FILE" || rollback_failed=1
    fi
    nginx -t >/dev/null 2>&1 || rollback_failed=1
    systemctl reload nginx >/dev/null 2>&1 || rollback_failed=1
    if [ "$rollback_failed" -eq 0 ]; then
        echo "ROLLBACK_VERIFIED backup=$backup_dir"
    else
        echo "ROLLBACK_FAILED backup=$backup_dir" >&2
    fi
    write_status failed 1
    exit 1
}
trap rollback ERR
trap 'exit 130' INT
trap 'exit 143' TERM

tmp_file="$(mktemp "$TARGET_FILE.XXXXXX")"
render_expected "$tmp_file"
chmod 644 "$tmp_file"
mv -f -- "$tmp_file" "$TARGET_FILE"

if ! nginx -t; then
    echo 'NGINX_TEST_FAILED' >&2
    rollback
fi
if ! systemctl reload nginx; then
    echo 'NGINX_RELOAD_FAILED' >&2
    rollback
fi

# Re-run the behavioural checks after the reload: a config that passes
# `nginx -t` can still reject the real SNI (wrong default_server ordering) and
# that would take the camouflage page offline for every client.
if ! verify_settled; then
    echo 'STEALTH_VERIFY_FAILED' >&2
    report_state >&2 || true
    rollback
fi
trap - ERR INT TERM
echo "APPLY_BACKUP_DIR=$backup_dir"
echo 'NGINX_STEALTH_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "nginx-stealth"

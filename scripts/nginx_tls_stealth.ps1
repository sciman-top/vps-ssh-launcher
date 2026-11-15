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
. (Join-Path $PSScriptRoot "lib\project_environment.ps1")

Initialize-LaneScript -ScriptRoot $PSScriptRoot -Config $Config -AllowPyLauncher

$applyLiteral = if ($Apply) { "1" } else { "0" }

$proxyTlsPortsPath = Join-Path $PSScriptRoot "lib\proxy_tls_ports.py"
$proxyTlsPortsB64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes((Get-Content -Raw -LiteralPath $proxyTlsPortsPath))
)

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
# The ports come from scripts/lib/proxy_tls_ports.py, which reads the live
# REALITY configs (xray target/dest and sing-box reality handshake) *and* the
# conf.d snippets that actually serve the subscription/decoy ports. Deriving
# only from REALITY left zz's 12378 answering every SNI with the host
# certificate (2026-11-15), while this lane reported applied. The file is a
# conf.d drop-in: nginx.conf keeps its distro content and vasma never writes
# this name. Read-only unless -Apply.
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
# These frontends are intentionally reached by IP literal by existing clients.
# Rejecting their unknown SNI would break that traffic, so the shared drift
# contract names them as explicit exceptions instead of silently treating them
# as healthy stealth ports.
UNKNOWN_SNI_EXEMPT="${VPS_STEALTH_UNKNOWN_SNI_EXEMPT:-8443 28317}"

is_unknown_sni_exempt() {
    case " $UNKNOWN_SNI_EXEMPT " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

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
SNIPPET_DIR="${VPS_STEALTH_SNIPPET_DIR:-/etc/nginx/conf.d}"
SNIPPET_EXCLUDE="${VPS_STEALTH_SNIPPET_EXCLUDE:-vps-launcher-stealth,alone,reality-handshake}"
facts="$(printf '%s' '__TLS_PORTS_HELPER_B64__' | base64 -d | python3 - "$XRAY_CONF" "$SB_SOURCE_DIR" "$SNIPPET_DIR" "$SNIPPET_EXCLUDE")" || facts='facts=invalid'
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
            if is_unknown_sni_exempt "$port"; then
                continue
            fi
            printf '\n# REALITY fallback for %s: unknown SNI must fail like a strict vhost.\n' "$sni"
            printf 'server {\n'
            printf '    listen %s ssl default_server;\n' "$port"
            # Key the v6 line on stable host capability, not a live socket
            # sample: a transient ss failure must not silently drop the v6
            # unknown-SNI rejection surface on -Apply (2026-11-02 review).
            if [ -f /proc/net/if_inet6 ]; then
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
    local i port sni expected mismatch=0 rejected=0 required=0 broken=0 leak=0
    expected="$(mktemp)"
    render_expected "$expected"
    for i in "${!ports[@]}"; do
        port="${ports[$i]}"
        sni="${snies[$i]:-}"
        if [ "$(probe_cert "$port" "$sni")" != 'cert' ]; then
            echo "real_sni_broken port=$port sni=$sni"
            broken=1
        fi
        if is_unknown_sni_exempt "$port"; then
            echo "unknown_sni_allowed port=$port"
        elif [ "$(probe_cert "$port" 'vps-launcher-probe.invalid')" = 'cert' ]; then
            echo "unknown_sni_answered port=$port"
        else
            rejected=$((rejected + 1))
        fi
        if ! is_unknown_sni_exempt "$port"; then
            required=$((required + 1))
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
    echo "unknown_sni_rejected=$rejected/$required"
    if [ "$broken" -eq 1 ]; then
        echo 'stealth=broken'
        return 2
    fi
    if [ "$file_state" = 'match' ] && [ "$rejected" -eq "$required" ] && [ "$leak" -eq 0 ]; then
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
    # A dry run must not overwrite the last real status: result=success from
    # a read-only pass would mask the previous genuine outcome.
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
    local rc="${1:-1}"
    trap - ERR HUP INT TERM
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
    write_status failed "$rc"
    exit "$rc"
}
trap rollback ERR
# HUP rides in on a dropped channel (2026-11-03 orphan evidence); roll back like INT/TERM.
trap 'rollback 129' HUP
trap 'rollback 130' INT
trap 'rollback 143' TERM

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
trap - ERR HUP INT TERM
# Apply-time hygiene: keep the newest 8 lane backups so repeated applies
# cannot grow /var/backups without bound (mirrors the updater keep-8 prune).
find "$BACKUP_PARENT" -mindepth 1 -maxdepth 1 -type d -name 'nginx-stealth.*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | tail -n +9 | cut -d' ' -f2- \
    | while IFS= read -r old_backup_dir; do
        rm -rf -- "$old_backup_dir" || echo 'BACKUP_PRUNE_FAILED' >&2
    done
echo 'BACKUP_PRUNE scope=nginx-stealth policy=keep_8'
echo "APPLY_BACKUP_DIR=$backup_dir"
echo 'NGINX_STEALTH_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral).Replace("__TLS_PORTS_HELPER_B64__", $proxyTlsPortsB64)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "nginx-stealth"

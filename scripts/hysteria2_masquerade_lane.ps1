#requires -Version 7
[CmdletBinding()]
param(
  [string]$Config,
  [Parameter(Mandatory = $true)]
  [ValidateSet("bwg", "zz")]
  [string]$Profile,
  [string]$MasqueradeUrl,
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
if (-not $MasqueradeUrl) {
  $MasqueradeUrl = @{
    "bwg" = "https://fq.sciman.top:33387/"
    "zz"  = "https://zz.sciman.top:12378/"
  }[$Profile]
}
if ($MasqueradeUrl -notmatch '^https://[A-Za-z0-9.:/_-]+$') {
  throw "-MasqueradeUrl must be an https:// URL literal without shell syntax."
}

$applyLiteral = if ($Apply) { "1" } else { "0" }

# Gives the un-obfuscated Hysteria2 base lane a plausible face: unauthenticated
# probes are proxied to the nginx camouflage vhost (proxy masquerade with a
# rewritten Host) instead of meeting a bare protocol refusal, and an explicit
# sing-box log level keeps the journal at info. Both edits are repo-owned
# idempotent normalizations over the vasma-generated base fragment
# (06_hysteria2_inbounds.json) plus a projected 97_ log fragment; upstream
# vasma is never edited. The obfs lane is deliberately untouched: salamander
# rejects non-obfuscated packets before the protocol layer, so a masquerade
# there is unreachable. Both bwg and zz currently carry a Hysteria2 base lane;
# the profile-specific defaults point at each host's existing nginx HTTPS vhost.
# Idempotent, backup-first, rollback-on-failure,
# and it never restarts a service it did not need to touch.
#
# sing-box 1.14 schema note: file masquerade accepts no path/name field, so
# the decoy is reached through the proxy type against the HTTPS camouflage
# vhost (valid LE certificate, hairpinned locally).
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
MASQUERADE_URL="__MASQUERADE_URL__"
LOCK_FILE="${VPS_HY2MASQ_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
SB_BINARY="${VPS_HY2MASQ_SB_BINARY:-/etc/v2ray-agent/sing-box/sing-box}"
SB_CONF_DIR="${VPS_HY2MASQ_SB_CONF_DIR:-/etc/v2ray-agent/sing-box/conf}"
SB_SOURCE_DIR="$SB_CONF_DIR/config"
SB_SERVICE="${VPS_HY2MASQ_SB_SERVICE:-sing-box}"
BASE_FRAGMENT="$SB_SOURCE_DIR/06_hysteria2_inbounds.json"
LOG_FRAGMENT="$SB_SOURCE_DIR/97_vps_ssh_launcher_singbox_log.json"
STATUS_DIR="${VPS_HY2MASQ_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
BACKUP_PARENT="${VPS_HY2MASQ_BACKUP_DIR:-/var/backups}"
STATUS_FILE="$STATUS_DIR/hysteria2-masquerade.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=hysteria2-masquerade\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

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

if [ ! -f "$BASE_FRAGMENT" ]; then
    echo '==hysteria2-masquerade=='
    echo 'hysteria2_base=absent'
    echo 'MASQUERADE_NOT_APPLICABLE'
    write_status noop 0
    exit 0
fi

echo '==hysteria2-masquerade=='
state="$(python3 - "$BASE_FRAGMENT" "$LOG_FRAGMENT" "$MASQUERADE_URL" <<'PY'
import json
import os
import sys

data = json.load(open(sys.argv[1], encoding='utf-8'))
want = {"type": "proxy", "url": sys.argv[3], "rewrite_host": True}
found = False
set_ok = False
for ib in data.get('inbounds', []) or []:
    if ib.get('type') == 'hysteria2' and ib.get('listen_port') == 443:
        found = True
        set_ok = ib.get('masquerade') == want
log_path = sys.argv[2]
log_ok = False
if os.path.isfile(log_path):
    try:
        log_ok = json.load(open(log_path, encoding='utf-8')).get('log', {}).get('level') == 'info'
    except Exception:
        log_ok = False
print(f'base_present={"yes" if found else "no"}')
print(f'masquerade_set={"yes" if set_ok else "no"}')
print(f'log_level_pinned={"yes" if log_ok else "no"}')
PY
)" || { echo 'STATE_RESOLVE_FAILED' >&2; write_status failed 4; exit 4; }
printf '%s\n' "$state"
base_present="$(printf '%s\n' "$state" | sed -n 's/^base_present=//p')"
masquerade_set="$(printf '%s\n' "$state" | sed -n 's/^masquerade_set=//p')"
log_pinned="$(printf '%s\n' "$state" | sed -n 's/^log_level_pinned=//p')"
if [ "$base_present" != "yes" ]; then
    echo 'BASE_LANE_MISSING' >&2
    write_status failed 4
    exit 4
fi
if [ "$masquerade_set" = "yes" ] && [ "$log_pinned" = "yes" ]; then
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
    if ! ss -lun 2>/dev/null | grep -q ':443 '; then
        echo 'BASE_HY2_PORT_NOT_LISTENING' >&2
        write_status failed 5
        exit 5
    fi
    echo 'masquerade=already_configured_healthy'
    write_status noop 0
    exit 0
fi
if [ "$APPLY" != '1' ]; then
    write_status success 0
    exit 0
fi
if ! service_manager >/dev/null; then
    echo 'SERVICE_MANAGER_UNSUPPORTED' >&2
    write_status failed 3
    exit 3
fi
if ! service_is_active; then
    echo 'SINGBOX_SERVICE_INACTIVE' >&2
    write_status failed 5
    exit 5
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/hysteria2-masquerade.XXXXXX")"
chmod 700 "$backup_dir"
service_active_before=0
if service_is_active; then service_active_before=1; fi
base_listening_before=0
if ss -lun 2>/dev/null | grep -q ':443 '; then base_listening_before=1; fi
obfs_listening_before=0
if ss -lun 2>/dev/null | grep -q ':8444 '; then obfs_listening_before=1; fi
cp -a "$BASE_FRAGMENT" "$backup_dir/06_hysteria2_inbounds.json"
if [ -f "$LOG_FRAGMENT" ]; then cp -a "$LOG_FRAGMENT" "$backup_dir/log-fragment.json"; else : > "$backup_dir/log-fragment.absent"; fi
cp -a "$SB_CONF_DIR/config.json" "$backup_dir/config.json"
service_restarted=0

rollback() {
    trap - ERR INT TERM
    set +e
    rollback_failed=0
    cp -a "$backup_dir/06_hysteria2_inbounds.json" "$BASE_FRAGMENT" || rollback_failed=1
    if [ -f "$backup_dir/log-fragment.json" ]; then
        cp -a "$backup_dir/log-fragment.json" "$LOG_FRAGMENT" || rollback_failed=1
    else
        rm -f "$LOG_FRAGMENT" || rollback_failed=1
    fi
    cp -a "$backup_dir/config.json" "$SB_CONF_DIR/config.json" || rollback_failed=1
    if [ "$service_restarted" -eq 1 ]; then service_restart >/dev/null 2>&1 || true; fi
    if ! cmp -s "$backup_dir/config.json" "$SB_CONF_DIR/config.json"; then
        rollback_failed=1
    fi
    if ! "$SB_BINARY" check -c "$SB_CONF_DIR/config.json" >/dev/null 2>&1; then
        rollback_failed=1
    fi
    if [ "$service_active_before" -eq 1 ] || [ "$service_restarted" -eq 1 ]; then
        if ! service_is_active; then
            rollback_failed=1
        fi
    fi
    if [ "$base_listening_before" -eq 1 ] &&
       ! ss -lun 2>/dev/null | grep -q ':443 '; then
        rollback_failed=1
    fi
    if [ "$obfs_listening_before" -eq 1 ] &&
       ! ss -lun 2>/dev/null | grep -q ':8444 '; then
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

edit_result="$(python3 - "$BASE_FRAGMENT" "$MASQUERADE_URL" <<'PY'
import json
import sys

path, masq_url = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding='utf-8'))
want = {"type": "proxy", "url": masq_url, "rewrite_host": True}
touched = False
for ib in data.get('inbounds', []) or []:
    if ib.get('type') == 'hysteria2' and ib.get('listen_port') == 443:
        if ib.get('masquerade') != want:
            ib['masquerade'] = want
            touched = True
if touched:
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
print('fragment_changed=yes' if touched else 'fragment_changed=no')
PY
)" || { echo 'FRAGMENT_EDIT_FAILED' >&2; rollback; }
printf '%s\n' "$edit_result"
if [ ! -f "$LOG_FRAGMENT" ]; then
    tmp_log="$(mktemp "$LOG_FRAGMENT.XXXXXX")"
    printf '{"log": {"level": "info"}}\n' > "$tmp_log"
    chmod 644 "$tmp_log"
    mv -f -- "$tmp_log" "$LOG_FRAGMENT"
fi

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
sleep 1
service_is_active || { echo 'SINGBOX_SERVICE_INACTIVE' >&2; rollback; }
ss -lun 2>/dev/null | grep -q ':443 ' || { echo 'BASE_HY2_PORT_NOT_LISTENING' >&2; rollback; }
if [ "$obfs_listening_before" -eq 1 ] &&
   ! ss -lun 2>/dev/null | grep -q ':8444 '; then
    echo 'OBFS_PORT_NOT_LISTENING' >&2
    rollback
fi
if ! python3 - "$SB_CONF_DIR/config.json" "$MASQUERADE_URL" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding='utf-8'))
want = {"type": "proxy", "url": sys.argv[2], "rewrite_host": True}
ok = any(
    ib.get('type') == 'hysteria2'
    and ib.get('listen_port') == 443
    and ib.get('masquerade') == want
    for ib in data.get('inbounds', []) or []
)
ok = ok and (data.get('log') or {}).get('level') == 'info'
raise SystemExit(0 if ok else 1)
PY
then
    echo 'MERGE_CONTRACT_FAILED' >&2
    rollback
fi
echo 'HYSTERIA2_MASQUERADE_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral).Replace("__MASQUERADE_URL__", $MasqueradeUrl)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "hysteria2-masquerade"

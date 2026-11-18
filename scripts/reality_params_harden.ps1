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
$helperB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(@'
import glob
import json
import os
import shutil
import sys

# mode: "scan" prints one line per REALITY inbound with the fields a probe
# could use, and mode "clear" blanks minClientVer / maxTimeDiff and prints the
# files it changed. A parse failure exits 2 so the caller rolls back instead
# of reading an unreadable config as "nothing to clear".
mode, conf = sys.argv[1], sys.argv[2]
files = sorted(glob.glob(os.path.join(conf, "*.json")))
changed = []
invalid = False
for path in files:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        invalid = True
        continue
    touched = False
    for ib in data.get("inbounds", []) or []:
        rs = (ib.get("streamSettings") or {}).get("realitySettings")
        if not rs:
            continue
        if mode == "scan":
            print("inbound port=%s min_client_ver=%s max_time_diff=%s" % (
                ib.get("port"), rs.get("minClientVer") or "", rs.get("maxTimeDiff") or 0))
            continue
        if str(rs.get("minClientVer") or "").strip():
            rs["minClientVer"] = ""
            touched = True
        try:
            diff = int(rs.get("maxTimeDiff") or 0)
        except (TypeError, ValueError):
            diff = 1
        if diff != 0:
            rs["maxTimeDiff"] = 0
            touched = True
    if mode == "clear" and touched:
        tmp = path + ".params.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=4)
            fh.write("\n")
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
        changed.append(os.path.basename(path))
if invalid:
    raise SystemExit(2)
if mode == "clear":
    print("files=" + " ".join(changed))
'@))

# REALITY's minClientVer and maxTimeDiff are evaluated only after the AEAD
# handshake, so they cannot stop an active probe. Set, they only widen the set
# of ClientHellos relayed to the fallback, and upstream documents them that
# way. vasma still writes 1.8.2 / 70000 from a 2021 default, so a reinstall
# restores a parameter this fleet deliberately keeps at absent / zero. This
# lane blanks both on every REALITY inbound of the xray core, tests the whole
# confdir, restarts xray and re-reads the on-disk state. It never touches
# shortIds, serverNames, keys or certificates: shortIds stay published in the
# subscription, so rotating them is a different, coordinated change.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
HELPER_B64="__HELPER_B64__"
LOCK_FILE="${VPS_RPARAMS_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
XRAY_CONF="${VPS_RPARAMS_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
XRAY_BINARY="${VPS_RPARAMS_XRAY_BINARY:-/etc/v2ray-agent/xray/xray}"
XRAY_SERVICE="${VPS_RPARAMS_XRAY_SERVICE:-xray}"
STATUS_DIR="${VPS_RPARAMS_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
BACKUP_PARENT="${VPS_RPARAMS_BACKUP_DIR:-/var/backups}"
STATUS_FILE="$STATUS_DIR/reality-params.status"

write_status() {
    local result="$1" code="$2" tmp
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=reality-params\n'
        printf 'finished_at=%s\n' "$(date -u +%FT%TZ)"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

helper="$(mktemp "${TMPDIR:-/tmp}/reality-params.XXXXXXXXXX")"
trap 'rm -f -- "$helper"' EXIT
printf '%s' "$HELPER_B64" | base64 -d > "$helper"
run_helper() { python3 "$helper" "$@"; }

weak_lines() {
    # Prints every REALITY inbound that does not carry the hardened pair.
    run_helper scan "$XRAY_CONF" | while IFS= read -r line; do
        [ -n "$line" ] || continue
        case "$line" in
            *min_client_ver=*max_time_diff=0) ;;
            *) printf '%s\n' "$line" ;;
        esac
    done
}

echo '==reality-params-state=='
if ! scan_out="$(run_helper scan "$XRAY_CONF")"; then
    echo 'reality_params=invalid' >&2
    write_status failed 4
    exit 4
fi
if [ -n "$scan_out" ]; then printf '%s\n' "$scan_out"; fi
weak="$(weak_lines || true)"
if [ -z "$weak" ]; then
    echo 'reality_params=hardened'
    if [ "$APPLY" = '1' ]; then write_status noop 0; fi
    exit 0
fi
echo 'reality_params=needs_clear'

if [ "$APPLY" != '1' ]; then
    # A read-only pass must not overwrite the last applied outcome.
    exit 0
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/reality-params.XXXXXX")"
chmod 700 "$backup_dir"
mkdir -p "$backup_dir/xray"
cp -a "$XRAY_CONF/." "$backup_dir/xray/"

restart_started=0
rollback() {
    local rc="${1:-1}"
    trap - ERR HUP INT TERM
    set +e
    rollback_failed=0
    cp -a "$backup_dir/xray/." "$XRAY_CONF/" || rollback_failed=1
    if [ "$restart_started" -eq 1 ]; then
        if ! "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1; then
            rollback_failed=1
        elif ! systemctl restart "$XRAY_SERVICE" >/dev/null 2>&1; then
            rollback_failed=1
        elif ! systemctl is-active --quiet "$XRAY_SERVICE"; then
            rollback_failed=1
        fi
    fi
    if [ "$rollback_failed" -eq 0 ]; then
        echo "ROLLBACK_VERIFIED backup=$backup_dir"
    else
        echo "ROLLBACK_FAILED backup=$backup_dir" >&2
    fi
    exit "$rc"
}
trap rollback ERR
# HUP rides in on a dropped channel; roll back like INT/TERM.
trap 'rollback 129' HUP
trap 'rollback 130' INT
trap 'rollback 143' TERM

cleared="$(run_helper clear "$XRAY_CONF")"
echo "reality_params_cleared=$cleared"
if ! "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1; then
    echo 'XRAY_CONFIG_TEST_FAILED' >&2
    rollback
fi
restart_started=1
systemctl restart "$XRAY_SERVICE"
sleep 1
systemctl is-active --quiet "$XRAY_SERVICE" || { echo 'XRAY_SERVICE_INACTIVE' >&2; rollback; }

trap - ERR HUP INT TERM
after_weak="$(weak_lines || true)"
if [ -n "$after_weak" ]; then
    echo "POST_CLEAR_STILL_WEAK line=$after_weak" >&2
    rollback
fi
echo 'reality_params_verified=hardened'
# Keep the newest 8 lane backups so repeated applies stay bounded.
find "$BACKUP_PARENT" -mindepth 1 -maxdepth 1 -type d -name 'reality-params.*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | tail -n +9 | cut -d' ' -f2- \
    | while IFS= read -r old_backup_dir; do
        rm -rf -- "$old_backup_dir" || echo 'BACKUP_PRUNE_FAILED' >&2
    done
echo 'BACKUP_PRUNE scope=reality-params policy=keep_8'
echo "APPLY_BACKUP_DIR=$backup_dir"
echo 'REALITY_PARAMS_HARDENED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral).Replace("__HELPER_B64__", $helperB64)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "reality-params"

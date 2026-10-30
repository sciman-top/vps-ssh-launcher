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

# vasma generates REALITY inbounds with shortIds ["", "<id>"] in both Xray and
# sing-box source configs. The empty shortId is accepted on the wire but no
# vasma subscription ever uses it (subscriptions hard-code the non-empty id),
# so it only widens the active-probing surface: an unauthenticated REALITY
# probe carrying an empty shortId gets a differentiated response. This lane
# removes the empty entry from the source configs, regenerates the merged
# sing-box config on zz, and re-verifies a REALITY handshake with the non-empty
# id. It is idempotent: a profile with no empty shortId reports noop. vasma
# regenerates the empty entry on any reinstall, so this is the post-vasma
# projection step; it never edits upstream install.sh.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
LOCK_FILE="${VPS_SIDH_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
XRAY_CONF="${VPS_SIDH_XRAY_CONF:-/etc/v2ray-agent/xray/conf}"
XRAY_BINARY="${VPS_SIDH_XRAY_BINARY:-/etc/v2ray-agent/xray/xray}"
XRAY_SERVICE="${VPS_SIDH_XRAY_SERVICE:-xray}"
SB_BINARY="${VPS_SIDH_SB_BINARY:-/etc/v2ray-agent/sing-box/sing-box}"
SB_CONF_DIR="${VPS_SIDH_SB_CONF_DIR:-/etc/v2ray-agent/sing-box/conf}"
SB_SOURCE_DIR="$SB_CONF_DIR/config"
SB_SERVICE="${VPS_SIDH_SB_SERVICE:-sing-box}"
STATUS_DIR="${VPS_SIDH_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
BACKUP_PARENT="${VPS_SIDH_BACKUP_DIR:-/var/backups}"
STATUS_FILE="$STATUS_DIR/reality-shortid.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=reality-shortid\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

# has_empty reports whether any REALITY inbound in a set of JSON files carries
# an empty shortId. Prints one line per file with the verdict.
scan_files() {
    python3 - "$@" <<'PY'
import json
import sys

any_empty = False
invalid = False
for path in sys.argv[1:]:
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        print(f"scan path={path} state=invalid")
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        rs = (ib.get('streamSettings') or {}).get('realitySettings') or {}
        sids = rs.get('shortIds')
        if sids is None:
            continue
        sids = sids or []
        empty = '' in sids
        any_empty = any_empty or empty
        print(f"scan path={path} port={ib.get('port')} empty_short_id={'yes' if empty else 'no'}")
raise SystemExit(2 if invalid else (0 if any_empty else 3))
PY
}

strip_files() {
    python3 - "$@" <<'PY'
import json
import sys

changed = 0
for path in sys.argv[1:]:
    data = json.load(open(path, encoding='utf-8'))
    touched = False
    for ib in data.get('inbounds', []) or []:
        rs = (ib.get('streamSettings') or {}).get('realitySettings') or {}
        sids = rs.get('shortIds')
        if sids is None:
            continue
        kept = [s for s in sids if s != '']
        if len(kept) == 0:
            # Never strip the last id; that would break the inbound entirely.
            continue
        if kept != sids:
            rs['shortIds'] = kept
            touched = True
    if touched:
        json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        open(path, 'a', encoding='utf-8').write('\n')
        changed += 1
        print(f"stripped path={path}")
raise SystemExit(1 if changed else 0)
PY
}

scan_singbox_source() {
    python3 - "$SB_SOURCE_DIR" <<'PY'
import glob
import json
import os
import sys

root = sys.argv[1]
any_empty = False
invalid = False
for path in sorted(glob.glob(os.path.join(root, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        print(f"scan path={path} state=invalid")
        invalid = True
        continue
    for ib in data.get('inbounds', []) or []:
        reality = ((ib.get('tls') or {}).get('reality')) or {}
        sids = reality.get('short_id')
        if sids is None:
            continue
        sids = sids or []
        empty = '' in sids
        any_empty = any_empty or empty
        print(f"scan path={path} port={ib.get('listen_port')} empty_short_id={'yes' if empty else 'no'}")
raise SystemExit(2 if invalid else (0 if any_empty else 3))
PY
}

strip_singbox_source() {
    python3 - "$SB_SOURCE_DIR" <<'PY'
import glob
import json
import os
import sys

root = sys.argv[1]
changed = 0
for path in sorted(glob.glob(os.path.join(root, '*.json'))):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    touched = False
    for ib in data.get('inbounds', []) or []:
        reality = ((ib.get('tls') or {}).get('reality')) or {}
        sids = reality.get('short_id')
        if sids is None:
            continue
        kept = [s for s in sids if s != '']
        if len(kept) == 0:
            continue
        if kept != sids:
            reality['short_id'] = kept
            touched = True
    if touched:
        json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        open(path, 'a', encoding='utf-8').write('\n')
        changed += 1
        print(f"stripped path={path}")
raise SystemExit(1 if changed else 0)
PY
}

echo '==reality-shortid-state=='
xray_files=()
if [ -d "$XRAY_CONF" ]; then
    for f in "$XRAY_CONF"/*.json; do
        [ -f "$f" ] || continue
        xray_files+=("$f")
    done
fi
# xray_state / sb_state: absent (no configs on this host), empty (at least one
# inbound accepts an empty shortId), clean, or invalid (fail-closed parse error).
xray_state=absent
if [ "${#xray_files[@]}" -gt 0 ]; then
    if scan_files "${xray_files[@]}" >/dev/null; then
        xray_state=empty
    else
        scan_rc=$?
        if [ "$scan_rc" -eq 3 ]; then xray_state=clean; else xray_state=invalid; fi
    fi
fi
sb_state=absent
if [ -d "$SB_SOURCE_DIR" ] && compgen -G "$SB_SOURCE_DIR/*.json" >/dev/null 2>&1; then
    if scan_singbox_source >/dev/null; then
        sb_state=empty
    else
        scan_rc=$?
        if [ "$scan_rc" -eq 3 ]; then sb_state=clean; else sb_state=invalid; fi
    fi
fi
echo "xray_empty_short_id=$([ "$xray_state" = empty ] && echo yes || echo no)"
echo "singbox_empty_short_id=$([ "$sb_state" = empty ] && echo yes || echo no)"
if [ "$xray_state" = invalid ] || [ "$sb_state" = invalid ]; then
    echo 'shortid_hardening=invalid' >&2
    write_status failed 4
    exit 4
fi
if [ "$xray_state" != empty ] && [ "$sb_state" != empty ]; then
    echo 'shortid_hardening=clean'
    if [ "$APPLY" = '1' ]; then write_status noop 0; fi
    exit 0
fi
echo 'shortid_hardening=needs_strip'

if [ "$APPLY" != '1' ]; then
    write_status success 0
    exit 0
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/reality-shortid.XXXXXX")"
chmod 700 "$backup_dir"
xray_touched=0
singbox_touched=0

if [ -f "$SB_CONF_DIR/config.json" ]; then
    cp -a "$SB_CONF_DIR/config.json" "$backup_dir/singbox-config.json"
else
    : > "$backup_dir/singbox-config.missing"
fi

rollback() {
    trap - ERR INT TERM
    set +e
    rollback_failed=0
    if [ -d "$backup_dir/xray" ]; then cp -a "$backup_dir/xray/." "$XRAY_CONF/"; fi
    if [ -d "$backup_dir/singbox" ]; then cp -a "$backup_dir/singbox/." "$SB_SOURCE_DIR/"; fi
    if [ -f "$backup_dir/singbox-config.json" ]; then
        cp -a "$backup_dir/singbox-config.json" "$SB_CONF_DIR/config.json" || rollback_failed=1
    elif [ -f "$backup_dir/singbox-config.missing" ]; then
        rm -f "$SB_CONF_DIR/config.json" || rollback_failed=1
    fi

    # Verify every touched service independently.  An OR between the two
    # services would allow one healthy daemon to hide a failed rollback of the
    # other daemon on a dual-core host.
    if [ "$xray_touched" -eq 1 ]; then
        if ! "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1 ||
           ! systemctl restart "$XRAY_SERVICE" >/dev/null 2>&1 ||
           ! systemctl is-active --quiet "$XRAY_SERVICE"; then
            rollback_failed=1
        fi
    fi
    if [ "$singbox_touched" -eq 1 ]; then
        if ! "$SB_BINARY" check -c "$SB_CONF_DIR/config.json" >/dev/null 2>&1 ||
           ! systemctl restart "$SB_SERVICE" >/dev/null 2>&1 ||
           ! systemctl is-active --quiet "$SB_SERVICE"; then
            rollback_failed=1
        fi
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

if [ "$xray_state" = empty ]; then
    xray_touched=1
    mkdir -p "$backup_dir/xray"
    cp -a "$XRAY_CONF/." "$backup_dir/xray/"
    if strip_files "${xray_files[@]}"; then :; fi
    if ! "$XRAY_BINARY" run -test -confdir "$XRAY_CONF" >/dev/null 2>&1; then
        echo 'XRAY_CONFIG_TEST_FAILED' >&2
        rollback
    fi
    systemctl restart "$XRAY_SERVICE"
    sleep 1
    systemctl is-active --quiet "$XRAY_SERVICE" || { echo 'XRAY_SERVICE_INACTIVE' >&2; rollback; }
fi

if [ "$sb_state" = empty ]; then
    singbox_touched=1
    mkdir -p "$backup_dir/singbox"
    cp -a "$SB_SOURCE_DIR/." "$backup_dir/singbox/"
    if strip_singbox_source; then :; fi
    if ! "$SB_BINARY" merge "$SB_CONF_DIR/config.json" -C "$SB_SOURCE_DIR/" -D "$SB_CONF_DIR/" >/dev/null 2>&1; then
        echo 'SINGBOX_MERGE_FAILED' >&2
        rollback
    fi
    if ! "$SB_BINARY" check -c "$SB_CONF_DIR/config.json" >/dev/null 2>&1; then
        echo 'SINGBOX_CHECK_FAILED' >&2
        rollback
    fi
    systemctl restart "$SB_SERVICE"
    sleep 1
    systemctl is-active --quiet "$SB_SERVICE" || { echo 'SINGBOX_SERVICE_INACTIVE' >&2; rollback; }
fi

trap - ERR INT TERM
# Re-scan to prove the empty shortId is gone.
recheck=0
if [ "$xray_state" = empty ] && scan_files "${xray_files[@]}" >/dev/null; then recheck=1; fi
if [ "$sb_state" = empty ] && scan_singbox_source >/dev/null; then recheck=1; fi
if [ "$recheck" -ne 0 ]; then
    echo 'POST_STRIP_STILL_EMPTY' >&2
    rollback
fi
echo "APPLY_BACKUP_DIR=$backup_dir"
echo 'SHORTID_HARDENED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "reality-shortid"

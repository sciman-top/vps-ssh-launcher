#!/usr/bin/env bash
# Update only the mack-a/v2ray-agent management script.
#
# This file deliberately does not execute install.sh, vasma, menu option 17,
# or any core installation path. It atomically replaces install.sh after
# validating the candidate, then checks the already-running proxy services.
# Xray/sing-box binaries and configuration are outside this updater's write
# set.
set -Eeuo pipefail

INSTALL_SCRIPT="/etc/v2ray-agent/install.sh"
SOURCE_URL="https://raw.githubusercontent.com/mack-a/v2ray-agent/master/install.sh"
LOCK_FILE="/run/vps-ssh-launcher-maintenance.lock"
LOG_FILE="/var/log/vps-launcher-v2ray-agent-update.log"
BACKUP_PREFIX="/var/backups/v2ray-agent-script-update"
MODE="apply"
BACKUP_DIR=""
CANDIDATE=""
REPLACED=0

log() {
    if [ ! -e "$LOG_FILE" ]; then
        install -m 600 -o root -g root /dev/null "$LOG_FILE" 2>/dev/null || true
    fi
    printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG_FILE"
}

cleanup() {
    if [ -n "$CANDIDATE" ] && [ -e "$CANDIDATE" ]; then
        rm -f -- "$CANDIDATE"
    fi
}
trap cleanup EXIT INT TERM

rollback() {
    local rc="$1"
    if [ "$REPLACED" != '1' ] || [ -z "$BACKUP_DIR" ] ||
       [ ! -f "$BACKUP_DIR/install.sh" ]; then
        log "ROLLBACK_NOT_NEEDED rc=$rc"
        return 0
    fi
    if cp -a -- "$BACKUP_DIR/install.sh" "$INSTALL_SCRIPT" &&
       chmod 700 -- "$INSTALL_SCRIPT" &&
       chown root:root -- "$INSTALL_SCRIPT" &&
       bash -n "$INSTALL_SCRIPT" &&
       verify_services; then
        log "ROLLBACK_VERIFIED backup=$BACKUP_DIR"
        return 0
    fi
    log "ROLLBACK_FAILED backup=$BACKUP_DIR"
    return 1
}

on_error() {
    local rc="$?"
    trap - ERR INT TERM
    log "ERROR updater_exit=$rc"
    rollback "$rc" || true
    exit "$rc"
}
trap on_error ERR INT TERM

usage() {
    cat <<'EOF'
Usage: v2ray-agent-script-update.sh [--check|--apply]

--check  Download and validate the official candidate without replacing
         /etc/v2ray-agent/install.sh.
--apply  Download, validate, and atomically replace install.sh when its hash
         changes. This is the default cron mode.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --check) MODE="check" ;;
        --apply) MODE="apply" ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        log "REFUSE missing_dependency=$1"
        exit 3
    }
}

for command_name in bash curl sha256sum flock install stat mktemp mv cp chmod chown grep; do
    require_command "$command_name"
done

if [ "$(id -u)" != '0' ]; then
    log "REFUSE root_required"
    exit 4
fi
if [ ! -f "$INSTALL_SCRIPT" ] || [ -L "$INSTALL_SCRIPT" ]; then
    log "REFUSE install_script_missing_or_symlink path=$INSTALL_SCRIPT"
    exit 5
fi
if [ ! -r "$INSTALL_SCRIPT" ]; then
    log "REFUSE install_script_unreadable path=$INSTALL_SCRIPT"
    exit 5
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "DEFERRED_BUSY lock=$LOCK_FILE"
    exit 75
fi

service_manager() {
    if command -v systemctl >/dev/null 2>&1 && systemctl cat "$1" >/dev/null 2>&1; then
        printf 'systemctl\n'
    elif command -v rc-service >/dev/null 2>&1; then
        printf 'rc-service\n'
    else
        return 1
    fi
}

service_is_active() {
    local manager
    manager="$(service_manager "$1")" || return 1
    if [ "$manager" = 'systemctl' ]; then
        systemctl is-active --quiet "$1"
    else
        rc-service "$1" status >/dev/null 2>&1
    fi
}

verify_services() {
    local service
    for service in xray nginx fail2ban; do
        if service_manager "$service" >/dev/null 2>&1; then
            service_is_active "$service" || {
                log "VERIFY_FAILED service=$service"
                return 1
            }
        fi
    done
    if [ -x /etc/v2ray-agent/xray/xray ] &&
       [ -d /etc/v2ray-agent/xray/conf ]; then
        /etc/v2ray-agent/xray/xray run -test \
            -confdir /etc/v2ray-agent/xray/conf >/dev/null 2>&1 || {
            log "VERIFY_FAILED xray_config"
            return 1
        }
    fi
    log "SERVICES_OK"
}

validate_candidate() {
    local bytes
    bytes="$(stat -c '%s' "$CANDIDATE")"
    # The current upstream script is about 400 KiB. Keep a broad bound so a
    # truncated response or an unexpectedly huge replacement fails closed.
    if [ "$bytes" -lt 100000 ] || [ "$bytes" -gt 2000000 ]; then
        log "REFUSE candidate_size=$bytes"
        return 1
    fi
    bash -n "$CANDIDATE" || {
        log "REFUSE candidate_syntax"
        return 1
    }
    for anchor in \
        'coreVersionManageMenu' \
        'xrayVersionManageMenu' \
        '16.core管理' \
        '17.更新脚本' \
        'updateV2RayAgent'; do
        grep -qF "$anchor" "$CANDIDATE" || {
            log "REFUSE candidate_anchor_missing=$anchor"
            return 1
        }
    done
    grep -Eq '当前版本：v[0-9]+\.[0-9]+\.[0-9]+' "$CANDIDATE" || {
        log "REFUSE candidate_version_marker_missing"
        return 1
    }
    return 0
}

current_sha="$(sha256sum "$INSTALL_SCRIPT" | awk '{print $1}')"
current_bytes="$(stat -c '%s' "$INSTALL_SCRIPT")"
log "START mode=$MODE current_sha=$current_sha current_bytes=$current_bytes source=$SOURCE_URL"

CANDIDATE="$(mktemp /etc/v2ray-agent/install.sh.candidate.XXXXXX)"
chmod 600 "$CANDIDATE"
if ! curl --fail --silent --show-error --location \
    --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 120 \
    --output "$CANDIDATE" "$SOURCE_URL"; then
    log "UNVERIFIED source_fetch_failed"
    exit 10
fi
validate_candidate
candidate_sha="$(sha256sum "$CANDIDATE" | awk '{print $1}')"
candidate_bytes="$(stat -c '%s' "$CANDIDATE")"
candidate_version="$(grep -oE '当前版本：v[0-9]+\.[0-9]+\.[0-9]+' "$CANDIDATE" | head -n 1 | sed 's/.*：//')"
log "CANDIDATE sha=$candidate_sha bytes=$candidate_bytes version=$candidate_version"

if [ "$MODE" = 'check' ]; then
    if [ "$candidate_sha" = "$current_sha" ]; then
        log "CHECK_NO_CHANGE"
    else
        log "CHECK_UPDATE_AVAILABLE"
    fi
    verify_services
    exit 0
fi

if [ "$candidate_sha" = "$current_sha" ]; then
    log "NO_CHANGE"
    verify_services
    exit 0
fi

BACKUP_DIR="$(mktemp -d "${BACKUP_PREFIX}.XXXXXX")"
chmod 700 "$BACKUP_DIR"
cp -a -- "$INSTALL_SCRIPT" "$BACKUP_DIR/install.sh"
printf '%s\n' "$current_sha" > "$BACKUP_DIR/current.sha256"
printf '%s\n' "$candidate_sha" > "$BACKUP_DIR/candidate.sha256"
chmod 600 "$BACKUP_DIR/current.sha256" "$BACKUP_DIR/candidate.sha256"

chmod 700 "$CANDIDATE"
chown root:root "$CANDIDATE"
mv -f -- "$CANDIDATE" "$INSTALL_SCRIPT"
CANDIDATE=""
REPLACED=1
if ! bash -n "$INSTALL_SCRIPT" || ! verify_services; then
    log "APPLY_VERIFY_FAILED backup=$BACKUP_DIR"
    rollback 11 || true
    exit 11
fi
REPLACED=0
log "APPLIED backup=$BACKUP_DIR sha=$candidate_sha version=$candidate_version"
exit 0

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

# Removes only the exact ArgoX-named ACCEPT rows left after that temporary
# tunnel was disabled. Port ranges alone are not a deletion rule: a future
# service may legitimately reuse one, and an uncommented rule may be a
# different security control. The service must already be inactive, no port
# in the range may listen, and every persisted matching row is backed up.
# This avoids editing vasma-owned or unrelated firewall rules.
$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
PROFILE="__PROFILE__"
LOCK_FILE="${VPS_STALE_FW_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
RULES_FILE="${VPS_STALE_FW_RULES_FILE:-/etc/iptables/rules.v4}"
STATUS_DIR="${VPS_STALE_FW_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
BACKUP_PARENT="${VPS_STALE_FW_BACKUP_DIR:-/var/backups}"
STATUS_FILE="$STATUS_DIR/stale-firewall.status"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=stale-firewall\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

matching_rows() {
    iptables-save -t filter 2>/dev/null |
        grep -F -- '-A INPUT' |
        grep -F -- '-j ACCEPT' |
        grep -E -- '--dport (3000[0-9]|30010|8080)( |$)' |
        grep -F -- '--comment "ArgoX PORT ' || true
}

echo '==stale-firewall=='
if [ "$PROFILE" = 'bwg' ]; then
    echo 'stale_firewall=not-applicable'
    write_status noop 0
    exit 0
fi
if systemctl is-active --quiet argo 2>/dev/null; then
    echo 'STALE_FIREWALL_SERVICE_ACTIVE'
    write_status failed 4
    exit 4
fi
echo 'argo_service=inactive'
if ss -Hlntu 2>/dev/null | grep -Eq ':(3000[0-9]|30010|8080)\b'; then
    echo 'STALE_FIREWALL_PORT_IN_USE'
    write_status failed 4
    exit 4
fi
echo 'argo_listeners=none'

rows="$(matching_rows)"
count=0
[ -n "$rows" ] && count="$(printf '%s\n' "$rows" | wc -l | tr -d '[:space:]')"
persisted=0
if [ -f "$RULES_FILE" ]; then
    persisted="$(grep -F -- '--comment "ArgoX PORT ' "$RULES_FILE" 2>/dev/null | grep -E -- '--dport (3000[0-9]|30010|8080)( |$)' | wc -l | tr -d '[:space:]' || true)"
fi
echo "stale_firewall_rules=$count persisted=$persisted"
if [ "$count" -eq 0 ]; then
    echo 'stale_firewall=absent'
    write_status noop 0
    exit 0
fi
printf '%s\n' "$rows" | sed 's/^/stale_firewall_row=/'
if [ "$APPLY" != '1' ]; then
    echo 'STALE_FIREWALL_CONFIRM_APPLY_REQUIRED'
    write_status success 0
    exit 0
fi

if ! command -v iptables >/dev/null 2>&1 ||
   ! command -v iptables-save >/dev/null 2>&1; then
    echo 'STALE_FIREWALL_IPTABLES_UNAVAILABLE'
    write_status failed 3
    exit 3
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/stale-firewall.XXXXXX")"
chmod 700 "$backup_dir"
iptables-save > "$backup_dir/iptables.rules"
if [ -f "$RULES_FILE" ]; then cp -a "$RULES_FILE" "$backup_dir/rules.v4"; else : > "$backup_dir/rules.absent"; fi

rollback() {
    trap - ERR INT TERM
    set +e
    failed=0
    if [ -f "$backup_dir/rules.v4" ]; then
        cp -a "$backup_dir/rules.v4" "$RULES_FILE" || failed=1
    else
        rm -f -- "$RULES_FILE" || failed=1
    fi
    # Restore advertised connections only, leaving unrelated NEW packets to
    # current firewall policy, so a backup rollback cannot widen INPUT.
    iptables -F INPUT || failed=1
    iptables-restore --noflush "$backup_dir/iptables.rules" || failed=1
    if [ "$failed" -eq 0 ]; then
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

while IFS= read -r rule; do
    [ -n "$rule" ] || continue
    # Delete by the exact protocol/port/comment tuple. Tokenising an
    # iptables-save row loses the quoted comment, so passing the whole row
    # would target a different rule or fail before deleting anything.
    parsed="$(printf '%s\n' "$rule" | python3 -c "
import re
import sys

rule = sys.stdin.read()
proto = re.search(r'\s-p\s+(tcp|udp)\s', rule)
port = re.search(r'--dport\s+([0-9]+)\s', rule)
comment = re.search(r'--comment\s+\"([^\"]+)\"\s', rule)
print(proto.group(1) if proto else '')
print(port.group(1) if port else '')
print(comment.group(1) if comment else '')
")"
    proto="$(printf '%s\n' "$parsed" | sed -n '1p')"
    port="$(printf '%s\n' "$parsed" | sed -n '2p')"
    comment="$(printf '%s\n' "$parsed" | sed -n '3p')"
    if ! [[ "$proto" =~ ^(tcp|udp)$ && "$port" =~ ^[0-9]+$ && -n "$comment" ]]; then
        echo 'STALE_FIREWALL_RULE_PARSE_FAILED'
        rollback
    fi
    iptables -D INPUT -p "$proto" --dport "$port" -m comment --comment "$comment" -j ACCEPT
done <<<"$rows"

if [ "$persisted" -gt 0 ]; then
    if command -v netfilter-persistent >/dev/null 2>&1 &&
       systemctl is-active --quiet netfilter-persistent 2>/dev/null; then
        netfilter-persistent save
    elif command -v service >/dev/null 2>&1 &&
         [ -e /etc/init.d/netfilter-persistent ]; then
        service netfilter-persistent save
    elif [ -f "$RULES_FILE" ] &&
         grep -q '^# Generated by iptables-save' "$RULES_FILE"; then
        # Some images retain the package's initscript without its service
        # wrapper. The persisted file is an iptables-save snapshot; regenerate
        # it atomically only after establishing that format, then verify both
        # live and disk layers below. A hand-written file is never rewritten.
        rules_dir="$(dirname "$RULES_FILE")"
        tmp_rules="$(mktemp "$rules_dir/.rules.XXXXXX")"
        iptables-save > "$tmp_rules"
        chmod --reference="$RULES_FILE" "$tmp_rules" 2>/dev/null || chmod 644 "$tmp_rules"
        mv -f -- "$tmp_rules" "$RULES_FILE"
    else
        echo 'STALE_FIREWALL_PERSISTENCE_UNSUPPORTED'
        rollback
    fi
fi

remaining="$(matching_rows)"
persisted_after=0
if [ -f "$RULES_FILE" ]; then
    persisted_after="$(grep -F -- '--comment "ArgoX PORT ' "$RULES_FILE" 2>/dev/null | grep -E -- '--dport (3000[0-9]|30010|8080)( |$)' | wc -l | tr -d '[:space:]' || true)"
fi
if [ -n "$remaining" ] || [ "$persisted_after" -ne 0 ]; then
    echo 'STALE_FIREWALL_DELETE_INCOMPLETE'
    rollback
fi
systemctl is-active --quiet argo 2>/dev/null && { echo 'STALE_FIREWALL_SERVICE_ACTIVE'; rollback; }
ss -Hlntu 2>/dev/null | grep -Eq ':(3000[0-9]|30010|8080)\b' && { echo 'STALE_FIREWALL_PORT_IN_USE'; rollback; }

trap - ERR INT TERM
echo "APPLY_BACKUP_DIR=$backup_dir"
echo 'STALE_FIREWALL_APPLIED'
write_status success 0
'@.Replace("__APPLY__", $applyLiteral).Replace("__PROFILE__", $Profile)

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "stale-firewall"

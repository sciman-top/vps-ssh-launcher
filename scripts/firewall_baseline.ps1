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
. (Join-Path $PSScriptRoot "lib\proxy_firewall_contract.ps1")

Initialize-WindowsProcessEnvironment
$script:Python = Resolve-ProjectPython -ProjectRoot $repoRoot -AllowPyLauncher
$Config = Resolve-LauncherConfigPath -ProjectRoot $repoRoot -Config $Config
if (-not (Test-Path -LiteralPath $Config)) {
  throw "Config file not found: $Config"
}

$contract = Get-ProxyFirewallContract -Profile $Profile
$rulesText = (New-ProxyFirewallRules -Profile $Profile).Replace("`r`n", "`n").Replace("`r", "`n")
$rulesSha = (
  [BitConverter]::ToString(
    [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($rulesText))
  )
).Replace("-", "").ToLowerInvariant()
$rulesBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($rulesText))
$tcpLiteral = $contract.TcpPorts -join ' '
$udpLiteral = $contract.UdpPorts -join ' '
$applyLiteral = if ($Apply) { "1" } else { "0" }

$remoteCommand = @'
#!/usr/bin/env bash
set -Eeuo pipefail
APPLY="__APPLY__"
PROFILE="__PROFILE__"
RULES_B64="__RULES_B64__"
EXPECTED_SHA="__RULES_SHA__"
EXPECTED_TCP="__TCP_PORTS__"
EXPECTED_UDP="__UDP_PORTS__"
SSH_PORT="__SSH_PORT__"
HOP_START="__HOP_START__"
HOP_END="__HOP_END__"
HOP_TARGET="__HOP_TARGET__"
LOCK_FILE="${VPS_FIREWALL_LOCK_FILE:-/run/vps-ssh-launcher-maintenance.lock}"
RULES_DIR="${VPS_FIREWALL_RULES_DIR:-/etc/vps-ssh-launcher}"
RULES_FILE="$RULES_DIR/firewall.nft"
UNIT_FILE="${VPS_FIREWALL_UNIT_FILE:-/etc/systemd/system/vps-launcher-firewall.service}"
BACKUP_PARENT="${VPS_FIREWALL_BACKUP_DIR:-/var/backups}"
STATUS_DIR="${VPS_FIREWALL_STATUS_DIR:-/var/lib/vps-ssh-launcher/maintenance-status}"
STATUS_FILE="$STATUS_DIR/firewall-baseline.status"
TABLE_NAME="vps_launcher_guard"

write_status() {
    local result="$1" code="$2" finished tmp
    finished="$(date -u +%FT%TZ)"
    mkdir -m 700 -p "$STATUS_DIR" 2>/dev/null || return 0
    tmp="$(mktemp "$STATUS_FILE.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp" ] || return 0
    {
        printf 'name=firewall-baseline\n'
        printf 'finished_at=%s\n' "$finished"
        printf 'result=%s\n' "$result"
        printf 'exit_code=%s\n' "$code"
        printf 'rules_sha256=%s\n' "$EXPECTED_SHA"
        printf 'tcp_ports=%s\n' "$EXPECTED_TCP"
        printf 'udp_ports=%s\n' "$EXPECTED_UDP"
        printf 'ssh_port=%s\n' "$SSH_PORT"
        printf 'hysteria2_hop=%s-%s->%s\n' "$HOP_START" "$HOP_END" "$HOP_TARGET"
    } > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$STATUS_FILE" 2>/dev/null || rm -f -- "$tmp"
}

render_expected() {
    printf '%s' "$RULES_B64" | base64 -d
}

render_unit() {
    cat <<EOF
[Unit]
Description=vps-ssh-launcher managed firewall baseline
After=network-pre.target nftables.service
Wants=network-pre.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f $RULES_FILE
ExecReload=/usr/sbin/nft -f $RULES_FILE
ExecStop=-/usr/sbin/nft delete table inet $TABLE_NAME

[Install]
WantedBy=multi-user.target
EOF
}

service_active() { systemctl is-active --quiet vps-launcher-firewall.service; }
service_enabled() { systemctl is-enabled --quiet vps-launcher-firewall.service; }
table_exists() { nft list table inet "$TABLE_NAME" >/dev/null 2>&1; }
input_chain_state() {
    local nft_json
    nft_json="$(nft -j list chain inet "$TABLE_NAME" input 2>/dev/null)" || return 1
    FIREWALL_NFT_JSON="$nft_json" python3 - "$EXPECTED_TCP" "$EXPECTED_UDP" <<'PY_FIREWALL'
import json
import os
import sys

doc = json.loads(os.environ["FIREWALL_NFT_JSON"])
objects = doc.get("nftables") or []
chains = [
    item["chain"] for item in objects
    if isinstance(item, dict)
    and "chain" in item
    and item["chain"].get("table") == "vps_launcher_guard"
    and item["chain"].get("name") == "input"
]
if len(chains) != 1:
    print("live-chain")
    raise SystemExit(0)

chain = chains[0]
if (
    chain.get("family") != "inet"
    or chain.get("type") != "filter"
    or chain.get("hook") != "input"
    or chain.get("prio") != -10
):
    print("live-chain")
    raise SystemExit(0)
if chain.get("policy") != "drop":
    print("live-policy")
    raise SystemExit(0)

tcp = [int(value) for value in sys.argv[1].split()]
udp = [int(value) for value in sys.argv[2].split()]
expected = [
    # CVE-2026-98432 mitigation (rendered by nft 1.0.9, verified in a
    # throwaway netns 2026-11-10): meta l4proto tcp ip hdrlength > 5 drop.
    [
        {"match": {"op": "==", "left": {"meta": {"key": "l4proto"}}, "right": "tcp"}},
        {"match": {"op": ">", "left": {"payload": {"protocol": "ip", "field": "hdrlength"}}, "right": 5}},
        {"drop": None},
    ],
    [
        {"match": {"op": "in", "left": {"ct": {"key": "state"}}, "right": ["established", "related"]}},
        {"accept": None},
    ],
    [
        {"match": {"op": "in", "left": {"ct": {"key": "state"}}, "right": "invalid"}},
        {"drop": None},
    ],
    [
        {"match": {"op": "==", "left": {"meta": {"key": "iif"}}, "right": "lo"}},
        {"accept": None},
    ],
    [
        {
            "match": {
                "op": "==",
                "left": {"meta": {"key": "l4proto"}},
                "right": {"set": ["icmp", "ipv6-icmp"]},
            }
        },
        {"accept": None},
    ],
    [
        {"match": {"op": "==", "left": {"payload": {"protocol": "udp", "field": "sport"}}, "right": 67}},
        {"match": {"op": "==", "left": {"payload": {"protocol": "udp", "field": "dport"}}, "right": 68}},
        {"accept": None},
    ],
    [
        {"match": {"op": "==", "left": {"payload": {"protocol": "udp", "field": "sport"}}, "right": 547}},
        {"match": {"op": "==", "left": {"payload": {"protocol": "udp", "field": "dport"}}, "right": 546}},
        {"accept": None},
    ],
    [
        {"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": {"set": tcp}}},
        {"accept": None},
    ],
    [
        {"match": {"op": "==", "left": {"payload": {"protocol": "udp", "field": "dport"}}, "right": {"set": udp}}},
        {"accept": None},
    ],
]
actual = [
    item["rule"].get("expr")
    for item in objects
    if isinstance(item, dict)
    and "rule" in item
    and item["rule"].get("table") == "vps_launcher_guard"
    and item["rule"].get("chain") == "input"
]
print("exact" if actual == expected else "live-rules")
PY_FIREWALL
}
contract_ok() {
    local tmp input_state
    [ "$(id -u)" = '0' ] || return 2
    command -v nft >/dev/null 2>&1 || return 3
    command -v python3 >/dev/null 2>&1 || return 9
    systemctl cat vps-launcher-firewall.service >/dev/null 2>&1 || return 4
    [ -f "$RULES_FILE" ] || return 5
    tmp="$(mktemp)"
    render_expected > "$tmp"
    cmp -s "$tmp" "$RULES_FILE" || { rm -f -- "$tmp"; return 6; }
    rm -f -- "$tmp"
    table_exists || return 70
    input_state="$(input_chain_state)" || return 73
    if [ "$input_state" = 'live-policy' ]; then
        nft list chain inet "$TABLE_NAME" input >&2 2>/dev/null || true
        return 71
    fi
    if [ "$input_state" != 'exact' ]; then
        nft list chain inet "$TABLE_NAME" input >&2 2>/dev/null || true
        return 72
    fi
    service_active && service_enabled || return 8
}

echo '==firewall-baseline=='
echo "firewall_profile=$PROFILE tcp=$EXPECTED_TCP udp=$EXPECTED_UDP ssh=$SSH_PORT"
echo "hysteria2_port_hopping=udp:$HOP_START-$HOP_END->$HOP_TARGET"
if ! command -v nft >/dev/null 2>&1 || ! command -v systemctl >/dev/null 2>&1; then
    echo 'firewall_baseline=not_applicable reason=missing_nft_or_systemd'
    exit 0
fi

state_rc=0
contract_ok || state_rc=$?
if [ "$state_rc" -eq 0 ]; then
    echo 'firewall_baseline=applied'
    write_status noop 0
    exit 0
fi
echo "firewall_baseline=drift reason=contract_$state_rc"
if [ "$APPLY" != '1' ]; then
    exit 0
fi

if [ "$(id -u)" != '0' ]; then
    echo 'REFUSE root_required' >&2
    write_status failed 4
    exit 4
fi
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "REFUSE busy lock=$LOCK_FILE" >&2
    write_status busy 75
    exit 75
fi

backup_dir="$(mktemp -d "$BACKUP_PARENT/firewall-baseline.XXXXXX")"
chmod 700 "$backup_dir"
table_was_present=0
if nft list table inet "$TABLE_NAME" > "$backup_dir/live.nft" 2>/dev/null; then
    table_was_present=1
else
    rm -f -- "$backup_dir/live.nft"
fi
if [ -f "$RULES_FILE" ]; then cp -a "$RULES_FILE" "$backup_dir/rules.nft"; else : > "$backup_dir/rules.absent"; fi
if [ -f "$UNIT_FILE" ]; then cp -a "$UNIT_FILE" "$backup_dir/unit.service"; else : > "$backup_dir/unit.absent"; fi
service_active_before=0
service_enabled_before=0
if service_active; then service_active_before=1; fi
if service_enabled; then service_enabled_before=1; fi

rollback() {
    local rc="${1:-1}" failed=0
    trap - ERR HUP INT TERM
    set +e
    # Scratch files may exist if the failure happened before the atomic mv;
    # the unit one lives under /etc/systemd/system, so never leave it there.
    [ -n "${tmp_rules:-}" ] && rm -f -- "$tmp_rules"
    [ -n "${tmp_unit:-}" ] && rm -f -- "$tmp_unit"
    if [ "$table_was_present" -eq 1 ]; then
        nft delete table inet "$TABLE_NAME" >/dev/null 2>&1 || true
        nft -f "$backup_dir/live.nft" >/dev/null 2>&1 || failed=1
    else
        nft delete table inet "$TABLE_NAME" >/dev/null 2>&1 || true
    fi
    if [ -f "$backup_dir/rules.nft" ]; then
        cp -a "$backup_dir/rules.nft" "$RULES_FILE" || failed=1
    else
        rm -f -- "$RULES_FILE" || failed=1
    fi
    if [ -f "$backup_dir/unit.service" ]; then
        cp -a "$backup_dir/unit.service" "$UNIT_FILE" || failed=1
    else
        rm -f -- "$UNIT_FILE" || failed=1
    fi
    systemctl daemon-reload >/dev/null 2>&1 || failed=1
    if [ "$service_enabled_before" -eq 1 ]; then
        systemctl enable vps-launcher-firewall.service >/dev/null 2>&1 || failed=1
    else
        systemctl disable vps-launcher-firewall.service >/dev/null 2>&1 || true
    fi
    if [ "$service_active_before" -eq 1 ]; then
        systemctl start vps-launcher-firewall.service >/dev/null 2>&1 || failed=1
    else
        systemctl stop vps-launcher-firewall.service >/dev/null 2>&1 || true
    fi
    if [ "$failed" -eq 0 ]; then
        echo "ROLLBACK_VERIFIED backup=$backup_dir"
    else
        echo "ROLLBACK_FAILED backup=$backup_dir" >&2
    fi
    write_status failed "$rc"
    exit "$rc"
}
trap rollback ERR
trap 'rollback 129' HUP
trap 'rollback 130' INT
trap 'rollback 143' TERM

mkdir -p "$RULES_DIR"
tmp_rules="$(mktemp "$RULES_DIR/.firewall.nft.XXXXXX")"
tmp_unit="$(mktemp "$UNIT_FILE.XXXXXX")"
render_expected > "$tmp_rules"
chmod 644 "$tmp_rules"
if ! nft -c -f "$tmp_rules" >/dev/null 2>&1; then
    echo 'FIREWALL_NFT_CHECK_FAILED' >&2
    rollback 5
fi
render_unit > "$tmp_unit"
chmod 644 "$tmp_unit"
mv -f -- "$tmp_rules" "$RULES_FILE"
mv -f -- "$tmp_unit" "$UNIT_FILE"
systemctl daemon-reload
nft -f "$RULES_FILE"
systemctl enable --now vps-launcher-firewall.service

verify_rc=0
contract_ok || verify_rc=$?
if [ "$verify_rc" -ne 0 ]; then
    echo "FIREWALL_BASELINE_VERIFY_FAILED reason=contract_$verify_rc" >&2
    rollback 6
fi
trap - ERR HUP INT TERM
write_status success 0
echo "FIREWALL_RULES_SHA256=$EXPECTED_SHA"
echo "FIREWALL_TCP_PORTS=$EXPECTED_TCP"
echo "FIREWALL_UDP_PORTS=$EXPECTED_UDP"
echo "HYSTERIA2_HOP_PORTS=$HOP_START-$HOP_END TARGET=$HOP_TARGET"
echo "APPLY_BACKUP_DIR=$backup_dir"
find "$BACKUP_PARENT" -mindepth 1 -maxdepth 1 -type d -name 'firewall-baseline.*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | tail -n +9 | cut -d' ' -f2- \
    | while IFS= read -r old_backup_dir; do
        rm -rf -- "$old_backup_dir" || echo 'BACKUP_PRUNE_FAILED' >&2
    done
echo 'BACKUP_PRUNE scope=firewall-baseline policy=keep_8'
echo 'FIREWALL_BASELINE_APPLIED'
'@.Replace("__APPLY__", $applyLiteral).
  Replace("__PROFILE__", $Profile).
  Replace("__RULES_B64__", $rulesBase64).
  Replace("__RULES_SHA__", $rulesSha).
  Replace("__TCP_PORTS__", $tcpLiteral).
  Replace("__UDP_PORTS__", $udpLiteral).
  Replace("__SSH_PORT__", "$($contract.SshPort)").
  Replace("__HOP_START__", "$($contract.Hysteria2HopStart)").
  Replace("__HOP_END__", "$($contract.Hysteria2HopEnd)").
  Replace("__HOP_TARGET__", "$($contract.Hysteria2HopTarget)")

Invoke-LauncherRemoteCommand -Python $script:Python -ProjectRoot $repoRoot -Config $Config -Profile $Profile -Command $remoteCommand -RemoteTempPrefix "firewall-baseline"

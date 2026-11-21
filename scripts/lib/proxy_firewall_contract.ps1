function Get-ProxyFirewallContract {
  param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("bwg", "zz")]
    [string]$Profile
  )

  $tcpByProfile = @{
    bwg = @(443, 2087, 8443, 10094, 21470, 28317, 29712, 33387)
    zz  = @(443, 2087, 10094, 12378, 21470, 31854, 35622)
  }
  $udpByProfile = @{
    bwg = @(443, 8444)
    zz  = @(443, 8444)
  }
  $sshByProfile = @{
    bwg = 29712
    zz  = 31854
  }
  $bridgeSidecarByProfile = @{
    bwg = @(18082)
    zz  = @()
  }

  [pscustomobject]@{
    Profile = $Profile
    TcpPorts = @($tcpByProfile[$Profile] | Sort-Object -Unique)
    UdpPorts = @($udpByProfile[$Profile] | Sort-Object -Unique)
    SshPort = [int]$sshByProfile[$Profile]
    LocalBridgeSidecarTcpPorts = @($bridgeSidecarByProfile[$Profile] | Sort-Object -Unique)
    Hysteria2HopStart = 20000
    Hysteria2HopEnd = 20999
    Hysteria2HopTarget = 8444
  }
}

function New-ProxyFirewallRules {
  param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("bwg", "zz")]
    [string]$Profile
  )

  $contract = Get-ProxyFirewallContract -Profile $Profile
  foreach ($port in @($contract.TcpPorts + $contract.UdpPorts)) {
    if ($port -lt 1 -or $port -gt 65535) {
      throw "Invalid firewall port in $Profile contract: $port"
    }
  }
  $tcpPorts = $contract.TcpPorts -join ', '
  $udpPorts = $contract.UdpPorts -join ', '
  $bridgeLines = foreach ($port in @($contract.LocalBridgeSidecarTcpPorts)) {
    "        # Allow local Docker bridge containers (CPA) to reach host sidecar.`n        iifname `"br-*`" ip daddr 172.17.0.1 tcp dport $port accept"
  }
  $localBridgeBlock = if ($bridgeLines) {
    "`n" + ($bridgeLines -join "`n")
  } else {
    ""
  }
  $template = @'
table inet vps_launcher_guard {
}
flush table inet vps_launcher_guard

table inet vps_launcher_guard {
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;

        # Single-target port hopping: clients select any advertised UDP port
        # and the kernel redirects it to the existing obfuscated Hysteria2
        # listener. This is ineffective only if the network blocks UDP as a
        # whole, in which case the TCP lanes remain the fallback.
        iifname != "lo" udp dport __HOP_START__-__HOP_END__ counter \
            redirect to :__HOP_TARGET__
    }

    chain output {
        type nat hook output priority dstnat; policy accept;

        # Keep same-host hairpin/acceptance probes on the same forwarding path
        # as an external client. This rule never matches traffic to a remote
        # address.
        fib daddr type local udp dport __HOP_START__-__HOP_END__ counter \
            redirect to :__HOP_TARGET__
    }

    chain input {
        type filter hook input priority -10; policy drop;

        # CVE-2026-98432 (IPv4 TCP use-after-free, CVSS 9.8): Ubuntu 24.04 has
        # no fixed 6.8.0 kernel yet, so this is Ubuntu's published nftables
        # mitigation, placed first so options-bearing packets never reach the
        # accepts below. Ordinary traffic never sends IP options; IPv6 and
        # option-free IPv4 TCP are unaffected.
        meta l4proto tcp ip hdrlength > 5 drop

        ct state established,related accept
        ct state invalid drop
        iif "lo" accept
        meta l4proto { icmp, ipv6-icmp } accept

        # DHCP/DHCPv6 replies are untracked in some provider networks.
        udp sport 67 udp dport 68 accept
        udp sport 547 udp dport 546 accept

        tcp dport { __TCP_PORTS__ } accept
        udp dport { __UDP_PORTS__ } accept
__LOCAL_BRIDGE_RULES__
    }
}
'@
  return $template.
    Replace("__HOP_START__", "$($contract.Hysteria2HopStart)").
    Replace("__HOP_END__", "$($contract.Hysteria2HopEnd)").
    Replace("__HOP_TARGET__", "$($contract.Hysteria2HopTarget)").
    Replace("__TCP_PORTS__", $tcpPorts).
    Replace("__UDP_PORTS__", $udpPorts).
    Replace("`n__LOCAL_BRIDGE_RULES__", $localBridgeBlock)
}

function Get-ProxyFirewallLiveStatePython {
  # Shared by the baseline's own post-apply check and the read-only drift gate.
  # All three chains are compared by nft's machine-readable semantics, so a
  # deleted or altered NAT redirect cannot stay hidden behind a correct rules
  # file and an active oneshot service.
  return @'
import json
import os
import sys

try:
    doc = json.loads(os.environ["FIREWALL_NFT_JSON"])
except (KeyError, TypeError, ValueError):
    print("live-unreadable")
    raise SystemExit(0)
objects = doc.get("nftables") if isinstance(doc, dict) else None
if not isinstance(objects, list):
    print("live-unreadable")
    raise SystemExit(0)

def valid_json(expression):
    # nft's JSON mode can omit a malformed expression object. Empty or opaque
    # targets in any rule are therefore unreadable, regardless of chain.
    if not isinstance(expression, dict) or not expression:
        return False
    if "counter" in expression:
        counter = expression["counter"]
        if (
            not isinstance(counter, dict)
            or set(counter) != {"packets", "bytes"}
            or any(
                not isinstance(counter[key], int)
                or isinstance(counter[key], bool)
                or counter[key] < 0
                for key in ("packets", "bytes")
            )
        ):
            return False
    return True

for item in objects:
    if not isinstance(item, dict):
        print("live-unreadable")
        raise SystemExit(0)
    if "rule" not in item:
        continue
    rule = item["rule"]
    if not (
        isinstance(rule, dict)
        and rule.get("table") == "vps_launcher_guard"
        and isinstance(rule.get("expr"), list)
        and rule["expr"]
        and all(valid_json(expression) for expression in rule["expr"])
    ):
        print("live-rules-unreadable")
        raise SystemExit(0)


def chains(table, name):
    found = [
        item["chain"] for item in objects
        if isinstance(item, dict)
        and isinstance(item.get("chain"), dict)
        and item["chain"].get("table") == table
        and item["chain"].get("name") == name
    ]
    return found if len(found) == 1 else None


def rules(table, name):
    found = [
        item["rule"] for item in objects
        if isinstance(item, dict)
        and isinstance(item.get("rule"), dict)
        and item["rule"].get("table") == table
        and item["rule"].get("chain") == name
    ]
    if not found:
        return []
    if any(not isinstance(item.get("expr"), list) for item in found):
        return None
    return [item["expr"] for item in found]


table = "vps_launcher_guard"
# An exact rule list is insufficient if another chain in this table also owns
# an input hook and accepts before the managed chain sees the packet.
hooks = {}
for item in objects:
    chain = item.get("chain") if isinstance(item, dict) else None
    if not isinstance(chain, dict) or chain.get("table") != table:
        continue
    hook = chain.get("hook")
    if not isinstance(hook, str):
        continue
    hooks[hook] = hooks.get(hook, 0) + 1
shadowed = [hook for hook in hooks if hooks[hook] > 1]
if shadowed:
    print("live-hooks:" + ",".join(shadowed))
    raise SystemExit(0)
prerouting = chains(table, "prerouting")
output = chains(table, "output")
input_chain = chains(table, "input")
if prerouting is None or output is None or input_chain is None:
    print("live-chain")
    raise SystemExit(0)
for chain, kind, hook, priority, policy in (
    (prerouting[0], "nat", "prerouting", -100, "accept"),
    (output[0], "nat", "output", -100, "accept"),
    (input_chain[0], "filter", "input", -10, "drop"),
):
    if (
        chain.get("family") != "inet"
        or chain.get("type") != kind
        or chain.get("hook") != hook
        or chain.get("prio") != priority
        or chain.get("policy") != policy
    ):
        print("live-chain")
        raise SystemExit(0)

try:
    hop_start = int(sys.argv[1])
    hop_end = int(sys.argv[2])
    hop_target = int(sys.argv[3])
    tcp_text = sys.argv[4]
    udp_text = sys.argv[5]
    bridge_text = sys.argv[6] if len(sys.argv) > 6 else ""
    tcp = [int(value) for value in tcp_text.split()]
    udp = [int(value) for value in udp_text.split()]
    bridge_tcp = [int(value) for value in bridge_text.split() if value.strip()]
except (IndexError, TypeError, ValueError):
    print("live-unreadable")
    raise SystemExit(0)

expected_prerouting = [[
    {"match": {
        "op": "!=",
        "left": {"meta": {"key": "iifname"}},
        "right": "lo",
    }},
    {"match": {
        "op": "==",
        "left": {"payload": {"protocol": "udp", "field": "dport"}},
        "right": {"range": [hop_start, hop_end]},
    }},
    {"counter": {"packets": 0, "bytes": 0}},
    {"redirect": {"port": hop_target}},
]]
expected_output = [[
    {"match": {
        "op": "==",
        "left": {"fib": {"result": "type", "flags": ["daddr"]}},
        "right": "local",
    }},
    {"match": {
        "op": "==",
        "left": {"payload": {"protocol": "udp", "field": "dport"}},
        "right": {"range": [hop_start, hop_end]},
    }},
    {"counter": {"packets": 0, "bytes": 0}},
    {"redirect": {"port": hop_target}},
]]
expected_input = [
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
        {"match": {
            "op": "==",
            "left": {"meta": {"key": "l4proto"}},
            "right": {"set": ["icmp", "ipv6-icmp"]},
        }},
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
for bridge_port in bridge_tcp:
    expected_input.append([
        {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": "br-*"}},
        {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "daddr"}}, "right": "172.17.0.1"}},
        {"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": bridge_port}},
        {"accept": None},
    ])


def canonical(value):
    # nft's JSON spelling of the same rule varies across releases: the NAT
    # redirect appears as "redir" or "redirect", and `fib daddr type local`
    # has been observed as flags ["daddr"] with the "local" value carried by
    # the surrounding match, or as a nested daddr payload plus a redundant
    # "local" flag. Collapse those spellings so semantic equivalence decides
    # the verdict instead of one release's serialization detail.
    if isinstance(value, list):
        return [canonical(item) for item in value]
    if not isinstance(value, dict):
        return value
    collapsed = {}
    for key, item in value.items():
        if key == "redir":
            key = "redirect"
        elif key == "fib" and isinstance(item, dict):
            item = {name: part for name, part in item.items() if name != "daddr"}
            if isinstance(item.get("flags"), list):
                item["flags"] = [flag for flag in item["flags"] if flag != "local"]
        collapsed[key] = canonical(item)
    return collapsed


def normalize(exprs):
    # nft -j includes volatile packet/byte counters. Their values are not part
    # of the configuration contract, but their presence and ordinal position
    # are, so normalize only those two fields.
    normalized = []
    for expression in exprs:
        if not isinstance(expression, dict):
            return None
        expression = dict(expression)
        if "counter" in expression:
            counter = expression["counter"]
            if not isinstance(counter, dict) or set(counter) != {"packets", "bytes"}:
                return None
            expression["counter"] = {"packets": 0, "bytes": 0}
        normalized.append(canonical(expression))
    return normalized


def normalize_rules(all_rules):
    # nft -j returns a list of rules; tests/rendered contracts use a list of
    # expression lists. Normalize both into the same rule-by-rule shape.
    if not isinstance(all_rules, list):
        return None
    normalized = []
    for rule in all_rules:
        exprs = rule.get("expr") if isinstance(rule, dict) else rule
        if not isinstance(exprs, list):
            return None
        current = normalize(exprs)
        if current is None:
            return None
        normalized.append(current)
    return normalized


actual = {
    "prerouting": normalize_rules(rules(table, "prerouting")),
    "output": normalize_rules(rules(table, "output")),
    "input": normalize_rules(rules(table, "input")),
}
if any(value is None for value in actual.values()):
    print("live-rules-unreadable")
    raise SystemExit(0)
expected = {
    "prerouting": normalize_rules(expected_prerouting),
    "output": normalize_rules(expected_output),
    "input": normalize_rules(expected_input),
}
if actual == expected:
    print("exact")
else:
    mismatched = [name for name in expected if actual[name] != expected[name]]
    print("live-rules:" + ",".join(mismatched))
'@
}

function New-ProxyFirewallUnit {
  # Shared source for the persistent oneshot definition. Both the baseline
  # self-check and the read-only drift gate consume this text, so a drop-in
  # that overrides ExecStart fails even while the currently loaded rules
  # happen to be correct.
  return @'
[Unit]
Description=vps-ssh-launcher managed firewall baseline
After=network-pre.target nftables.service
Wants=network-pre.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/vps-ssh-launcher/firewall.nft
ExecReload=/usr/sbin/nft -f /etc/vps-ssh-launcher/firewall.nft
ExecStop=-/usr/sbin/nft delete table inet vps_launcher_guard

[Install]
WantedBy=multi-user.target
'@
}

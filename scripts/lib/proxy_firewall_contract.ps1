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

  [pscustomobject]@{
    Profile = $Profile
    TcpPorts = @($tcpByProfile[$Profile] | Sort-Object -Unique)
    UdpPorts = @($udpByProfile[$Profile] | Sort-Object -Unique)
    SshPort = [int]$sshByProfile[$Profile]
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
    }
}
'@
  return $template.
    Replace("__HOP_START__", "$($contract.Hysteria2HopStart)").
    Replace("__HOP_END__", "$($contract.Hysteria2HopEnd)").
    Replace("__HOP_TARGET__", "$($contract.Hysteria2HopTarget)").
    Replace("__TCP_PORTS__", $tcpPorts).
    Replace("__UDP_PORTS__", $udpPorts)
}

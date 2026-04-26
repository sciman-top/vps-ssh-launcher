# Gemini Google IPv4 Routing Evidence

- timestamp: 2026-04-26T19:33:35+08:00
- rule_ids: R3, R6, R8, E4
- risk_level: medium
- current_landing: host network / remote Xray routing on profile `bwg`
- target_landing: keep Google/Gemini proxy egress on one IPv4 route and avoid mixed IPv4/IPv6 identity
- rollback:
  - remote backup: `/etc/v2ray-agent/backup-google-ipv4-systemd-20260426T113250Z`
  - remote backup: `/etc/v2ray-agent/backup-google-ipv4-apply-20260426T113250Z`
  - local browser-policy backup: `%APPDATA%\vps-ssh-launcher\host-network-backup-20260426T193306`

## Basis

Gemini reported an unusual-traffic block with mixed egress identity:

- IPv4: `144.34.229.116`
- IPv6: `2607:8700:5500:50c7::2`

Remote read-only probes on profile `bwg` confirmed the same dual-stack public egress:

- `curl -4 https://api.ipify.org` -> `144.34.229.116`
- `curl -6 https://api64.ipify.org` -> `2607:8700:5500:50c7::2`

The active Xray config had reverted to the default Google direct rule:

- `/etc/v2ray-agent/xray/conf/09_routing.json` existed
- `/etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json` was missing

## Change

Remote profile `bwg`:

- Restored `/etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json`.
- Replaced Google/Gemini routing in `/etc/v2ray-agent/xray/conf/09_routing.json`.
- Added `/etc/v2ray-agent/apply-google-ipv4-routing-config.sh`.
- Updated `/etc/v2ray-agent/reapply-google-ipv4-routing.sh`.
- Added `/etc/systemd/system/xray.service.d/20-google-ipv4-routing.conf` with:

```ini
[Service]
ExecStartPre=/bin/bash /etc/v2ray-agent/apply-google-ipv4-routing-config.sh
```

Local browser policy:

- Set Chrome and Edge `DnsOverHttpsMode=secure`.
- Set Chrome and Edge `DnsOverHttpsTemplates=https://dns.google/dns-query https://cloudflare-dns.com/dns-query`.
- Set Chrome and Edge `QuicAllowed=0`.

## Verification

Remote verification:

```text
google-ipv4-routing already current
config-ok
systemctl is-active xray -> active
/etc/v2ray-agent/xray/conf/09_routing.json contains domain:gemini.google.com and google_ipv4_out
/etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json contains ForceIPv4
```

Local proxy verification:

```text
curl -x http://127.0.0.1:10808 -I -L https://gemini.google.com/ -> HTTP/1.1 200 OK
curl -x http://127.0.0.1:10808 -I -L https://www.google.com/ -> HTTP/1.1 200 OK
curl -x http://127.0.0.1:10808 https://dns.google/resolve?name=gemini.google.com&type=A -> Status 0
```

Browser policy verification:

```text
Chrome DnsOverHttpsMode=secure, QuicAllowed=0
Edge DnsOverHttpsMode=secure, QuicAllowed=0
```

## Notes

- No repository code was changed.
- No real password, private key, token, or remote credential was recorded.
- Existing unrelated local modification remained untouched: `.governed-ai/repo-profile.json`.

## Post Update Recheck

- timestamp: 2026-04-26T19:35:00+08:00
- trigger: v2ray-agent script and Xray core were updated after the routing fix.
- result: Google/Gemini IPv4 routing was not overwritten.

Remote profile `bwg`:

```text
Xray 26.3.27 (Xray, Penetrates Everything.)
systemctl is-active xray -> active
/etc/systemd/system/xray.service.d/20-google-ipv4-routing.conf exists
ExecStartPre=/bin/bash /etc/v2ray-agent/apply-google-ipv4-routing-config.sh
/etc/v2ray-agent/apply-google-ipv4-routing-config.sh exists
/etc/v2ray-agent/reapply-google-ipv4-routing.sh exists
/etc/v2ray-agent/xray/conf/09_routing.json contains domain:gemini.google.com and google_ipv4_out
/etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json contains ForceIPv4
xray run -test -confdir /etc/v2ray-agent/xray/conf -> config-ok
```

Systemd restart evidence:

```text
Apr 26 11:35:00 sciman systemd[1]: Starting xray.service - Xray Service...
Apr 26 11:35:00 sciman bash[233325]: google-ipv4-routing already current
Apr 26 11:35:00 sciman systemd[1]: Started xray.service - Xray Service.
```

Local proxy verification after update:

```text
curl -x http://127.0.0.1:10808 -I -L https://gemini.google.com/ -> HTTP/1.1 200 OK
curl -x http://127.0.0.1:10808 -I -L https://www.google.com/ -> HTTP/1.1 200 OK
curl -x http://127.0.0.1:10808 -4 https://api.ipify.org -> 144.34.229.116
curl -x http://127.0.0.1:10808 -6 https://api64.ipify.org -> exit 7
```

Remaining files:

```text
/etc/v2ray-agent/backup-google-ipv4-20260426T103337Z
/etc/v2ray-agent/rollback-snapshot-before-google-ipv4-revert-20260426T105559Z
/etc/v2ray-agent/rollback-snapshot-before-google-ipv4-revert-extra
/etc/v2ray-agent/backup-google-ipv4-reapply-20260426T113125Z
/etc/v2ray-agent/backup-google-ipv4-apply-20260426T113250Z
/etc/v2ray-agent/backup-google-ipv4-systemd-20260426T113250Z
```

These are rollback/evidence directories, not active config. They were intentionally left in place.

# 2026-10-31 bwg web-root extras, firewall hygiene, conf perms

User-authorized continuous-remediation batch following the round-12 review
(512d33e) and the 50x.html quarantine receipt (bafd2ed). Three controlled
remote writes on bwg, each reversible, all verified.

## A. Camouflage web-root staging leftovers quarantined

Inventory check first: every candidate had **zero references** from any
`*.html` in the whole document root (root pages, `en/` tree included).

- Moved (22 items, mv-only, same filesystem): 10 template archives
  (`html1.zip`…`html9.zip.1`, ~60 MB), `psd/` (~45 MB of design files),
  `__MACOSX/` (AppleDouble junk), 2 Cloudflare-worker scripts (~3.8 MB),
  `manifest.json`, `service-worker.js`, 2 `precache-manifest.*.js`,
  `Dockerfile`, `README.md`, `check`, `kshop16.xml`.
- Destination: `/var/backups/camouflage-extras-20261031T021745Z` (0700);
  web root shrank 141 MB → 19 MB of pure site content.
- Flock `/run/vps-ssh-launcher-maintenance.lock` held; `nginx -t` ok.
- Behavior (loopback, SNI fq.sciman.top:33387): `/`, `/about.html`,
  `/en/index.html` → 200; `/50x.html`, `/html4.zip`, `/manifest.json`,
  `/service-worker.js`, `/psd/index.psd` → 404.
- Rollback: `mv /var/backups/camouflage-extras-20261031T021745Z/* back`.

## B. Stale vasma INPUT rules removed

`iptables-save` carried 115 `macka`-commented ACCEPT rules across 62 ports
from vasma menu history; only 443/10094/21470/33387 still had listeners.

- Atomically applied a filtered full ruleset via `iptables-restore`
  (108 stale rules removed, 7 kept: the four live ports × tcp/udp).
- Pre-restore assertions all passed: our `8444/udp(hysteria2-obfs)` rule and
  raw-table guard drops retained, docker chains present, filtered line count
  matches the stale count.
- Persistence regenerated with `netfilter-persistent save` (rc 0);
  `/etc/iptables/rules.v4` now carries exactly the 7 kept rules.
- Verification: TCP connect ok on 443/33387/10094/21470/8443/28317/29712;
  UDP 443/8444 listening.
- Backups: `/var/backups/iptables-mack-a-cleanup-20261031T022023Z`
  (`iptables-before.v4` + `rules.v4.before`); rollback =
  `iptables-restore < iptables-before.v4` + re-save.
- Note: a future vasma menu reconfiguration may re-add rules for newly
  configured ports; this cleanup is point-in-time hygiene, not a lane.

## C. Credential-bearing xray conf files tightened to 0600

`02_VLESS_TCP_inbounds.json.disabled`, `07_VLESS_vision_reality_inbounds.json`
(REALITY private/public/mldsa65 seed), `12_VLESS_XHTTP_inbounds.json` (same),
`14_VLESS_XHTTP_TLS_inbounds.json` (client UUIDs) are now `0600 root:root`,
matching the projected files. vasma reinstall may reset them to 644; treat
that as cosmetic.

## Final gate

`proxy_hardening_drift.ps1 -Profile bwg` → `HARDENING_DRIFT=NONE` after all
three writes.

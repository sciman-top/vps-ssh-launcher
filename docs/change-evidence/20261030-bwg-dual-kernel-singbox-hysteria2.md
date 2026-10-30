# BWG dual-kernel landing: sing-box + Hysteria2 alongside Xray

## Scope

- Target: `bwg` only. `zz` untouched.
- Change: add sing-box + Hysteria2 to the existing Xray host via the vasma
  mixed-mode flow (Xray primary core, sing-box addon core), then project the
  dual-lane maintenance machinery from the Phase-A repo changes (`86d7d15`).
- Repository prerequisite: `86d7d15` (dual-lane kernel cron, renewtls bwg
  reload branch, dual-core lane acceptance).

## Evidence

- Pre-install backup: `/root/bwg-pre-singbox-hy2-20261030-171651.tar.gz`
  (43.6 MB, sha256 prefix `b741940aa12921f2`; xray conf, sing-box tree, tls,
  nginx conf.d, acme home, root crontab snapshot).
- Preflight: UDP 443 free; `fq.sciman.top` cert/key present; xray fragments
  carry the protocol-code-0 TLS lane required by vasma's hysteria2 gate;
  a dormant sing-box 1.13.8 tree (empty conf, inactive unit) was present.
- Scripted install: `printf '4\n1\ny\ny\n443\n100\n50\n' | install.sh`
  (Hysteria2管理→安装, upgrade stale binary → y, reuse xray UUIDs → y,
  UDP port 443, bandwidth 100/50). Exit 0.
- Post-install: services xray/sing-box/nginx/fail2ban all active; sing-box
  upgraded to `1.14.4` (binary sha256 `1264361b…`, identical to zz);
  `sing-box check` OK; UDP 443 listening; TCP 443/21470/10094 listening;
  `06_hysteria2_inbounds.json` generated; xray fragment list byte-identical
  to the backup (`FRAGMENTS_IDENTICAL`).
- Subscription republished via `7→6→y` (salt reuse): decoded subscription now
  carries `1×hysteria2:// + 4×vless://`; mtime 2026-10-30 17:20:56Z.
- obfs lane: `hysteria2_obfs_lane.ps1 -Profile bwg -Apply` →
  `HYSTERIA2_OBFS_APPLIED` (backup `/var/backups/hysteria2-obfs.AAs7kZ`),
  UDP 8444 listening; password stored in `%APPDATA%\vps-ssh-launcher\
  bwg-hysteria2-obfs-password.txt` (never committed).
- Kernel wrappers: xray lane `-Apply` (backup `…maint.CkFeIE`) then sing-box
  lane first `-Apply` (backup `…maint.dfQGEc`, schedule `30 14 * * 5`).
  Projected `/etc/cron.d/vps-launcher-kernel-update` now has two lines
  (`20 14` xray, `30 14` sing-box); both wrappers coexist.
- renewtls: bwg wrapper re-projected with the sing-box restart branch
  (backup `…renewtls-deploy.Qnl92D`, `RENEWTLS_LOCKED_PROJECTED`).
- Read lane: `pin_freshness … vasma_pin=fresh binary_pin=fresh` for BOTH
  wrappers (`pinned_version=v26.10.29` / `v1.14.4`);
  `==proxy-camouflage==` now reports the hysteria2 section
  (`listen_port=443 obfs=none bands=set`) and `obfs_lane=present listening=yes
  port=8444`; hardening state held (`empty_short_id=no`, decoy-applied).
- Controlled loopback acceptance: `proxy_lane_acceptance.ps1 -Profile bwg` →
  `reality-xray PASS`, `hysteria2-base PASS (443)`, `hysteria2-obfs PASS
  (8444 salamander)`, `ACCEPTANCE_RESULT=PASS`.
- GFW-path TCP reachability (IP literal, no proxy): bwg 443/21470/10094 all
  open; UDP 443/8444 datagrams leave with no ICMP refusal (QUIC silent).

## Controlled first runs (post-projection exercise)

- sing-box kernel wrapper triggered manually (same entry as the Friday cron):
  exit 0, ensured the durable Google/Gemini IPv4 route fragment on the bwg
  sing-box tree (`99_vps_ssh_launcher_google_ipv4.json`), merged config
  changed → one controlled runtime restart, kernel pin matched → `skip
  reinstall`; heartbeat `kernel-sing-box.status result=success exit_code=0`
  written. Post-restart listeners verified: UDP 443 + 8444, TCP
  443/21470/10094 all present again.
- renewtls wrapper triggered manually: certificate valid (no renewal), the
  new bwg reload section took the zero-restart path —
  `RENEWTLS_BASELINED sha=a00f1df8…` with the applied-sha state file written;
  sing-box active throughout.
- Strict doctor re-run: `DOCTOR_CONTRACT_OK` with both new-generation
  heartbeats green (`kernel-xray age_hours=3`, `kernel-sing-box age_hours=0`).

## Boundary and follow-up

- `ACCEPTANCE_RESULT=PASS` proves server-side loopback handshakes only.
  End-to-end client acceptance of the new hysteria2/obfs lanes is confirmed
  by the operator refreshing the vasma subscription in v2rayN (nodes are
  already published) and using them.
- The stale sing-box 1.13.8 binary was upgraded in place by the vasma flow;
  the weekly sing-box wrapper now pins `1.14.4`.
- A vasma FULL reinstall (menu 1) would wipe the sing-box tree (upstream
  `cleanUp`); recovery is re-running the scripted install above (UUIDs are
  reused) plus the hardening lanes. Kernel core updates (menu 16) are
  unaffected.
- First cron-driven dual run: Friday 14:20/14:30 UTC; expect two heartbeats
  (`kernel-xray`, `kernel-sing-box`) in doctor.

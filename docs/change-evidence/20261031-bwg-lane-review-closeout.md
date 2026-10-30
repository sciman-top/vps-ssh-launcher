# bwg 车道审查收口：21470 退役 + Hysteria2 masquerade + 14 域对齐（2026-10-31）

 originates in the 2026-10-31 full-chain lane review (P1 21470 dual-bind,
 P2 schedule guard / rollback captures, P3 masquerade / 14-domain /
 iptables). All remote operations read-only-probed first, backup-first,
 rollback-on-failure, single-host (bwg only). No credentials or
 subscription URLs in this receipt.

## 1. P1: TCP 21470 same-port dual-inbound collision (retired)

- Finding: `02_VLESS_TCP_inbounds.json` (VLESSTCP, tls) and
  `12_VLESS_XHTTP_inbounds.json` (VLESSRealityXHTTP, reality) both listened
  on `*:21470` (SO_REUSEPORT, zero `already in use` log lines). Kernel
  4-tuple hash dispatched each connection randomly; real-client loopback
  probe measured `ok=5/8` with client-side
  `REALITY: received real certificate` on the misses.
- Retire: `mv 02_VLESS_TCP_inbounds.json 02_VLESS_TCP_inbounds.json.disabled`
  after backup `/var/backups/vlesstcp-retire.ZP9zk6`; `xray run -test` ok,
  one controlled restart; `listeners_21470_after=1`; camo probe
  `https://127.0.0.1:21470/` → 200 via reality fallback → 33387 decoy.
- Verification: `XHTTP_21470_PROBE ok=15/16` (first-connection warm-up miss,
  zero real-certificate errors) then `ok=16/16` on a fresh client run.
  Status file `maintenance-status/vlesstcp-retire.status` result=success.
- Subscription republished via the documented vasma flow (`7→6→y`, salt
  reuse, same URLs): exit 0, subscribe file mtime advanced
  (19:10:31Z). The retired VLESSTCP node drops out of the subscription;
  clients pick it up on their next subscription refresh.
- Replay: a vasma full reinstall recreates `02_VLESS_TCP_inbounds.json`;
  re-run the same retire flow (backup → disable → `run -test` → restart →
  probe → republish) and re-assert a single 21470 listener.

## 2. Hysteria2 base-lane masquerade + sing-box log level (new script)

- `scripts/hysteria2_masquerade_lane.ps1` (bwg-only, `-Apply`): ensures a
  proxy masquerade `{"type":"proxy","url":"https://fq.sciman.top:33387/",
  "rewrite_host":true}` on the UDP-443 base inbound of
  `06_hysteria2_inbounds.json`, and projects
  `97_vps_ssh_launcher_singbox_log.json` with `{"log":{"level":"info"}}`.
  The obfs lane is untouched (salamander rejects non-obfuscated packets
  before the protocol layer, so a masquerade there is unreachable).
- Schema note: sing-box 1.14.4 rejects `masquerade.path` and
  `masquerade.name` (`json: unknown field`), so the file-masquerade idea was
  dropped in favor of proxy-to-camouflage-vhost (real LE certificate
  verifies, hairpinned locally).
- Apply: `SINGBOX_MERGE_FAILED` on the first attempt (schema discovery
  above) rolled back cleanly (`ROLLBACK_VERIFIED`, zero residue); the
  corrected schema applied with `HYSTERIA2_MASQUERADE_APPLIED`
  (backup `/var/backups/hysteria2-masquerade.k3ma1l`). Read-back shows
  `masquerade_set=yes log_level_pinned=yes` and
  `masquerade=already_configured_healthy` (idempotent noop path).
- Post-apply acceptance: `ACCEPTANCE_RESULT=PASS`
  (reality-xray / hysteria2-base / hysteria2-obfs all PASS).

## 3. sing-box lane IPv4 pin 12 → 14 domains (aligns xray lane)

- `vasma_kernel_update_cron.ps1` bwg table adds `claude.com`,
  `platform.claude.com` (mirrors the xray lane's 14-domain scope from
  `google_ipv4_routing.ps1`, closing the 2026-10-30 receipt's noted
  asymmetry); zz intentionally stays at 10.
- Wrapper `-Apply` (backup `/var/backups/v2ray-agent-maint.vkTbM2`, pins
  fresh: vasma `ae56d372…`, sing-box binary `1264361b…`, v1.14.4,
  schedule canonical `30 14 * * 5`), then triggered first-run:
  fragment re-projected (jq: 14 entries ending
  `...,claude.com,platform.claude.com`), merged config changed → one
  controlled restart, kernel pin skip, `kernel-sing-box.status`
  result=success exit=0, UDP 443+8444 listening.

## 4. Schedule guard (repo-side, no remote write)

- `-Schedule` default is now per-kernel canonical (`xray 20 14`, 
  `sing-box 30 14`), and a dual-lane profile refuses the sibling lane's
  canonical slot — same-minute cron lines would share the flock and
  silently starve one lane (exit 75) every week. The bwg projection now
  carries this guard; today's apply passed it.

## 5. iptables 8444/udp allow row (completeness)

- `iptables -I INPUT 1 -p udp --dport 8444 -m comment --comment
  "allow 8444/udp(hysteria2-obfs)" -j ACCEPT` added and persisted via
  `netfilter-persistent save` (`/etc/iptables/rules.v4`). Zero functional
  change under the ACCEPT policy; protects the obfs lane if the policy is
  ever tightened.

## 6. Final verification sweep

- `ACCEPTANCE_RESULT=PASS` (x3 runs across the change sequence).
- Independent post-review XHTTP probe: `20/20` successful `204` responses on
  TCP `21470`, with `0` `received real certificate` client errors.
- Public-path acceptance passed for BWG (`reality-xray-public:443`,
  Hysteria2 base/obfs by `fq.sciman.top`) and ZZ
  (`reality-singbox-public:22752`, Hysteria2 base/obfs by `zz.sciman.top`).
- The refreshed default subscription now carries `3` VLESS + `1` Hysteria2
  nodes on ports `443/21470/10094`; the retired duplicate `21470` VLESS node is
  gone.
- Guardrails doctor: `DOCTOR_CONTRACT_OK`, 13x drift MATCH, kernel-xray and
  kernel-sing-box heartbeats result=success.
- `scripts/proxy_hardening_drift.ps1 -Profile bwg`: `HARDENING_DRIFT=NONE`.
- Listeners: single TCP 21470 (XHTTP-Reality), UDP 443+8444, TCP
  443/10094 unchanged; camouflage vhost 33387 serves the decoy page.

## Related commits

- `424f121` (with absorbed parallel batch: kernel UNVERIFIED exit-10
  semantics, acceptance `-PublicName`, nginx rollback test double, zz
  updater runbook) — rollback fail-closed captures, renewtls mirror test,
  env-independent CI refusal test.
- This receipt's commit: masquerade lane script + tests, schedule guard,
  14-domain table, ruff reformatting of owned lines.

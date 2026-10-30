# BWG 双内核 wrapper 重投影与受控验收（2026-10-31）

## Scope

- Target: `bwg` only; `zz` 未连接、未写入。
- Change: 重新投影 Xray 与 sing-box 内核更新 wrapper/cron，使用当前 BWG
  已安装二进制和 vasma 的真实 SHA-256 pin。
- No kernel update was triggered. No proxy configuration, certificate, credential,
  subscription, or service restart was requested by this run.

## Projection

- vasma: `v3.5.33`, SHA-256
  `ae56d372cfc9249235bffd0ee1887a53ab2dd8a6b811a7005d51532f6f5d07e5`.
- Xray: `v26.10.29`, installed binary pin
  `e783b9d2b46ed1ab3617c59b04dfbce38c999322d018c6e060f18d88bf6158aa`.
- sing-box: `v1.14.4`, installed binary SHA-256 read fresh before projection:
  `1264361b5f8868c8768f7555d3e77e20118bf503c41ac08bb290a0d99056aa3a`.
- Cron remains split under `/etc/cron.d/vps-launcher-kernel-update`:
  Xray Friday 14:20 UTC and sing-box Friday 14:30 UTC.
- Each apply returned an `APPLY_BACKUP_DIR` under `/var/backups/`; the later
  sing-box re-apply was needed because the first supplied binary pin was not the
  full installed SHA-256. No binary update occurred during either apply.

## Readback

- Both wrappers reported `syntax-ok`.
- Both lanes reported `vasma_pin=fresh binary_pin=fresh`.
- The Xray and sing-box services remained available; the existing TCP/UDP
  listeners remained present.

## Controlled acceptance

Fresh read-only BWG runs after the final projection:

```text
HARDENING_DRIFT=NONE
LANE=reality-xray RESULT=PASS port=45987 sid=<redacted>
LANE=hysteria2-base RESULT=PASS port=443
LANE=hysteria2-obfs RESULT=PASS port=8444 obfs=salamander
ACCEPTANCE_RESULT=PASS
```

The acceptance is a real client handshake from BWG through each local proxy
listener and a `gstatic` `204` response. It proves controlled host-side
acceptance (`host_loaded` / `controlled_acceptance`), not natural external
client use across every network or carrier.

## Rollback

Use the `APPLY_BACKUP_DIR` printed by the corresponding projection command and
restore only that lane's wrapper/cron files through the existing maintenance
rollback path. Git rollback does not replace remote restoration.

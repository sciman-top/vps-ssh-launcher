# BWG Hysteria2 base masquerade lane（2026-10-31）

## Scope

- Target: `bwg` only. `zz` has no Hysteria2 base inbound and was not changed.
- Remote write: Hysteria2 base `443` gained `proxy` masquerade to the local
  nginx camouflage vhost, plus the projected `97_` sing-box info-log fragment.
- No subscription, credential, certificate, family, Reality key, or Xray
  inbound was changed. The Hysteria2 obfs lane was not touched because
  salamander rejects un-obfuscated packets before protocol masquerade.

## Transaction

- Apply backups:
  `/var/backups/hysteria2-masquerade.k3ma1l` and
  `/var/backups/hysteria2-masquerade.AP9ELZ`.
- Projected files:
  - `06_hysteria2_inbounds.json`:
    `e18c2710f0e01ceb74f63fba7a353cf97b98025a8444f173f35f9c97f3a9bfbb`
  - `97_vps_ssh_launcher_singbox_log.json`:
    `3e46e24929418f99f279db554dd9bd2349f1beea41e42d7d0eda85e831a33373`
  - merged `conf/config.json`:
    `114ed0d206ecbec3c88b740b1c55af53c76f829e13f87eb4026d2f38f7e3938a`
- Contract: base `listen_port=443`; masquerade
  `{"type":"proxy","url":"https://fq.sciman.top:33387/","rewrite_host":true}`;
  projected log level `info`.

## Verification

- `sing-box check` and Xray `run -test` both pass.
- Services `sing-box`, `xray`, `nginx` remain active.
- UDP listeners remain exactly `443` and `8444`.
- `bwg_full_maintenance.ps1 -Mode Observe -RunIntegration` completed all
  `11/11` steps, including the new `proxy-hardening-read` step.
- `HARDENING_DRIFT=NONE`, including the new
  `hysteria2-masquerade` check.
- Controlled acceptance after the change:

```text
LANE=reality-xray RESULT=PASS port=45987 sid=<redacted>
LANE=reality-xray-public RESULT=PASS name=fq.sciman.top port=443
LANE=hysteria2-base RESULT=PASS port=443
LANE=hysteria2-base-public RESULT=PASS name=fq.sciman.top port=443
LANE=hysteria2-obfs RESULT=PASS port=8444 obfs=salamander
LANE=hysteria2-obfs-public RESULT=PASS name=fq.sciman.top port=8444 obfs=salamander
ACCEPTANCE_RESULT=PASS
```

## Boundary

The verification proves config/schema loading, service/listener continuity,
and authenticated base/obfs client data paths. It does not independently prove
the HTTP/3 response body an unauthenticated generic prober receives from the
masquerade target; that remains a separate active-probing acceptance item.

## Rollback

Use `scripts/hysteria2_masquerade_lane.ps1` transaction rollback inputs, or
restore the two source fragments and merged config from the printed backup,
then run `sing-box check` and restart only `sing-box`. Git rollback does not
replace remote restoration.

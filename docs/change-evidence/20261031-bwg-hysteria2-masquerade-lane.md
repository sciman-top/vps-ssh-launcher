# BWG + ZZ Hysteria2 base masquerade lane（2026-10-31）

## Scope

- Targets: `bwg` first, verified, then `zz`; each host uses its own nginx
  camouflage vhost as the masquerade target.
- Remote write: Hysteria2 base `443` gained `proxy` masquerade to the local
  nginx camouflage vhost, plus the projected `97_` sing-box info-log fragment.
- No subscription, credential, certificate, family, Reality key, or Xray
  inbound was changed. The Hysteria2 obfs lane was not touched because
  salamander rejects un-obfuscated packets before protocol masquerade.

## Transaction

- BWG apply backups:
  `/var/backups/hysteria2-masquerade.k3ma1l` and
  `/var/backups/hysteria2-masquerade.AP9ELZ`.
- ZZ apply backup: `/var/backups/hysteria2-masquerade.eb8HI3`.
- Projected files on BWG:
  - `06_hysteria2_inbounds.json`:
    `e18c2710f0e01ceb74f63fba7a353cf97b98025a8444f173f35f9c97f3a9bfbb`
  - `97_vps_ssh_launcher_singbox_log.json`:
    `3e46e24929418f99f279db554dd9bd2349f1beea41e42d7d0eda85e831a33373`
  - merged `conf/config.json`:
    `114ed0d206ecbec3c88b740b1c55af53c76f829e13f87eb4026d2f38f7e3938a`
- Projected files on ZZ:
  - `06_hysteria2_inbounds.json`:
    `bb2ac2cba651826ccbd8d89c7f9a031a1f51026e525fdc786f96f675441cd40b`
  - `97_vps_ssh_launcher_singbox_log.json`:
    `3e46e24929418f99f279db554dd9bd2349f1beea41e42d7d0eda85e831a33373`
  - merged `conf/config.json`:
    `c28e28721bc9db54a7d57fed0c453f5dde26be9b7b42533e5679511f027edffb`
- Contract on both hosts: base `listen_port=443`; masquerade
  `{"type":"proxy","url":"https://fq.sciman.top:33387/","rewrite_host":true}`;
  ZZ uses `{"type":"proxy","url":"https://zz.sciman.top:12378/","rewrite_host":true}`;
  both project log level `info`.

## Verification

- `sing-box check` and Xray `run -test` pass on BWG; `sing-box check` passes on
  ZZ. The expected services remain active.
- UDP listeners remain `443` and `8444` on both hosts.
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

```text
LANE=reality-singbox RESULT=PASS port=22752 sid=<redacted>
LANE=reality-singbox-public RESULT=PASS name=zz.sciman.top port=22752
LANE=hysteria2-base RESULT=PASS port=443
LANE=hysteria2-base-public RESULT=PASS name=zz.sciman.top port=443
LANE=hysteria2-obfs RESULT=PASS port=8444 obfs=salamander
LANE=hysteria2-obfs-public RESULT=PASS name=zz.sciman.top port=8444 obfs=salamander
ACCEPTANCE_RESULT=PASS
```

## Boundary

The verification proves config/schema loading, service/listener continuity,
and authenticated base/obfs client data paths. It does not independently prove
the HTTP/3 response body an unauthenticated generic prober receives from the
masquerade target; that remains a separate active-probing acceptance item.

## Rollback

Use `scripts/hysteria2_masquerade_lane.ps1` transaction rollback inputs, or
restore the two source fragments and merged config from the printed per-host
backup, then run `sing-box check` and restart only `sing-box`. Git rollback
does not replace remote restoration.

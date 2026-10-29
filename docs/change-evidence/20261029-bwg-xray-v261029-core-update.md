# BWG Xray v26.10.29 core update + kernel-wrapper pin re-fresh (2026-10-29)

## Scope

Authorized BWG-only full-chain maintenance round. One real remote write
family: the kernel update lane (`scripts/vasma_kernel_update_cron.ps1
-Apply` plus one manual wrapper run). No CPA, OAuth credential, lane,
plugin, nginx, or account change. zz was probed read-only only.

## Trigger and background

- Full-chain Observe run `20261029T120120Z-63d15365` passed 9/9 steps; the
  read lanes were green, but direct comparison of the deployed wrapper
  against live state found a latent fail-closed defect: the wrapper
  embedded `EXPECTED_VASMA_SHA256="b7c7adf3…"` (vasma v3.5.30) while the
  deployed `/usr/bin/vasma` was already `ba83e425…` (v3.5.31, repo pin
  `4b45e8b` landed 2026-10-29 18:31 +0800). The next weekly cron
  (2026-10-30 14:20 UTC) would have exited 13 (`vasma SHA-256 mismatch;
  refusing unreviewed script`) and turned the strict doctor's
  `kernel-xray` heartbeat red — the exact 2026-10-23 incident shape.
  Root cause of the recurrence: the vasma pin advance transaction does not
  include a kernel wrapper re-pin step, and no read gate compared the two.
- Upstream Xray-core published stable `v26.10.29` (2026-10-29): routine
  fixes plus geodata loader hardening (file-extension checks, download
  size/frequency limits). No security emergency; the host ran `v26.10.21`.
  Advancing the pin to the current upstream latest simultaneously healed
  the stale vasma pin and restored the weekly `latest == pin` contract.

## Pins and supply chain

- Official asset `Xray-linux-64.zip` for `v26.10.29`: zip SHA-256
  `8a5f3e6a1057e4bd0f8a7c57c9d91204b4be0b74222a6e210d070603101163c2`,
  cross-checked against the release `.dgst` manifest (`SHA2-256=` line).
  Inner `xray` binary SHA-256
  `e783b9d2b46ed1ab3617c59b04dfbce38c999322d018c6e060f18d88bf6158aa`.
- Deployed vasma sha pin: `ba83e425df585e54909922fd41e452e790dc33b01c9f85
  8ebd99bdf346b5943a` (v3.5.31, matches the repo source pin commit).

## Transaction and verification

1. `vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Version
   26.10.29 -InstalledSha256 e783b9d2… -VasmaSha256 ba83e425… -Apply` →
   exit 0, deploy backup `/var/backups/v2ray-agent-maint.lFeIYB`, cron
   `20 14 * * 5` intact, anchors present, wrapper `bash -n` ok.
2. Manual wrapper run (`bash /etc/v2ray-agent/auto_update_xray.sh`, single
   remote command per its contract) → exit 0.
3. Independent second-command verification: `Xray 26.10.29` running, live
   binary sha == pin, service active, public listeners 443/21470/10094 up,
   log markers `vasma Xray-core update done` + `Configuration OK`,
   `PRUNE scope=core_backups policy=keep_8`.
4. Post gates: `google_ipv4_routing.ps1` read check green (drop-in,
   markers, `ROUTE_CONTRACT=GOOGLE_AND_CLAUDE_IPV4_ONLY_DOMAINS`,
   config test ok); strict doctor `DOCTOR_CONTRACT_OK` with
   `heartbeat=kernel-xray result=success exit_code=0 age_hours=0`.
5. Client impact: brief xray restart at 2026-10-29 12:07 UTC; protocol
   config unchanged, v2rayN subscription links unchanged.

## Repository fix (this commit)

`scripts/vasma_kernel_update_cron.ps1` read lane gained an `==kernel-pin==`
section: it compares each deployed wrapper's embedded `TARGET_VERSION` /
`EXPECTED_SHA256` / `EXPECTED_VASMA_SHA256` against the live vasma and
kernel binaries and reports `pin_freshness … vasma_pin=… binary_pin=…`
verdicts (plus `state=missing`). Report-only by design: staleness must be
visible between the two -Apply transactions without making the read step
abort the full-chain orchestrator. Verified live on bwg: `vasma_pin=fresh
binary_pin=fresh pinned_version=v26.10.29`. Contract tests extended in
`tests/test_kernel_update_cron.py`.

## Follow-ups

- zz shows the identical pre-incident state (deployed vasma v3.5.31
  `ba83e425…` vs sing-box wrapper pin `b7c7adf3…`, kernel binary pin
  v1.14.4 `1264361b…` still matching the live sing-box). Per the
  one-machine-at-a-time rule the zz re-Apply awaits explicit user
  confirmation:
  `vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box -Version
  1.14.4 -InstalledSha256 1264361b… -VasmaSha256 ba83e425… -Apply`
  (pin unchanged; refreshes the vasma sha so Friday's run takes the
  skip-reinstall fast path instead of exit 13).
- Xray startup deprecation notice: `freedom.domainStrategy` auto-migrates
  to `sockopt.domainStrategy` today; when upstream removes the old key the
  vasma-owned fragments need a regeneration pass. Watch item, vasma domain.

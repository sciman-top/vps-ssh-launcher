# BWG v2ray-agent v3.5.32 updater projection and apply

## Scope

- Target: `bwg` only.
- Change: project the reviewed v2ray-agent updater and apply the pinned
  official `install.sh` candidate.
- No Xray/sing-box binary change, proxy configuration change, OAuth change,
  provider route change, concurrency change, retry increase, or cooldown
  release was performed.
- No ZZ connection or maintenance action was performed in this run.

## Evidence

- Repository commit: `c539704` (`v3.5.32` source pin).
- Source commit: `ade6c61b41e0bf631fa53602507a424b49ce2fa5`.
- Candidate SHA-256:
  `04d2bf9c105a164a483da7a2cce184849ac864fc8aa6b404128ede1a644612f1`.
- Pre-apply installed SHA-256:
  `ba83e425df585e54909922fd41e452e790dc33b01c9f858ebd99bdf346b5943a`
  (`v3.5.31`).
- Read-only preflight: updater syntax and runtime verification passed;
  Xray/Nginx/fail2ban were active.
- Projection result: `RUNTIME_VERIFY_OK`, `UPDATER_PROJECTED`, backup created at
  `/var/backups/v2ray-agent-script-update-deploy.gNIVDi`.
- Apply result: `APPLIED`, backup created at
  `/var/backups/v2ray-agent-script-update.iXNMYX`.
- Post-apply installed SHA-256 matches the candidate and the installed version
  is `v3.5.32`.
- Post-apply readback: `RUNTIME_VERIFY_OK`; the updater log recorded
  `APPLIED ... version=v3.5.32`.
- The existing Xray wrapper was then reprojected with the fresh Xray binary
  pin `e783b9d2b46ed1ab3617c59b04dfbce38c999322d018c6e060f18d88bf6158aa`
  and the new vasma pin. Its readback reported
  `pin_freshness ... vasma_pin=fresh binary_pin=fresh`, with no Xray binary or
  configuration change. Projection backup: `/var/backups/v2ray-agent-maint.BkpoIR`.
- CPA pre/post doctor: `DOCTOR_CONTRACT_OK`; projection drift remained
  `MATCH`; OAuth lanes remained `OPEN`.
- Controlled acceptance after the updater apply used the maintained default
  non-OAuth path only: `python3 /opt/cliproxyapi/cpa-health.py generation` →
  `HEALTH_OK`, followed by `readiness` → `HEALTH_OK`. This was one bounded
  request with no OAuth generation, no retry, and no route or concurrency
  change.

## Boundary and follow-up

- The provider route manifest still contains one explicitly reviewed plaintext
  HTTP slot. A no-credential probe confirmed that its HTTPS endpoint returns
  `wrong version number` while HTTP responds; no unverified HTTPS replacement
  was guessed or projected.
- The known Antigravity served-name observation
  (`gemini-3.8-flash-n` for requested `gemini-3.8-flash-high`) remains a single
  documented naming pair. It was not rewritten or used as evidence to change
  OAuth routing.
- This receipt proves repository projection and BWG host readback only. It is
  not provider quality, account-compliance, or natural-user acceptance. The
  controlled non-OAuth result proves the selected acceptance path only.

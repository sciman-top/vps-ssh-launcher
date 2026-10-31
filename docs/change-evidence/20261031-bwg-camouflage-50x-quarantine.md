# 2026-10-31 bwg camouflage stock 50x.html quarantine

## Context

Round-12 GFW-focused review (512d33e) found the nginx stock `50x.html` still
serving 200 on every vhost that renders `/usr/share/nginx/html` (the 33387
REALITY-fallback/subscribe vhost and the 15994 XHTTP-front vhost) — the same
fingerprint class as the "Welcome to nginx!" page the camouflage lane already
removes. 512d33e extended the lane with a guarded stock-artifact quarantine;
this receipt records the controlled apply on bwg.

## Change

- `pwsh -NoProfile -File scripts/nginx_camouflage_site.ps1 -Profile bwg -Apply`
- Pre-flight: `REF_COUNT=0` — no nginx config references `50x.html`, so the
  removal guard takes the remove path; root index == decoy index
  (sha `f73eb0c2…`) before and after the run.
- Lane output: `STOCK_FILE_REMOVED file=50x.html`, `nginx -t` ok,
  `CAMOUFLAGE_APPLIED`, backup `/var/backups/nginx-camouflage.jVVTUK`,
  status file `result=success` / `exit_code=0`.
- User content in the web root (site-template zips/`psd/`/`__MACOSX/`,
  ~60 MB) was deliberately left untouched; quarantining that content is a
  separate item awaiting its own authorization.

## Acceptance evidence

Behavioral probes (loopback, SNI `fq.sciman.top`):

- 33387: `/` → 200 (decoy), `/50x.html` → 404 (200 before this apply),
  unknown path → 404
- 15994: `/` → 200, `/50x.html` → 404, unknown path → 404
- `Server:` header carries no version (`server: nginx`)
- `proxy_hardening_drift.ps1 -Profile bwg` → `HARDENING_DRIFT=NONE`
  (stock-artifact, obfs-contract, xhttp-path and true-SNI checks all green),
  all designed listeners up.

## Rollback

`/var/backups/nginx-camouflage.jVVTUK` holds the full pre-apply document
root; restoring it brings `50x.html` back verbatim.

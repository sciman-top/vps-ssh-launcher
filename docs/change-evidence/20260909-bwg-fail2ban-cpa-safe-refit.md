# 2026-09-09 bwg fail2ban filter refit for the cpa_safe access log

## Cause

- The same-day guardrails apply (see
  `20260909-bwg-cpa-public-guardrails.md`) switched the gateway access log to
  the redacted `cpa_safe` format, which no longer records `$request`. The
  fail2ban `cpa-gateway` jail installed earlier that day matched on the
  request-line shape (`"METHOD path HTTP/x" 401`), so it could no longer match
  any line — fail-open (no wrong bans), but the 401-burst protection was
  blind.

## Change

- Only `/etc/fail2ban/filter.d/cpa-gateway.conf` was rewritten (the guardrails
  script does not manage fail2ban, so future `-Apply` runs cannot conflict):
  - old: `^<HOST> .+ "[A-Z]+ .+" (401|403) `
  - new: `^<HOST> method=[A-Z]+ status=(401|403) `
  - plus a comment pointing at the `cpa_safe` format definition so the
    dependency is discoverable.
- Pre-change backup: `/root/cpa-fail2ban-refit-backup-20260909T152355Z/`.
- Rollback: restore that file and `fail2ban-client reload`. No other host
  state is involved; nginx was not touched this time.

## Verification

- `fail2ban-client -t` OK; reload OK; `fail2ban-client get cpa-gateway
  failregex` shows the new pattern loaded in the running jail.
- `fail2ban-regex` against the live log: 78 of 341 lines matched. All 78 are
  loopback `status=401` lines from the guardrails session's controlled
  replay — no external 401/403 has reached the gateway, i.e. the random
  capability path has not been probed from the internet so far.
- The jail's `ignoreip = 127.0.0.1/8 ::1` still excludes loopback from
  failure counting, so replays and local probes can never trigger a ban.
- `sshd` jail and all other services untouched.

## Residual note

- Real-world end-to-end ban counting will only engage when an external source
  accumulates >=20 auth failures within 600 s on port 8443; filter- and
  jail-level loading are verified above, and deliberate self-ban testing from
  a non-ignored address was intentionally not performed on the live host.

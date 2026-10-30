# BWG/ZZ dual-kernel consistency: per-profile sing-box IPv4 scope + updater symmetry

## Scope

- Repository commit: `8ea8b83` (per-profile domain list + updater sing-box
  check branch + contract tests).
- Targets: both hosts, one at a time (bwg first, verified before zz).
- Motivation: after the dual-kernel landing, the bwg sing-box lane enforced
  only the 10-domain Google/Gemini IPv4 pin while the bwg xray lane enforces
  12 (anthropic.com/claude.ai since 2026-10-19) — proxy-lane traffic to
  Anthropic domains could exit via the IPv6 SIT tunnel, diverging from the
  xray lane's egress family policy. zz stays at the 10-domain design
  (v4-only host). The updater's verify_services validated xray config but
  not sing-box config.

## Evidence

- bwg: updater re-projected (backup `…script-update-deploy.utooTq`) and
  triggered → `SERVICES_OK` with the new `sing-box check` branch executing;
  sing-box lane wrapper re-projected (backup `…maint.lGM1Uw`, schedule
  unchanged `30 14 * * 5`) and first-run controlled: exit 0, merged config
  changed (10→12 domains) → one controlled restart, kernel pin skip,
  heartbeat `result=success`. Deployed fragment verified by jq: 12 entries
  ending `...,anthropic.com,claude.ai`. Listeners recovered: UDP 443+8444,
  TCP 443/21470/10094.
- zz: updater re-projected and triggered → `SERVICES_OK` (1 sing-box check
  site); wrapper re-projected (backup `…maint.gK6ytV`) and first-run
  controlled: exit 0, NO merged-config-change line (zero restart — sing-box
  process start time unchanged), fragment still 10 entries, service active.
  Read lane: both pins fresh, `vasma_live=ae56d372…`, camouflage/obfs state
  held (`decoy-applied`, obfs 8444 listening).
- bwg post-change acceptance: `proxy_lane_acceptance.ps1 -Profile bwg` →
  reality-xray / hysteria2-base / hysteria2-obfs all PASS,
  `ACCEPTANCE_RESULT=PASS`; strict doctor `DOCTOR_CONTRACT_OK` with both
  kernel heartbeats green.
- Parallel-session note: an unrelated in-flight hardening batch
  (obfs-lane rollback hardening etc.) was present in the shared worktree;
  this change set was committed with explicit paths, no overlap.

## Boundary

- zz's next weekly cron (Fri 14:30 UTC) runs the re-rendered wrapper with
  identical 10-domain semantics — no behavior change expected; bwg's next
  cron runs the 12-domain fragment (already live and asserted).
- No proxy parameter, kernel version, or CPA change; watch items unchanged
  (11/1 monthly first run, v8.0.48 + plugin 0.8.90/91 soak).

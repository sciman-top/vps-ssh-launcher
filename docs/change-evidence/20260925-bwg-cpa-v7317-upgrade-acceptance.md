# BWG CPA v7.3.17 upgrade acceptance

## Scope

- Target: BWG only. ZZ was not accessed or changed.
- Requested change: upgrade CPA v7.3.16 -> v7.3.17 with explicit attention to
  account/rate-limit/degradation risk control, executing the 2026-09-25
  release evaluation's recommendations.
- v7.3.17 relevance (release notes + PR #6090 diff reviewed against the
  deployment): the only change on our lanes is #6090 — OAuth codex
  Execute/compact requests now send the native `X-Codex-Routing-Hint`
  (derived from the final upstream body, so it cannot disagree with the
  request; API-key lanes explicitly untouched; overridable via auth `header:`
  rules; no config switch). This is fingerprint-alignment in the same class
  as the UA cloak and matches the 2026-09-20 posture: follow upstream default
  fidelity, build no self-made evasion. No cooldown, quota, or turn-state
  semantics changed; the 60s transient cooldown assertion is untouched.
- The standing "patch AUTO after 72h soak" policy was consciously waived for
  this one hop by user decision (2026-09-25), with post-apply acceptance as
  the compensating control.

## Timeline

- Pre-flight read-only strict doctor (this session, ~08:05Z): CPA
  v7.3.16@sha256:ca6af8b1…, `DOCTOR_CONTRACT_OK`, 12-ID catalog, 
  `oauth_monitor=OK` (days_left=3, refresh age 163.5h, failures 7d=0),
  `cooldown_state=none`, `luna_state=available`.
- The image upgrade itself was completed by the parallel owner session at
  2026-09-25T08:10:38Z: `compose.yml` image pinned to
  `eceasy/cli-proxy-api:v7.3.17@sha256:a1dffb9c…` and the container
  recreated. This hop did not go through `auto-update.sh` (no CANDIDATE/OK
  lines for 08:10; the timer's 04:04Z run had correctly deferred because
  v7.3.17 was not yet 72h mature).
- This session independently prepared a soak-relaxed updater variant
  (single-point `>= 259200` -> `>= 0` edit, sha-verified against the deployed
  script). It ran `--check` only (read-only selection) and was deleted once
  the parallel upgrade was detected; it never executed `--apply` and no
  double-write occurred.
- Post-upgrade strict doctor (this session): `DOCTOR_CONTRACT_OK` (exit 0),
  container `v7.3.17@sha256:a1dffb9c…` `restart=0`, 12/12 catalog IDs, 
  `oauth_monitor=OK`, `cooldown_state=none`, `luna_state=available`,
  `oauth_refresh_failures_7d=0`.
- `auth_unavailable` retained sample unchanged: 3 x
  `openai-compatible-ai.input.im/gpt-6-sol` (known relay-lane transient).
- All doctor runs were non-consuming; no OAuth-lane automation traffic was
  generated (2026-09-22 silence discipline preserved).

## Hygiene (P3 closeout from the 2026-09-24 review)

- The `.bak-20260908` item no longer existed at review time.
- Removed four stale root-level config backups:
  `config.yaml.bak-20260921-panel-always-on`,
  `config.yaml.bak-20260921-usage-stats`,
  `config.yaml.bak-keyrot-20260921T073929Z`,
  `config.yaml.bak-keyrot-rmold-20260921T113640Z`. Each was pre-verified as a
  retired-key-bearing old config variant (`api-keys` differ from the live
  config; the rmold variant held two retired keys). The `backups/` apply
  rollback set (13 dirs) was untouched; zero `.bak-*` entries remain in the
  CPA root.

## Residual watch

- OAuth refresh point ~2026-09-27 (days_left=3); oauth-monitor thresholds
  unchanged (<=22h FAIL / <=72h WARN).
- If any new OAuth-lane risk-control signal appears, the routing-hint header
  (new in v7.3.17) is the one new variable on that lane and comes first in
  attribution.
- v7.3.16 stays locally available for image-level rollback until the prune
  policy (current_plus_previous) rotates it after the next verified update.

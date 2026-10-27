# 2026-10-27 BWG CPA gpt-6.1-terra tombstone (catalog contract restore)

## Goal and scope

The codex OAuth upstream added `gpt-6.1-terra` to its discovered model list and
CPA auto-registered it into the client `/v1/models` catalog. The strict doctor
`client-model-catalog` contract failed closed on the undeclared alias
(`MODEL_IDS_UNKNOWN=gpt-6.1-terra`, `DOCTOR_CONTRACT_FAILED`), which would have
blocked future `-Apply` transactions. This record covers the read-only
discovery round, the exclusion-only fix, and its deployment from the
filing-machine session. No credential was rotated, deleted, quarantined, or
substituted; no new model was adopted into the serving path.

## Attribution (read-only)

- Doctor first observed the failure at `2026-10-27T03:4xZ` window (second run
  with full capture under `/tmp/doctor-20261027-full.log`, redacted). All other
  gates were green: `admission-integrity=OK` (live admission sha equals repo
  pin `0b2c88ab…`), projection drift 13/13 `MATCH`, `lane-hold=OK` (three
  OAuth lanes `OPEN`; retired Claude tombstone inert), cooldown `none`,
  substitution 7d `0`.
- The remote `config.yaml` declares exactly the manifest aliases (12 at that
  point) and does not contain `gpt-6.1-terra`; the only dynamic registration
  source is codex OAuth discovery. Precedent: the `gpt-5.6-terra` and
  antigravity tombstones exist for the same mechanism.
- CPA source (registered external checkout, read-only) confirms
  `oauthExcludedModelsForConfig` + `applyExcludedModels` filter OAuth-kind
  registration per provider, so a projected `oauth.excluded-models` entry
  removes the slug from the catalog.

## Routing decision

`gpt-6.1-terra` is not adopted: no generation acceptance exists, the context
pin is not verifiable from available evidence, and the model had zero traffic.
Following the never-adopted-name pattern (`gpt-6-sol`, `gpt-reserve`),
`gpt-6.1-terra` was added to both manifest exclusion lists
(`oauth_exclusions`, `codex_api_key_exclusions`) so neither lane claims it.
Adoption later would be its own catalog checklist round with a live
single-shot acceptance.

## Production projection

- Source commit: `c59e268`
  (`fix(cpa): tombstone upstream gpt-6.1-terra from both lanes`); full gates
  green before apply: 802 passed + 398 subtests, bandit x2, ruff check/format
  (67 files), mypy clean.
- BWG `-Apply` projected the manifest-derived exclusions. Pre-change backup:
  `/root/cpa-guardrails-backup-20261027T035530.206020993Z`; the authorized
  keep-8 cleanup removed one older backup. `READY_STATUS=200`; the apply
  recreated the CPA container (process-local cooldown cleared as a
  consequence; none cleared manually).
- Apply receipt `catalog_summary` reported `models=11` (was 12) with all
  retired-alias assertions false and `has_slot3_gpt56_terra=True`.
- Fresh strict doctor exited 0: `DOCTOR_CONTRACT_OK`, `MODEL_IDS_UNKNOWN=none`
  (11 catalog IDs), projection drift 13/13 `MATCH`, `admission-integrity=OK`,
  `lane-hold=OK`.

## Post-apply single-shot probe

One non-stream request through the full admission chain
(`127.0.0.1:8318/v1/chat/completions`, `glm-5.3-flash`, `max_tokens=16`):

- `PROBE_STATUS=200`, `PROBE_MODEL=glm-5.3-flash` (echo correct),
  `PROBE_COMPLETION_TOKENS=16`; all four admission lanes `inflight=0`,
  `hold=None`.
- `finish_reason=length`, not `stop`: the probe's own 16-token budget was
  consumed by generation (`completion_tokens` equals the cap), so the
  response is budget-truncated by construction, not a service failure. Per
  the single-attempt discipline no follow-up was fired. The catalog-adoption
  `finish=stop` bar does not apply to an exclusion-only round; serving
  health after the container restart is proven by the 200 + correct model
  echo + token accounting + lanes OPEN + `DOCTOR_CONTRACT_OK`.

## Residual notes for the operation machine

- The catalog contract was red between the upstream slug appearing and this
  apply; if the operation machine has an in-flight adoption round for
  `gpt-6.1-terra`, revert path: remove the tombstone entries, add the model
  to the owning route/provider with a verified context pin, full gates,
  commit, `-Apply`, strict doctor, single-shot `finish=stop` acceptance.
- 24h journal aggregates at discovery time showed the operation machine's
  v8.0.44/0.9.77 rounds (`flock/maintenance_busy` guard refusals 13) and
  ordinary upstream capacity sheds (18 events); nothing actionable.

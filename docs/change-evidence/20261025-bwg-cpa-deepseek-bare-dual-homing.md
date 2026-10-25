# BWG CPA deepseek bare-name dual-homing (2026-10-25, second revision)

## Scope and decision

Same-day revision of
[the alias contract](20261025-bwg-cpa-deepseek-open-alias-contract.md):
after reviewing both options the user decided to retire the
`deepseek-v4.1-flash-open` alias (it lived in production for ~3 hours) and
dual-home the bare name `deepseek-v4.1-flash` across the workbuddy plugin
lane and the opencode slot-2 channel, with execution-layer auto-switching.
The analogous merge for `gpt-6.1-sol` (ChatGPT Plus OAuth + ai.input.im)
was assessed and deliberately NOT done: failover would land on the most
ban-sensitive subscription account, the chatgpt lane's dominant failure
mode (lane cooldown during capacity windows) is not covered by pooling,
and the relay leg is currently dead. `gpt-6.1-sol-input` stays as the
deliberate relay-only pin.

## Changes (commit 0bccbc4)

- `scripts/remote/cpa_provider_routes.json`: slot-2 entry becomes
  `{name: deepseek-v4.1-flash, alias: deepseek-v4.1-flash,
  max-context-length: 1000000}`; `optional_models` back to `[]` (the name
  is already readiness-optional through the workbuddy plugin model list).
  No tombstone for the retired alias (longcat precedent): the doctor
  unknown-ID gate fail-closes on any resurrection.
- `scripts/remote/cpa-health.py`: provider matrix probe skip set gains
  `deepseek-v4.1-flash` — a matrix probe would ride the pooled route onto
  the workbuddy shared-subscription credential.
- Tests: `PROVIDER_MATRIX_TAIL` drops the alias (the bare name never
  appears there), guardrails manifest oracle updated, provider echo map
  entry removed (name == alias now).
- Runbooks: slot-2 paragraph, channel table, and luna-slot route history
  rewritten to the dual-homing end state, including the observed boundary
  below.

## Verification

- Full gates exit 0: 779 passed, 1 skipped (opt-in SSH), 372 subtests.
- `-Apply` (backup `/root/cpa-guardrails-backup-20261025T155336.061378180Z`):
  `GUARDRAILS_APPLIED`, `READY_STATUS=200`. The slot-2 `models` list was
  regenerated from the manifest, removing the alias from live config; the
  container restart also cleared the workbuddy plugin's in-memory state
  (selected-account pointer reset to default, rate-limit cache emptied).
- Strict doctor exit 0, `DOCTOR_CONTRACT_OK`: catalog 11 IDs (alias gone),
  `MODEL_IDS_UNKNOWN=none`, `MODEL_CONTEXT_DRIFT=none`
  (`deepseek-v4.1-flash:1000000` retained), `catalog_oauth_missing=none`,
  four lanes OPEN (claude tombstone HELD as designed), projection MATCH.
- Controlled live probes (curl; the lane's CF-1010 fingerprint rule
  applies to any downstream python UA):
  - 8317 direct, bare name, request `f4b422a5` @ 23:55:05Z: the observed
    one-request failover chain is the core acceptance — workbuddy CN
    `eb624ce3` 6004 (261 ms, reset 10-26 12:34:44+08) → INTL `0faaff85`
    6004 (222 ms) → INTL `ca64800b` 6004 (211 ms) → session-affinity
    binding `provider=mixed` on `openai-compatibility:opencode` → HTTP
    200, 1.46 s, `finish_reason=stop`, content exactly `OK`. All three
    workbuddy credentials are traversed per request at the CPA layer
    (account-level switching, orthogonal to the plugin's selected mode).
  - 8318 admission, bare name, request `1b3958ed` @ 23:55:47Z: HTTP 200,
    0.81 s, routed straight to opencode (`LCP cache hit`, zero workbuddy
    retries) — the 6004 failures never surfaced to admission because the
    opencode leg absorbs them with a 200 final status, so the lane stays
    OPEN and the name remains serveable through 8318 during the Tencent
    window. Steady state is cooldown+affinity steering straight to
    opencode with no wasted upstream calls.

## Boundary (observed, corrects the pre-deployment prediction)

The pre-deployment analysis predicted an admission model-park fast-fail
during 6004 windows. The pooled regime supersedes that: admission sees
only the final status (200), so no park re-arms while either leg can
serve. The real fast-fail boundary is both legs failing in the same
request: the terminal failure then surfaces to admission and the
lane/model cooldown answers 429 with Retry-After. Matrix probing stays
exempt for the name. The workbuddy lane budget (max_inflight=1) still
serializes all bare-name traffic through admission.

## Rollback

Revert 0bccbc4 and re-run `-Apply` (restores the alias regime from the
same-day backup chain); no credential or auth file was touched.

## Residual

- workbuddy selected-account pointer reset to default (CN) by the apply
  restart; irrelevant during the 6004 window (all three creds parked
  until 10-26 12:16/12:34/18:16 +08), and the user may re-select in
  cockpit afterwards if desired.
- The desktop picker shows the bare name (already present); the retired
  alias disappears from the catalog on the client's next model-list
  refresh. The earlier user-side cockpit re-save step is now moot for
  this name.

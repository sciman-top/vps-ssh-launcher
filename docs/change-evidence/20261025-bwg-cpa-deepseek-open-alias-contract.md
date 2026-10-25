# BWG CPA `deepseek-v4.1-flash-open` alias contract sync (2026-10-25)

## Scope and authorization

The user added the public alias `deepseek-v4.1-flash-open` to the opencode
slot-2 entry through the CPA management panel (config mtime 15:55Z window,
SHA `2534c38e…`), then authorized continuous repair/optimization through
completion. This change brings the repository contract in line with that
live alias and converges the remote config to the manifest. No credential,
lane, admission, or concurrency change. `scheduler_mode=selected` on the
workbuddy plugin is untouched (policy decision retained from 2026-10-20).

## Facts established before the change

- Upstream `https://opencode.ai/zen/go/v1/models` (curl) serves the bare
  name `deepseek-v4.1-flash` only; `-open` is a CPA-side public alias. The
  bare name stays claimed by the workbuddy plugin lane (admission
  `workbuddy-oauth`, max_inflight=1); the alias is in no lane, so admission
  treats it as passthrough.
- The alias does not appear in the Codex desktop picker because the cockpit
  managed model catalog rebuild (`rebuild_stale_managed_model_catalogs`)
  refreshes structure/capability fields from the OLD model list only and
  never adds new models; the provider entry must be re-saved in cockpit for
  the alias to reach the picker. (User-side step, outside this repo.)
- Config `priority:` on an openai-compatibility entry does not reach the
  auth selector on the live path (`ApplyAuthPriorityMetadata` is driven by
  credential-file metadata only), so no priority edit was made;
  availability-based switching is carried by round-robin selection plus
  per-credential per-model cooldown, verified in source
  (`pickNextMixed`/`highestPriorityAuths`/`tried`-set retry).

## Repository changes (commit 56c768f)

- `scripts/remote/cpa_provider_routes.json`: slot-2 opencode gains
  `{name: deepseek-v4.1-flash, alias: deepseek-v4.1-flash-open,
  max-context-length: 1000000}` (1000000 matches the workbuddy settings
  twin pin); alias listed in `optional_models` (its sole backing credential
  may cool, so readiness must not require it).
- `tests/cpa_catalog_expectations.py`: `PROVIDER_MATRIX_TAIL` gains the
  alias in manifest order.
- `tests/script_validation_support.py`: `CPA_TEST_PROVIDER_ALIASES` maps
  the alias to the upstream echo name (first run failed 9 tests because
  fixtures echoed the alias where the generation contract expects the
  upstream name).
- `tests/test_cpa_guardrails_script.py`: manifest oracle extended.
- `docs/runbooks/cpa-gateway.md` (slot-2 paragraph + channel table) and
  `docs/runbooks/cpa-oauth-luna-slot.md`: catalog paragraphs dated.

## Verification

- Full gates exit 0: 779 passed, 1 skipped (opt-in real SSH integration),
  372 subtests; build/bandit/ruff/mypy green. First run failed 9
  matrix/quality tests on the echo-name gap and one ruff-format line; both
  fixed before the final green run.
- `-Apply` (backup `/root/cpa-guardrails-backup-20261025T152426.983087232Z`):
  `GUARDRAILS_APPLIED`, `READY_STATUS=200`. The apply regenerated the
  slot-2 `models` list from the manifest (deepcopy preserves the reviewed
  `request-scoped-errors` 1010 rule and pins), converging the panel edit
  with the manifest.
- Strict doctor exit 0, `DOCTOR_CONTRACT_OK`: 12 catalog IDs including the
  alias, `MODEL_IDS_UNKNOWN=none`, `MODEL_CONTEXT_DRIFT=none`
  (`deepseek-v4.1-flash-open:1000000` in the client context catalog),
  `catalog_oauth_missing=none`, 13 projection entries MATCH.
- Controlled single-shot live probe via `127.0.0.1:8318` (curl; the lane's
  known CF-1010 fingerprint rule rejects python-urllib UA, observed again
  as the 403 in the journal): HTTP 200, 0.74 s, responded
  model=`deepseek-v4.1-flash` (alias translated to the upstream name —
  routed to the opencode slot, not the parked workbuddy lane),
  `finish_reason=stop`, content exactly `OK`. Admission journal attributes
  the request `lane=passthrough model=deepseek-v4.1-flash-open
  status=200 successful=true` (request_id `03d950104e1046549f2acd0e85f848a4`).

## Rollback

Repo: revert 56c768f. Remote: restore the config from the backup dir above
and re-run `-Apply` from the reverted manifest; no credential or auth file
was touched.

## Residual

- The alias reaching the Codex desktop picker still requires the user to
  re-save the provider entry in cockpit (managed catalog does not
  auto-add), then refresh the desktop model list.
- workbuddy 6004 model-level park on the bare name remains in force until
  the Tencent windows expire (10-26 12:16/12:34/18:16 +08); during those
  windows the alias is the only live DeepSeek path (opencode, passthrough).

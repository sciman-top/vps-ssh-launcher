# BWG WorkBuddy realm contract projection (2026-10-29 +08:00)

## Scope and authorization

The user authorized continued repair and optimization for the BWG CPA risk
review. This transaction projected the reviewed WorkBuddy CN/INTL route
contract and the matching admission parser to **bwg**. It did not create,
delete, refresh, rotate, quarantine, restore, or otherwise read OAuth
credentials. It did not raise concurrency, retries, queue limits, or alter a
cooldown.

The implementation commit was \`8771712\` (\`fix(cpa): declare workbuddy realm
pooling\`). The local source was clean and the committed \`main\` revision was
used as the projection source.

## Causal change

The route manifest previously described only WorkBuddy INTL even though the
deployed plugin pins both \`models_cn\` and \`models_intl\` to
\`deepseek-v4.1-flash\`. The risk audit could therefore not express the full
CN/INTL topology. The manifest now declares both realms.

\`cpa-admission.py\` accepts a repeated model alias only when it is listed in
\`pooled_aliases\` and all declarations resolve to the same admission lane.
An undeclared duplicate or one spanning distinct lanes remains a fail-closed
configuration error. The admission integrity pin was rotated in the same
commit.

This records topology and preserves the existing single \`workbuddy-oauth\`
admission budget. It does not make \`deepseek-v4.1-flash\` a strict realm-aware
routing selector; the existing plugin can still use its reviewed shared
fallback behavior.

## Preflight and controlled deployment

The initial strict doctor observed that BWG still had the previous admission
and route-manifest bytes. It rejected the ordinary bulk apply through the
admission-integrity guard, as designed. Before any host mutation, the
reviewed admission transaction dry-run reported:

- live pin \`357872f07c07e6bc48a03a881b67030f7badb57ff3a9fbe3a6a62e7a9c1c9a1e\`;
- candidate pin \`12d0a356dab11cb187800e2bafb6991f12b5ee64fe09e278d80fba6f36b38e7d\`;
- staged Python syntax valid; and
- no durable hold in any active lane.

Apply used the reviewed narrow admission transaction. It temporarily returned
\`503\` with \`Retry-After: 30\` on the two public admission routes, took the
shared maintenance lock, waited for the admission lanes to become idle, and
atomically replaced only \`cpa-admission.py\` and its pin. It produced
\`ADMISSION_IDLE_OK\`, \`ADMISSION_INTEGRITY_OK\`, \`DEPLOY_OK\`, and
\`INGRESS_RESTORED\`.

The narrow transaction backup is
\`/opt/cliproxyapi/backups/20261028T161603.330612403Z-admission-disconnect\`.
The temporary ingress backup is
\`/opt/cliproxyapi/backups/20261028T161600.534591446Z-admission-ingress-window\`.

The subsequent guarded \`-Apply\` projected the route manifest and normal CPA
source set under the same maintenance lock. Every file hash was verified as
written. It completed with \`GUARDRAILS_APPLIED\`; its rollback backup is
\`/root/cpa-guardrails-backup-20261028T161857.554897000Z\`.

## Verification

- Fresh strict doctor: \`DOCTOR_CONTRACT_OK\` (exit 0).
- Admission integrity: \`OK\`; all projection entries, including the route
  manifest, admission source, and pin, reported \`MATCH\`.
- CPA image remained \`v8.0.46\`; loopback listeners \`8317\` and \`8318\`, plus
  the public \`8443\` listener, were healthy.
- WorkBuddy retained \`scheduler_mode=credits\`, \`account_max_concurrent=1\`,
  \`identity_switch_on_429=false\`, pinned CN/INTL model lists, and disabled
  background task/lifecycle/token keepalive.
- The post-projection \`cache-canary glm-5.3-flash\` returned a first-sample
  write and a second-sample \`cache_read_tokens=4096/4114\`,
  \`hit_ratio=0.9956\`, and \`HEALTH_OK\`. It uses the non-OAuth GLM lane.
- Local full gate: 806 passed, 1 skipped, 398 subtests; Bandit, Ruff, format,
  and mypy passed.

\`repo_verified\`, \`filesystem_projected\`, and \`host_loaded\` are established
for this transaction. The cache canary is limited controlled acceptance for
the GLM lane. No OAuth generation request was created, so this transaction
does not establish OAuth provider acceptance, account eligibility, or natural
traffic quality.

## Retained operational risks

- ChatGPT admission was in \`WAIT_UPSTREAM_WINDOW\` with no durable hold; its
  provider-supplied cooldown was preserved.
- \`http-bridge-8003\` remains a declared cleartext HTTP provider. No verified
  HTTPS replacement was available, so it was not migrated or removed.
- Model-substitution observation remained one known served-name pair:
  \`gemini-3.8-flash-n\` for requested \`gemini-3.8-flash-high\`. It is an
  observation, not evidence that a request had equivalent quality.

## Rollback

Restore only the relevant backup under the shared maintenance lock. For the
admission change, restore \`cpa-admission.py\` and
\`cpa-admission.sha256\` from the narrow transaction backup, run
\`/usr/local/libexec/cpa-admission-integrity-check\`, restart
\`cpa-admission.service\`, and confirm \`/healthz\` plus the pre-deploy hold
state. For a broader projection regression, restore the guarded-apply backup
and use its built-in rollback path. Do not use a Git revert as a substitute
for host rollback.

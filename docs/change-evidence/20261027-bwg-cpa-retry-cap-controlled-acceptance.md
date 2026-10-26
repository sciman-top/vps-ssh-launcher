# 2026-10-27 BWG CPA credential retry cap and controlled acceptance

## Goal and scope

This records the follow-up to the 2026-10-26 BWG risk-control deployment. The
existing OpenAI/GPT, Antigravity/Gemini, WorkBuddy CN/INTL OAuth credentials and
API-key routes were retained. No credential was rotated, deleted, quarantined,
or substituted.

The committed policy sets `routing.retry.max-retry-credentials=1` while keeping
`routing.retry.request-retry=0`. This limits the number of distinct credentials
tried in one credential-retry round and prevents CPA from opening additional
request-retry rounds. It reduces credential spread for one request; it does not
provide account isolation across separate requests. WorkBuddy `credits`
scheduling may still choose another eligible credential on a later request.

## Production projection

- Source commit: `d2d34c9248faa254fd64eec8ef5e1e27daa50075`
  (`fix(cpa): cap credentials tried per retry round`). The policy validator,
  apply script, tests, and runbooks agree on the value `1`.
- BWG `-Apply` projected the policy. Pre-change backup:
  `/root/cpa-guardrails-backup-20261026T145549.832781952Z`.
  The authorized keep-8 cleanup removed one older backup.
- The apply recreated the CPA container, which clears process-local cooldown
  and WorkBuddy park/scheduler state. No cooldown was manually cleared and no
  OAuth generation request was made to obtain acceptance.
- Fresh strict doctor at `2026-10-26T16:32:27Z` (`2026-10-27 00:32:27 +08:00`)
  exited 0 with `DOCTOR_CONTRACT_OK`, `POLICY_OK`, `semantic-policy=OK`,
  projection `MATCH`, and the exact retry values visible. CPA v8.0.43 was
  running at its pinned image digest; container `restart=0` and start time
  `2026-10-26T14:56:09Z`. ChatGPT, Antigravity and WorkBuddy lanes were `OPEN`;
  the retired Claude tombstone remained inert. Auth/config permissions and
  catalog/context checks passed.

## Routing decisions

- Keep `session-affinity=true`, TTL `1h`, and `session-affinity-subagents=false`.
  This retains an existing session's credential choice and cache locality while
  allowing subagents to use separate eligible credentials.
- Keep `routing.strategy=fill-first`. It controls initial selection order; it
  is not a quota or account-safety control. Switching to round-robin would
  distribute new sessions across the pool without reducing the provider's
  shared account/project limits.
- Keep `switch-preview-model=false` in the compatibility policy. The official
  [CLIProxyAPI v8.0.43 configuration example](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.43/config.example.yaml)
  has no v8 runtime option with that name, so this value is not counted as an
  effective runtime guard.
- The previous pacing/cache review remains current: OpenAI-compatible SSE
  forwarding had healthy chunk pacing; the GLM cache sample was `4096/4114`
  (`0.9956`), so no cache-key or cache TTL change was justified. See
  [v8.0.43/plugin review](20261026-bwg-cpa-v8043-plugin-0970.md).

## Isolated fixture acceptance

The first current-binary fixture run exposed a harness drift: its synthetic
catalog omitted required API-key model `beam-free`, so local readiness passed
but the generation gate correctly reported `UPSTREAM_UNAVAILABLE`. This did
not touch production config or the running container. The fixture now has a
loopback-only OpenCode API-key route and fails locally if its configured model
set drifts from the health contract.

Full acceptance then passed using the binary extracted from the running
v8.0.43 container (`SHA-256 93de5d0881d39a503a3a5b8c104f80814afc2321285dcf020120183903a999b5`).
All staged repository files passed SHA-256 verification. The runner used a
private mount and network namespace, with `/opt/cliproxyapi`, `/tmp`, `/run`,
and `/var/lib/vps-ssh-launcher` shadowed; the namespace had loopback only, no
non-loopback address, and no default route. It staged no production config,
OAuth files, or real API keys. Only synthetic keys and loopback services were
used.

- Synthetic overload: HTTP 200 with the capacity marker and exactly one
  upstream call.
- During cooldown: rejected with 503 and zero upstream calls.
- After 62 seconds: same CPA process recovered with a completed response and
  one upstream call; `cpa-health.py generation` returned `HEALTH_OK`.
- Updater `start_fail` and unexpected-model exposure: exit 1, old compose
  restored, real fixture CPA readiness passed, rollback logged.
- Sustained upstream 503: exit 10, no rollback, local readiness passed, and
  `UNVERIFIED` recorded.
- Normal update: exit 0, new fixture compose retained, readiness passed.
- Final marker: `ACCEPTANCE_RESULT=PASS`. The exact temporary directory was
  removed after its marker and absolute path were checked. Production config
  hash, container ID, restart count, and start time were unchanged across the
  fixture run.
- This fixture does not directly exercise a two-credential failure/failover
  case. The retry-cap value is verified in repository policy and the loaded
  production config; forcing a real OAuth failure to observe credential
  selection was deliberately excluded.

## Natural traffic and limits

The fresh doctor is a bounded read-only view; it does not generate OAuth
traffic. At the observation time, the CPA container had been running for about
1 hour 36 minutes since the projection. Its 24-hour journal spans both sides of
the projection, so it is not a 24-hour post-change soak. The sample contained
four upstream capacity 429
events: two ChatGPT OAuth model-scoped events (`gpt-6-luna` and
`gpt-6.1-sol`) and two GLM events. The three configured OAuth lanes had no
durable hold; natural completions were present for ChatGPT, Antigravity, and
WorkBuddy. WorkBuddy had 744 completed results and one not-completed result in
the bounded journal. The ChatGPT requests continued to complete, but the two
429 capacity events show that the retry cap does not prevent upstream
capacity/rate windows.

The same snapshot recorded 21 `inspection_limit` classifications for
`chatgpt-oauth/gpt-6-luna` and 239 retained-log warnings for one Gemini
model-name substitution pair. These are bounded-observability/model-name
signals, not proof of account enforcement or quality degradation. Existing
telemetry does not identify the exact
oversized SSE event or measure all provider TTFB/cache fields. Increasing the
inspection limit or changing cache behavior without that evidence is not
justified.

Provider eligibility and terms compliance remain external facts. OpenAI
consumer OAuth, Antigravity and WorkBuddy terms are not made safe by CPA
settings; the policy review is recorded in
[`cpa-gateway.md`](../runbooks/cpa-gateway.md). No claim is made that these
accounts cannot be rate-limited, suspended, or quality-limited. The production
OAuth acceptance remains natural-use observation only.

## Repository verification

After the fixture correction, `pwsh -NoProfile -ExecutionPolicy Bypass -File
scripts/run_gates.ps1 -Profile Full` exited 0: build, both Bandit passes, Ruff,
format, and mypy passed; pytest reported `802 passed, 1 skipped, 397 subtests
passed`. The skip is opt-in real SSH integration. The controlled fixture is
not a live OAuth or provider-eligibility test.

Rollback reference: retain the listed backup and use the reviewed guardrail
apply/restore path; do not restore a whole old config over newer authentication
state.

# 2026-09-19 BWG CLIProxyAPI relay re-enable evidence

## Scope

- Target: profile `bwg` only; profile `zz` was not connected, checked, or
  modified.
- Goal: restore the owner-approved bare catalog topology by projecting the
  `relay-8003` provider ENABLED again (undoing the 2026-09-19 stopgap disable),
  keeping declarative registration of exactly `gpt-5.6-sol` / `gpt-5.6-terra`.
- Credentials, auth files, client keys, and random path values were not
  printed, rotated, or committed.

## Why the disable existed and why it is undone

- The 2026-09-19 disable (`d0d1cb4`) was a latency-driven stopgap after the
  relay's 5s-120s+ small-prompt tail and broken SSE flush polluted the health
  gate. It was enforced at three layers: `cpa_policy.py` (assert
  `disabled: true`), `cpa_bwg_guardrails.ps1` (project the flag), and
  `cpa-health.py` (`RELAY_DISABLED` exit 13).
- Fresh upstream probes from BWG before the change: `gpt-5.6-terra` answered
  3/3 HTTP 200 `finish=stop` at 1.5s/3.4s/4.3s; `gpt-5.6-sol` returned a
  stable 503 `No available channel ... (distributor)` — the upstream
  distributor has removed the Sol channel (upstream catalog shrank 39 -> 38).
- The owner then requested the 4-route topology be restored; the CPA side can
  only project the route, Sol's 200 availability is an upstream-side
  dependency and returns without any config change once the channel is back.

## Local changes and proof

- `scripts/remote/cpa_policy.py`: relay assertion flipped to
  `disabled must be absent or false` (constant renamed to
  `EXPECTED_ENABLED_OPENAI_PROVIDER`).
- `scripts/cpa_bwg_guardrails.ps1`: apply projection now `pop("disabled")`
  for relay-8003 (config_after and expected symmetric); OAuth-logout
  survival set now includes `gpt-5.6-sol` / `gpt-5.6-terra`.
- `scripts/remote/cpa-health.py`: comment-only sync; runtime logic was
  already relay-state aware (`_relay_enabled`), Sol/Terra stay out of the
  scheduled gate (Luna-only smoke) and remain opt-in via relay-soft and
  explicit generation-all.
- `test_scripts.py`: policy fixture enabled; violation case now
  `disabled: True`.
- README + runbooks (`cpa-oauth-luna-slot.md`, `cpa-stale-cooldown-recovery.md`)
  wording: bare baseline is five IDs; Sol may 503 while its upstream channel
  is absent.
- Focused proof: `39 passed, 107 subtests passed`.
- Full gate: `126 passed, 1 skipped, 141 subtests passed`; Bandit, Ruff,
  format, mypy all green.

## BWG projection

The apply command was:

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply
```

The transaction created this host backup before writing:

```text
/root/cpa-guardrails-backup-20260919T090123.863840881Z
```

Fresh apply/readback evidence:

```text
READY_STATUS=200
models=5
has_bare_luna=True
has_relay_bare_sol=True
has_relay_bare_terra=True
has_glm=True
has_deepseek=True
has_r1=False
GUARDRAILS_APPLIED
```

A fresh read-only strict doctor rerun ended with `DOCTOR_CONTRACT_OK`.

Post-apply SHA-256 of the changed non-secret projected files:

```text
/opt/cliproxyapi/config.yaml   cd7e22900c23ca87709777ec3d352232924ad483b268ce5e9d27e6f111771599
/opt/cliproxyapi/cpa-health.py 3c464df65d21d687450f7992c7adce00844e605dbd83ee10906978c5c0d6026d
/opt/cliproxyapi/cpa_policy.py 9138c5c269158a7b8513fbab4ec740cd5115951be292e03de685bb36f563490b
```

## live_accepted: five-bare-catalog generation probes

Single-shot, no retry, flat 1024 max_tokens, loopback data plane, after the
apply restart:

```text
gpt-5.6-luna     200 finish=stop 2.5s   (ChatGPT Plus OAuth)
gpt-5.6-sol      503 model_not_found 0.1s (upstream channel absent; passthrough)
gpt-5.6-terra    200 finish=stop 11.6s  (relay-8003)
glm-5.3-flash    200 finish=stop 1.5s   (GLM Coding Plan)
deepseek-flash   200 finish=stop 0.9s   (DeepSeek official API)
```

`ACCEPTANCE_RESULT=PASS` for the restored topology: exactly five bare IDs in
`/v1/models`; the four owner-approved routes all serve real 200 traffic;
Sol is declared but upstream-blocked until the distributor restores the
channel.

## Evidence boundary and rollback

- `repo_verified` / `filesystem_projected` / `host_loaded` / `live_accepted`:
  all yes for this change (gates, apply readback, strict doctor, and the
  generation probes above on the fresh process).
- Rollback, if explicitly required, is host-local: restore the CPA config and
  projected files from `/root/cpa-guardrails-backup-20260919T090123.863840881Z`,
  restart `cli-proxy-api`, and rerun the strict BWG doctor. Git rollback alone
  cannot restore the remote runtime configuration.

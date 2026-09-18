# 2026-09-19 BWG CLIProxyAPI bootstrap bound and relay disable evidence

## Scope

- Target: profile `bwg` only; profile `zz` was not connected, checked, or
  modified.
- Goal: bound CLIProxyAPI v7.3.7 stream bootstrap waiting and remove the
  measured high-latency `relay-8003` path from the BWG public model catalog.
- Data-plane contract retained: public Nginx with one random path on `8443`,
  CPA loopback-only on `127.0.0.1:8317`, no SSH tunnel.
- Credentials, auth files, client keys, subscription URLs, and random path
  values were not printed, rotated, or committed.

## Local changes and proof

- `scripts/remote/cpa_policy.py` now requires:
  - `codex.stream-bootstrap-buffering: true`
  - `codex.stream-bootstrap-timeout: "20s"`
  - exactly one `openai-compatibility` entry named `relay-8003` with
    `disabled: true`
- `scripts/cpa_bwg_guardrails.ps1` applies only those approved fields in its
  existing backup-first atomic transaction and expects the three stable bare
  routes after OAuth Luna deactivation.
- `scripts/remote/cpa-health.py` excludes Sol/Terra when relay-8003 is disabled
  and returns `RELAY_DISABLED` (exit `13`) without a relay request.
- Focused proof: `39 passed, 106 subtests passed`.
- Full gate: `126 passed, 1 skipped, 140 subtests passed`; Bandit, Ruff,
  format, mypy, PowerShell parse, and `git diff --check` passed.

## BWG projection

The apply command was:

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply
```

The transaction created this host backup before writing:

```text
/root/cpa-guardrails-backup-20260918T195716.276210218Z
```

Fresh apply/readback evidence:

```text
READY_STATUS=200
models=3
has_bare_luna=True
has_glm=True
has_deepseek=True
has_relay_bare_sol=False
has_relay_bare_terra=False
GUARDRAILS_APPLIED
DOCTOR_CONTRACT_OK
```

The three exposed bare models are `glm-5.3-flash`, `gpt-5.6-luna`, and
`deepseek-flash`. A redacted provider-state readback returned:

```text
[('zhipu-plan', False), ('deepseek', False), ('relay-8003', True)]
RELAY_DISABLED
RELAY_SOFT_EXIT=13
```

The container remained `running` with `restart=0`, image
`eceasy/cli-proxy-api:v7.3.7`, and the expected loopback/public listeners.
Nginx merged configuration, random-route count, fail2ban contract, safe access
log, logrotate, and exact loopback binding all passed the strict doctor.

## Remote readback hashes

These are SHA-256 values of non-secret projected files after the apply:

```text
/opt/cliproxyapi/config.yaml      c6ef4852fe773e58c4d7ad6fd8b24c98d7232d0e4d6cd22e227781bc1d5c4f4c
/opt/cliproxyapi/compose.yml      f213e035becb3a54af9c93fd2ac68172af0f659b6a91bc389cae5e9bb41aecd4
/opt/cliproxyapi/auto-update.sh   55222286e6235d9714ba61e24b799dd358f8e95def987877a0197a78bf7039f8
/opt/cliproxyapi/cpa-health.py    42b9c333aa62a75fc57ba652017b7855db5dcf2acdf9151aefaa91ce59249ab4
/opt/cliproxyapi/cpa_policy.py    f1265a3502aab10f2098b3c761d89107b439801dc6cc474936aaa405cc3e4a78
/etc/nginx/conf.d/cpa-gateway.conf a2853703bba57a209b49b384f4b50ebce450cfed3d271feb70bc4fc9d81fe9fd
```

## Evidence boundary and rollback

- `repo_verified`: yes — local focused and full gates passed.
- `filesystem_projected`: yes — backup-first apply, atomic projection, and
  post-write hashes/readback passed.
- `host_loaded`: yes for the projected config/runtime — the container restarted
  at the apply timestamp and strict doctor passed on the fresh process.
- `live_accepted`: yes — 2026-09-19 closeout verification (second session,
  readback + single-shot probes, no retry). Independent hash readback matched
  the projected values above; `/v1/models` returns exactly the three bare ids.
  Single-shot generation probes through the loopback data plane covered ALL
  three bare routes (no retry): `glm-5.3-flash` 200 `finish=stop` 1.8s,
  `gpt-5.6-luna` 200 `finish=stop` 1.7s (flat 1024 max_tokens), and
  `deepseek-flash` 200 `finish=stop` 0.6s — the bootstrap-bound codex path
  serves real traffic. Independent full-gate rerun on the committed HEAD:
  126 passed / 141 subtests, Bandit, Ruff, format, and mypy all green.

Rollback, if explicitly required, is host-local and must use the backup above:

```text
restore the backed-up CPA config and projected files from
/root/cpa-guardrails-backup-20260918T195716.276210218Z,
restart cli-proxy-api, then rerun the strict BWG doctor
```

Git rollback alone cannot restore the remote runtime configuration.

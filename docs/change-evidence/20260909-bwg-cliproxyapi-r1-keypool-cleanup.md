# 2026-09-09 bwg CLIProxyAPI r1 key-pool cleanup and channel review

## Scope

- Target: profile `bwg` only. Profile `zz` was not connected, checked, or
  modified.
- Real remote writes: exactly two — (1) removal of one dead codex relay
  credential from `/opt/cliproxyapi/config.yaml` (`codex-api-key` 3 -> 2
  entries), (2) one `docker restart cli-proxy-api` to guarantee the new
  config load. No other host, nginx, xray, or auth state was changed.
- Companion local slice (same session): root `AGENTS.md` now declares the
  SSH connection config source of truth and the working Git Bash launcher
  invocation; the stale repo-root `target.json` copy was deleted (untracked,
  held no credentials). The authoritative user-level config gained
  `default: bwg` first, so no-arg launcher behavior is preserved.

## Review findings (read-only, before the write)

- Channel verdicts ~21:00 UTC: OAuth `gpt-5.6-luna` OK (token to 2026-09-18);
  `glm-5.3-flash` OK (provider key field is spelled `api-key-entries`);
  `deepseek-chat`/`deepseek-reasoner` failing with upstream 401 because the
  `REPLACE_WITH_DEEPSEEK_KEY` placeholder was never filled; `r2`
  (codex.ciii.club) still broken, error shape now 502/503 "temporarily
  unavailable" (2026-09-08: 403-style "access forbidden").
- r1 key A (ai.input.im, prefix `r1`) had its station group changed to a
  non-codex set (deepseek-v4/glm/grok/image): direct `/v1/responses` returns
  404 "Model ... not supported by any configured account in this group", so
  the credential could not serve any `r1/*` model and only added a guaranteed
  failed first attempt under `fill-first`.

## Applied change

- Backup: `/root/cliproxyapi-before-r1-keyfix-20260909T131153Z/` and
  `...T131318Z/` each contain the pre-change `config.yaml`; sha256
  `b6581a45...` verified identical to the live file before the edit.
- Edit flow: fetch -> local yaml edit -> semantic equality assertion (only
  the one entry removed, every other key value identical; an earlier aborted
  run was caught by this assertion and changed nothing) -> upload `.new` ->
  server-side parse check -> `chmod 600` -> atomic `mv`. Live config sha256
  after: `ac0cea63...`.
- One container restart (ready in ~4 s, loopback `/v1/models` -> 401) because
  the process predated the edit and config hot-reload has no observable
  signal; same "recreate to guarantee load" pattern as the 2026-09-09
  risk-hardening receipt.

## Verification (post-restart)

- Startup log: `5 clients (1 auth entries + 2 Codex keys + 2 OpenAI-compat)`
  — the dead credential is not loaded.
- `/v1/models`: 20 models (was 22). `deepseek-*` are no longer advertised:
  with `save-cooldown-status: true` the placeholder credential starts
  cooldown-marked, so CPA now returns `503 auth_unavailable` instead of
  proxying a guaranteed 401 — a more honest catalog. r1 (9) and r2 (9) sets
  intact; allowlist enforced (`gpt-5.6-sol` -> 400); management API 403
  host-local / 404 via nginx.
- Smokes via the public path: `gpt-5.6-luna` 200 (1.6-1.9 s), `glm-5.3-flash`
  200, `r1/gpt-5.6-luna` 200 (slow, see below), `r2/*` 503 station-side.

## Honest finding: r1 latency is station-side, not fallback cost

- Pre-change hypothesis (r1 luna ~8.6 s = failed key-A attempt + retry) is
  disproved: after the cleanup, r1 latency rose during the observation window
  (8.6 s -> 19.8 s post-restart -> ~60 s one hour later) while OAuth luna
  stayed at 1.6-1.9 s. ai.input.im is progressively degrading tonight;
  codex.ciii.club additionally surfaced a new 503 shape ("No upstream account
  permits image generation for this request").
- Consequence: the key-A removal is correct hygiene but not a latency fix.
  If ai.input.im stays at this level, the r1 channel is effectively unusable
  regardless of local config. DeepSeek key and r2 admin remain user-side
  follow-ups.

## Rollback

- Host: `cp /root/cliproxyapi-before-r1-keyfix-20260909T131318Z/config.yaml
  /opt/cliproxyapi/config.yaml && docker restart cli-proxy-api` restores the
  3-entry `codex-api-key` block.
- Local: revert the `AGENTS.md` hunk; the deleted repo-root `target.json`
  needs no restoration (the user-level config is the single source of truth).

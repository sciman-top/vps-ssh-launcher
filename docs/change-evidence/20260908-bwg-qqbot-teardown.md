# 2026-09-08 bwg QQ bot (AstrBot/NapCat) teardown evidence

## Scope

- Target: profile `bwg` only; public address and SSH port redacted. Profile
  `zz` was not connected, checked, or modified.
- Real remote writes: yes — removal of the decommissioned QQ bot deployment
  (AstrBot + NapCat). The user explicitly requested this cleanup.
- Background: the bot runtime was migrated off this host to the new VPS on
  2026-07-31 (see `qq-codex-bot/docs/change-evidence/20260731-new-vps-new-qq-migration.md`);
  the old deployment kept running here until today. This is a retirement, not
  a migration — the old bot QQ account is already abandoned.
- Explicit exclusions (verified untouched): CLIProxyAPI gateway
  (`/opt/cliproxyapi`, containers `cli-proxy-api`, loopback port 8317),
  xray, nginx, fail2ban, cron, sshd.

## Removed state

- Containers (compose project `qq-codex-bot`, workdir `/opt/qq-codex-bot`):
  `napcat` (`mlikiowa/napcat-docker:latest`, loopback 6099) and `astrbot`
  (`qq-codex-bot/astrbot-formula-render:v4.25.5`, loopback 6185), plus network
  `qq-codex-bot_botnet`, via `docker compose down`.
- Images: the two images above (about 8.4 GB of tagged layers) plus 1.718 GB
  of on-host build cache left over from building the astrbot image
  (`docker builder prune`).
- Directory: `/opt/qq-codex-bot` (409 MB, including `ntqq/` QQ login state,
  `napcat/` config, AstrBot `data/`, `.env`, prior `.deploy-backups`).
- systemd units (all pointed into `/opt/qq-codex-bot/scripts/`):
  `qq-codex-bot-cleanup.{service,timer}`,
  `qq-codex-bot-login-watchdog.{service,timer}`,
  `qq-codex-bot-provider-watchdog.{service,timer}` — disabled, stopped,
  unit files removed, `daemon-reload` run. The login watchdog service had
  been in a failed/restart loop before removal.
- Root crontab had no bot entries (only the three pre-existing
  `v2ray-agent` maintenance lines), so no cron cleanup was needed.

## Backup and rollback

- Pre-deletion backup on the server:
  `/root/qq-codex-bot-backup-20260908-131720.tar.gz` (290 MB, mode 600,
  contains QQ login tokens — do not download into any repo).
  sha256: `1c92af4ebc900e0ef701f9b75134af93b81b079020aa6772704bd573faa1e7e4`.
- Rollback: extract the tarball back to `/opt`, restore the six unit files
  (reconstructable from the qq-codex-bot repo scripts), then
  `docker compose up -d`. The pinned images would need a rebuild/pull since
  they were removed. Keep the tarball only until the user confirms no
  recovery need; deleting it is a separate authorized step.

## Post-teardown verification (fresh reads)

- `docker ps`: only `cli-proxy-api` remains (gateway). Note: the temporary
  `cpa-login` OAuth helper container (up earlier today) exited and was
  auto-removed on its own during the window; it was not touched by this
  teardown.
- Listening ports: loopback 6099/6185 gone; remaining listeners are the
  pre-existing xray (443 + two high ports + one loopback), sshd (high port),
  nginx (one high public port + two loopback), and CPA on 127.0.0.1:8317.
- CPA health: `GET /v1/models` on 127.0.0.1:8317 returned HTTP 401
  (expected without a client key; service alive).
- nginx / xray / fail2ban / cron / docker units: active. (`sshd.service`
  reports inactive under that name on this host; the `ssh` unit is active
  and the live session itself rides it.)
- systemd: zero `qq-codex-bot` units or timers remain.
- Docker: 0 dangling images, build cache 0 B, no named volumes.
- Disk: 4.7G/40G (13%) used, down from 8.1G before this teardown (12G/32%
  at the 2026-07-10 maintenance).

## Local closeout

- Docs-only slice: `git diff --check` clean; no code, gate, or runtime files
  touched; real SSH integration tests not run.

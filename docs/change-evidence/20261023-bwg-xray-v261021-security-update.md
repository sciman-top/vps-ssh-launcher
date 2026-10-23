# BWG Xray v26.10.21 security update (2026-10-23)

## Scope

Authorized BWG-only kernel security update on the proxy plane. No CPA, OAuth
credential, lane, plugin, IP, or account change occurred. All OAuth accounts
and API key channels were retained unchanged.

## Background and trigger

- The weekly kernel wrapper `/etc/v2ray-agent/auto_update_xray.sh` failed at
  `2026-10-23T14:20:01Z` with `vasma SHA-256 mismatch; refusing unreviewed
  script` (exit 13), turning the strict doctor's `kernel-xray` heartbeat red.
- Root cause of the mismatch: `/etc/v2ray-agent/install.sh` had drifted from
  the pinned upstream `v3.5.25` @ `5c5e2b72` (sha `fca0ad30…`) to upstream
  `v3.5.26` content (sha `1d0d39d1…`, released upstream 2026-10-20). The most
  plausible writer is an interactive vasma self-update (menu `17.更新脚本`),
  which fetches upstream HEAD; no repository automation writes that path. The
  weekly v2ray-agent script updater detected the drift 20 minutes later and
  restored the pin (`APPLIED … version=v3.5.25`, backup
  `/var/backups/v2ray-agent-script-update.cKeuYa`), so the mismatch was
  already healed when this update ran.
- Separately, upstream Xray-core published stable `v26.10.21` (2026-10-21):
  an emergency security release removing three cracked Indian root CAs from
  the built-in RootCAs (v26.10.10/v26.10.17 were withdrawn) and built with
  Go 1.27.3. The host ran `v26.3.27` (June); every weekly run since then had
  been a skip-reinstall no-op, so this was the first wrapper-driven install
  path execution in months.

## Pins and supply chain

- Official asset `Xray-linux-64.zip` for `v26.10.21`:
  zip SHA-256 `0eec3ed4163c273d8c8bf821f7d219e257921cf584aa3fb970715e458e631978`
  (verified against the release `.dgst` manifest); inner `xray` binary SHA-256
  `a27e3f95ab6ce270956d0cecd32eda4686a811afc71c64a6a734a3dede14883e`.
- Method cross-check: the same procedure on `v26.3.27` reproduced the deployed
  binary hash `8255dd93…` exactly, validating the pin derivation.
- Wrapper projection (sanctioned path, `scripts/vasma_kernel_update_cron.ps1
  -Profile bwg -Kernel xray -Apply`): `TARGET_VERSION="v26.10.21"`,
  `EXPECTED_SHA256="a27e3f95…"`, `EXPECTED_VASMA_SHA256="fca0ad30…"`
  (vasma pin intentionally kept at upstream `v3.5.25` @ `5c5e2b72`).
- Local maintenance policy `%APPDATA%\vps-ssh-launcher\maintenance.toml`
  `[pins.xray]` updated to `26.10.21` / zip sha `0eec3ed4…` (that plane pins
  the zip; the wrapper pins the installed binary).

## First attempt failed by observer effect (lesson)

- Attempt 1 (`15:01:23Z`) exited 11 and auto-rolled back (`ROLLBACK_VERIFIED`,
  backup `/var/backups/v2ray-agent-core-update.SJFuV8`). vasma downloaded
  correctly, then aborted at `xray关闭失败`: after `systemctl stop`, its
  liveness check `pgrep -f "xray/xray"` matched the monitoring SSH session,
  whose single long-lived shell command line contained the literal string
  `/etc/v2ray-agent/xray/xray` (bundled verify section). vasma read that as
  "xray still running" and aborted; the wrapper restored `v26.3.27` and left
  the service active — the fail-closed chain worked as designed.
- The wrapper template documents the manual-trigger rule at lines 197–198:
  run the wrapper as the only remote command and verify in a second SSH
  command. This failure is a live confirmation that the rule is load-bearing:
  any concurrent ssh/cmdline carrying the literal `xray/xray` substring can
  poison vasma's pgrep check. Do not bundle monitoring with the trigger.

## Second attempt and verification

- Launched `15:08:12Z` detached as the only remote command; completed
  `15:08:16Z` (`result=success`, exit 0): stop → install → start →
  `PRUNE scope=core_backups policy=keep_8`.
- Post-verify (separate SSH command): `Xray 26.10.21 … (go1.27.3
  linux/amd64)`, binary sha equals pin, `systemctl is-active xray` = active,
  443 listener present, wrapper status file `result=success`.
- Fresh strict doctor: `DOCTOR_CONTRACT_OK`; all six maintenance heartbeats
  green, including `kernel-xray result=success`.

## Decision records

- vasma pin stays at upstream `v3.5.25` @ `5c5e2b72`. Adopting `v3.5.26` is
  deferred: it changes `RenewTLS` internals (`sslRenewalThresholdDays`,
  `readAcmeTLS` flow), which would require re-validating the hardened
  renewtls wrapper compatibility first; it is not needed for this security
  update. The weekly script updater will keep restoring the pin if upstream
  content appears on the host again.
- The v3.5.26 drift itself was a one-off write, not a recurring loop: the
  hardened renewtls wrapper renews via `acme.sh` directly and only uses
  `install.sh RenewTLS` as a backstop; `install.sh` has no self-update on
  that path.

## Rollback

Wrapper auto-backup `/var/backups/v2ray-agent-core-update.SJFuV8` holds the
pre-update `v26.3.27` binary and conf snapshot. Manual rollback: copy the
binary back to `/etc/v2ray-agent/xray/xray`, `systemctl restart xray`, then
re-verify version/sha/config. Future weekly runs keep the `v26.10.21` pins
and only act when upstream stable moves again. Git rollback does not restore
the remote kernel.

## Evidence boundary

`host_loaded` plus live service verification. Long-run proxy-plane stability
is observed through natural usage; no throughput or capacity claim is made.

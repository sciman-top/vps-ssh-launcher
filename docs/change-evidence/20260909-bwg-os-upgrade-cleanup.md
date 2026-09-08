# 2026-09-09 bwg OS package upgrade and light cleanup

## Scope

- Target: profile `bwg` only. Profile `zz` was not connected, checked, or
  modified.
- User-authorized maintenance window: `apt` package upgrade plus optional
  journal/apt-cache cleanup. No repo code, gateway config, Nginx/Xray config,
  credentials, or auth files were changed.
- The `/root/qq-codex-bot-backup-20260908-131720.tar.gz` teardown backup was
  explicitly excluded from cleanup and left untouched.

## Preflight (read-only probes)

- Ubuntu 24.04.4 LTS, kernel `6.8.0-107-generic`, uptime 69 days, no
  `reboot-required` flag; `unattended-upgrades` active (Ubuntu security
  patches already auto-applied).
- dpkg lock free (`unattended-upgrade-shutdown --wait-for-signal` resident
  helper only; no apt/dpkg process running).
- `nginx -t` passed before the change; single container `cli-proxy-api` Up.
- Disk 4.7G/40G used (13%), memory 518M/2048M used.

## Rollback base (pre-upgrade versions)

`docker-ce` 5:29.7.2, `docker-ce-cli` 5:29.7.2, `containerd.io` 2.3.4-1,
`docker-buildx-plugin` 0.36.1, `docker-ce-rootless-extras` 5:29.7.2,
`docker-compose-plugin` 5.5.0, `nginx` 1.31.4, `base-files` 13ubuntu10.4.
All previous versions remain available from their upstream apt repos, so
rollback is `apt-get install` of the pinned old versions. No conffile changes
occurred (`--force-confold` guard was set; no prompts were triggered), so no
config rollback is needed. No reboot was required (no kernel update).

## Applied changes

- `apt-get update` + `apt-get -y upgrade` (noninteractive, needrestart auto),
  10 packages upgraded, `upgrade_rc=0`:
  - docker-ce / docker-ce-cli / docker-ce-rootless-extras 5:29.7.2 -> 5:29.8.0
  - containerd.io 2.3.4-1 -> 2.3.5-1
  - docker-buildx-plugin 0.36.1 -> 0.37.0
  - docker-compose-plugin 5.5.0 -> 5.5.1
  - nginx 1.31.4 -> 1.31.5 (nginx.org mainline repo)
  - base-files 13ubuntu10.4 -> 13ubuntu10.5
  - ubuntu-release-upgrader-core / python3-distupgrade 1:24.04.28 -> 1:24.04.29
- 3 packages held back (phased updates, incl. `linux-firmware`): expected,
  left for a later cycle.
- Cleanup: `journalctl --vacuum-size=50M` (95.5M -> 45.5M) and `apt-get clean`
  (110M -> 28K). Disk after: 4.5G/40G (12%).

## Verification

- Services `nginx`, `docker`, `xray`: all `active` after upgrade; dockerd
  restarted at 17:05:48 UTC inside the upgrade; `systemctl --failed`: none.
- `nginx -t`: successful; listeners on `*:443` (Xray) and `0.0.0.0:8443`
  (Nginx public gateway) present again after restart.
- Container `cli-proxy-api`: `running`, `RestartCount=0`, back Up within ~34s
  of the dockerd restart (restart policy `unless-stopped`).
- Public TLS probe from the local machine: `https://fq.sciman.top:8443/` and
  `/v1/models` (bare paths, no prefix) -> HTTP 404, expected because the API
  is routed under its configured prefix only; TLS + Nginx chain confirmed up.
- Direct CPA probe on the host: `http://127.0.0.1:8317/v1/models` -> HTTP 401
  (unauthenticated), i.e. CPA itself is serving.
- No `reboot-required`; no kernel was installed.

## Outcome

- Maintenance complete within the single authorized window; public API and
  panel chain verified healthy after the only impactful step (docker daemon +
  nginx restarts, seconds-level blip).

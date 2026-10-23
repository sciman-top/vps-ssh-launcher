# ZZ vasma install.sh restore (2026-10-23)

## Scope

Authorized single-host restore of `/etc/v2ray-agent/install.sh` on zz after
interactive deletion. No config, credential, core binary, systemd unit, cron,
or service state change. The data plane (sing-box, nginx, TLS certs, acme.sh)
was verified untouched throughout.

## Incident timeline (UTC)

- `01:30` nightly RenewTLS wrapper ran clean: cert issued `2026-10-13
  01:30:16`, 81 days remaining — install.sh still present.
- `14:20` weekly sing-box wrapper ran clean (`skip reinstall`), the only
  wrapper path that did not need install.sh this cycle.
- `15:21:51` interactive root SSH session opened from the user's own ISP IP.
- `15:22:02` journal recorded `sudo chmod 700 /etc/v2ray-agent/install.sh`
  (TTY pts/0); the `/etc/v2ray-agent` directory mtime changed the same second
  (`15:22:02.108`) — the deletion moment. `.wget-hsts` updated the same
  minute (a wget reached an HSTS host; `/root/install.sh` is absent now).
- `15:24:54` user reconnected; bare `vasma` failed with
  `bash: /usr/bin/vasma: No such file or directory` — the dangling-symlink
  form (`/usr/bin/vasma -> /etc/v2ray-agent/install.sh`, link dated 2026-04-16,
  target gone), not a missing PATH entry.
- Root cause classification: user's own interactive session; every SSH login
  that day came from one ISP IP (user-side and probe connections share the
  egress). Not an intrusion. Intent of the 15:22 commands (history shows a
  fork-installer wget chain and the chmod; the deletion itself left no
  command trace) remains unconfirmed by the user.

## Lane distinction (why the bwg updater was NOT used)

- zz's projected sing-box weekly wrapper pins
  `EXPECTED_VASMA_SHA256="15551f809c54e95314cbb07e7b8ca38c4668d3feb9439cee5167573134223976"`
  — the sciman fork checkout (`sciman-v2ray-agent/install.sh` at `b4d72b70`,
  the 2026-09-25 upstream sync).
- bwg's `v2ray-agent-script-update.sh` enforces upstream mack-a `v3.5.25`
  @ `5c5e2b72` (sha `fca0ad30…`). Applying it to zz would have satisfied
  nothing and broken the weekly pin. zz has no script-updater cron; its
  install.sh lineage is the manually installed fork file.

## Restore (authorized remote write)

- Transport: shared `scripts/lib/project_environment.ps1`
  `Invoke-LauncherRemoteCommand` (base64 payload, chunked mode-600 remote
  temp file). One-off driver in `%TEMP%`, not committed.
- Sequence: local sha256 pre-check (must equal `15551f80…`, 402033 bytes) →
  upload base64 → remote decode to `/etc/v2ray-agent/install.sh.new` →
  remote sha re-verify (mismatch aborts with cleanup, exit 11) →
  `chmod 700` → atomic `mv` over the missing path → temp cleanup.
- Rollback: `rm -f /etc/v2ray-agent/install.sh` returns to the pre-restore
  state (dangling symlink); nothing else was written.

## Verification

- `RESTORED sha256=15551f80…` — equals the local checkout and the weekly
  wrapper pin; mode `-rwx------`, symlink resolves.
- `test -x /usr/bin/vasma` → EXEC_OK; `bash -n install.sh` → SYNTAX_OK;
  `coreVersionManageMenu` anchor present.
- RenewTLS dry run (`/bin/bash /etc/v2ray-agent/install.sh RenewTLS`), the
  exact nightly 01:30 path: rc=0, check date 2026-10-23, cert generated
  2026-10-13, 80 days remaining, `证书有效`.
- Weekly wrapper pin self-consistency: live `sha256sum` ==
  `EXPECTED_VASMA_SHA256`, so the next Friday run will pass its pin gate
  instead of refusing fail-closed.

## Follow-ups

- If the user's 15:22 intent was a version switch (upstream master or fork
  update), that is a separate engineering change (wrapper pin + projection
  updates) and must not ride along with restores.
- zz install.sh has no automated pin enforcement between weekly wrappers;
  accepted as-is (R5) — the weekly wrapper itself is the drift detector.

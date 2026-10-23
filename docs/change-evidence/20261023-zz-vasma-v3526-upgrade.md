# ZZ vasma upgrade to v3.5.26 fork sync (2026-10-23)

Authorized follow-up to `20261023-zz-vasma-install-sh-restore.md`: the user
confirmed the 15:22 deletion was an attempted vasma upgrade, so the upgrade
was executed end-to-end. zz only; bwg untouched (its upstream-lane v3.5.25
pin and updater behavior are a separate decision).

## Fork sync (sciman-v2ray-agent checkout)

- Upstream moved `5c5e2b72` (v3.5.25) -> `d892640` (tag `v3.5.26`,
  2026-10-20): TLS renewal rewritten to an expiry-window threshold
  (`sslRenewalThresholdDays=7`), duplicate-ACME-install fix, Reality Vision
  xudp, CentOS 8 compatibility.
- Compatibility evidence gathered BEFORE merging: `RenewTLS` cron dispatch
  intact (`cronFunction` -> `renewalTLS` -> exit 0); all sing-box weekly
  wrapper anchors (`16.core管理`, `coreVersionManageMenu`,
  `singBoxVersionManageMenu`, `1.升级 sing-box`, update prompts) present in
  upstream v3.5.26 with identical counts; fork patches and the upstream delta
  overlap only in `menu()`; fork never modified `renewalTLS`.
- Merge `f39b25d`: conflicts limited to the menu() branding block in
  `install.sh` and `shell/install_en.sh`, resolved per UPSTREAM-SYNC.md
  (fork attribution + upstream version number). Post-merge proof:
  `git diff upstream/master -- install.sh` = 52/49-line delta, the exact
  shape of the pre-merge fork patch set; anchors and 12 fork-URL references
  intact; `bash -n` clean. CHANGELOG entry `cc809ce`.
- Upstream install.sh content sha cross-check: `1d0d39d1…` matches the sha
  bwg's 10/23 drift incident recorded for v3.5.26.

## CRLF incident during deploy (root-caused and fixed)

- First projection of the merged file landed as
  `9a53cad0…` (414310 bytes) and `install.sh RenewTLS` failed remotely with
  rc=2: `line 6: $'\r': command not found`.
- Root cause: fork repo has no `.gitattributes` and `core.autocrlf=true`
  (repo-local and global), so the merge checkout rewrote the working-tree
  `install.sh` to CRLF. The pre-merge working tree predated that conversion
  (LF, `15551f80`), which is why the earlier restore worked. The raw-byte
  file projection path (unlike the wrapper transport, which normalizes
  CRLF) sent the bytes as-is. Local Git Bash `bash -n` tolerated CR, so the
  pre-flight syntax check did not catch it; remote Linux bash rejects it.
- Fix: fork commit `873ed90` pins `*.sh text eol=lf`; working tree
  re-checked out to LF; redeployed sha `3d4ee60a…` (403806 bytes) with a
  remote CR-free assertion on the candidate before the atomic swap.
- Deploy guards used in both passes: pre-state sha assertion (mismatch
  aborts), remote sha re-verify before swap, backups under
  `/var/backups/vps-launcher-vasma/` (`install.sh.pre-v3526` = v3.5.25 fork,
  `install.sh.crlf-broken` = the CRLF intermediate).

## Wrapper pin re-projection

- `scripts/vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box
  -Version 1.14.3 -InstalledSha256 768d31fa… -VasmaSha256 3d4ee60a… -Apply`
  (read-only probe first: `anchors:present`). sing-box pins unchanged;
  `EXPECTED_VASMA_SHA256` moved `15551f80…` -> `3d4ee60a…` in lockstep with
  the deployed file, so the Friday 14:20 UTC pin gate stays self-consistent.
- Post-apply ERROR/WARN lines in the projection output were all wrapper
  source-scan content (`log "ERROR: …"` lines), zero real failures.

## Verification (UTC, ~16:00)

- `bash -n install.sh` OK; `test -x /usr/bin/vasma` OK; menu anchors in.
- `install.sh RenewTLS` dry run (the nightly 01:30 path): **rc=0**, 80 days
  remaining, `证书有效` — the v3.5.26 renewal logic works against zz's
  acme.sh state (this was the pre-existing adoption blocker for the
  upstream lane, now validated in the fork variant).
- Wrapper: TARGET_VERSION `v1.14.3`, EXPECTED_SHA256 `768d31fa…`,
  EXPECTED_VASMA_SHA256 `3d4ee60a…`; `bash -n` clean; cron.d
  `vps-launcher-kernel-update` unchanged (`20 14 * * 5`).
- Data plane untouched: sing-box and nginx active, sing-box process uptime
  continuous across the whole operation.
- Rollback: restore `/var/backups/vps-launcher-vasma/install.sh.pre-v3526`
  (v3.5.25 fork, LF) and re-project the wrapper with
  `-VasmaSha256 15551f80…`.

## Open item: fork repository unreachable

`git fetch/push origin` to `https://github.com/sciman-top/v2ray-agent.git`
fails with "Repository not found" (it was fetchable via wget in April).
The v3.5.26 fork sync therefore lives only in the local checkout and zz's
deployed file until the repo is renamed/re-created/made accessible. Until
then, the user's historical wget-installer chain would fetch the stale
v3.5.25 fork file — do not treat that file as an upgrade.

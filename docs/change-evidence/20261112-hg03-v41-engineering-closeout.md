# 2026-11-12 HG-03 v4.1 evidence-contract repair

## Scope

This receipt covers repository evidence-contract changes, the v4.1 handoff
package, and HOME-local read-only probes. It does not claim BWG/ECS deployment,
office wired acceptance, home remote access or a long-term provider SLA.

## Intent and authority

The user confirmed that the current Windows host is HOME and the other is
OFFICE, and authorized continuous repair and optimization within the established
approval, single-host and live-acceptance boundaries. The user also identified
the separate always-on Windows home host as ONOFFICE. Its observed identity,
role, network and readiness remain unverified. The recommended route is
to fix existing evidence and reuse existing D/E access before adding a new
network component or control plane. No provider credential, account, cooldown,
host key, traffic limit or remote firewall was altered.

## Changes and evidence

- Added `scripts/hg03_contract.py`: schema-bound, redaction-first evidence
  with private atomic output, endpoint capability stripping, request-ID checks,
  access-layer gates and one-shot CPA observation. A local readiness snapshot
  contains only a protected host/port reference and no business check; its
  record stays `UNVERIFIED`. It is a local checker; it does not imply live
  acceptance.
- Added `tests/test_hg03_contract.py`, covering secret-safe failure, explicit
  hotspot confirmation, real-business checks, protocol terminal and request
  consistency, a downloaded Git object, and private output. A readiness
  observation is kept `UNVERIFIED`; a controlled one-shot probe remains
  `UNVERIFIED` until its fresh server-side request record is attached.
- The existing Cockpit provider report now abbreviates key IDs and continues to
  remove the protected gateway path. A binding ID derived from local key
  material is intentionally not printed in this receipt.
- Reworked `HG03_Final_v4/scripts/office-net-audit.ps1` to emit compact structured
  fields rather than CIM object graphs. It emits `UNVERIFIED` when a declared
  interface mode lacks observed support. An active split-default tunnel also
  prevents a `WIRED_ONLY`/baseline observation from passing. PowerShell 7 and
  5.1 local runs passed syntax/execution; the local host currently has WLAN as
  its default route, so it is not office wired evidence.
- Extracted TCP probe fields under PowerShell 7 and 5.1. The fields are now
  strings instead of serialized objects; local loopback proves only fixture
  shape, not remote network success.
- Extended `ecs-audit.sh` as a conservative read-only snapshot, including host
  memory/swap limits and service/container cgroup accounting. `bash -n` passed
  in both the local Bash and WSL Ubuntu 24.04. No real ECS connection was made.
- Added `docs/runbooks/hg03-integration.md` as the v4.1 contract and route/rollback
  source. C application is blocked until compliance/authorization and an actual
  approved endpoint gap are evidenced.
- Updated package wording, template versions, claims, ECS cleanup/recovery and
  docs CI paths. The package SHA256 manifest was regenerated after all file
  changes.

Local evidence paths (private, not distribution artifacts):

- A prior private HOME audit under `%TEMP%\hg03-home-b0-20261112\` returned
  `EVIDENCE_STATUS=UNVERIFIED`, because the declared `WIRED_ONLY` was not met.
- The local loopback and PowerShell 5.1 fixture runs are temporary private
  files under `%TEMP%\hg03-fixed-*\`; they are not evidence of a remote host.
- HOME endpoint readiness: a private `%TEMP%\hg03-readiness-*\readiness.json`
  — `UNVERIFIED`, with no request ID and no CPA request. The protected endpoint
  reference is reduced to host/port plus `/<protected>`.

These local files establish only probe shape/status, not production health.

## Unverified boundaries

- HOME WAN/IPv6/CGNAT, router access, and real-home CPA behavior remain NOT_RUN.
  The local endpoint pointer is readable, but it is not evidence of a successful
  generation, upstream account health or a stable public path.
- BWG/ECS live state, frozen-route snapshots, disk recovery drill and profile
  identity remain NOT_RUN unless a separately approved read-only integration
  probe is made.
- Office wired WEB/GIT/CPA/GLM/intranet testing remains OFFICE-owned and
  requires on-site hotspot disconnection. No remote browser substitutes for it.
- Provider account availability, rate-limit behavior and continuing network
  quality require controlled/natural evidence, not a schema pass.

## Rollback

Restore the listed source, tests, docs and package files to their pre-repair
versions. The package is currently untracked, so this source slice has no
prior v4 commit to revert. Preserve the user's unrelated dirty worktree. Local
evidence is outside Git and private. No remote state was modified, so no remote
rollback is required.

The `cockpit_provider_health.py` repair is independent of the HG-03 package.
Roll back only that file and its test addition if the display change is not
desired; it does not alter provider configuration or credentials.

The package's private `SHA256SUMS.txt` is regenerated after package edits.
Committing it would change those bytes and leave the manifest stale, so it stays
a local handoff artifact and is not included in source commits.

## Verification record

The final repository gate ran `1337 passed, 21 skipped, 671 subtests passed`;
the skips were the documented real-SSH opt-in and unavailable symlink/flock
fixtures. Bandit, Ruff check/format and Mypy then passed. PowerShell 7 and 5.1
local probe runs returned `UNVERIFIED`; Bash and WSL Ubuntu 24.04 syntax checks
passed. The package manifest has nine matching payload entries. These are local
engineering checks only. No office wired request, real ECS/BWG snapshot, real
home CPA generation or provider/account acceptance was performed.

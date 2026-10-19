# BWG maintenance hardening — 2026-10-20

## Scope and authority

User requested continuous repair of the five findings from the full-chain
maintenance audit. Scope is BWG only. No credentials, provider configuration,
public route, CPA image, or native plugin binary are changed by this transaction.

## Repository changes

- Missing required resources block the maintenance plan instead of returning noop.
- Monthly APT index refresh uses `APT::Update::Error-Mode=any`; a partial source
  failure stops the package mutation phase.
- CPA host updates defer with exit 77 when native plugins are enabled. Joint
  host/plugin ABI validation remains a controlled canary operation. No-candidate
  daily readiness remains non-consuming.
- Admission holds a separate shared flock descriptor for the complete request.
  The updater takes the exclusive lock before replacing the image and holds it
  through verification or rollback. Health remains observable during the lock.
- Docker client calls are bounded (pull 600s, Compose 180s, other calls 30s;
  forced termination grace 15s). INT/TERM preserve exit 130/143.
- `scripts/remote/cpa-update-timeout.conf` is the source for the updater's
  systemd drop-in: start budget 20min, stop/rollback budget 4min.

## Staging and rollback preparation

The shared `Invoke-LauncherRemoteCommand` transport staged normalized LF sources
at `/root/vps-maint-hardening-stage-20261019`. Production target hashes matched
the reviewed Git baseline before staging. The installed native plugin was not
downloaded, replaced, or invoked.

Remote Linux fixture receipts:

```text
BASELINE_MATCH=PASS
NATIVE_FLOCK_HTTP_FIXTURE=PASS
PLUGIN_DEFER_FIXTURE=PASS
CONTROLLED_SIMULATION=PASS no_real_credentials=true no_provider_requests=true
ROLLBACK_DRY_RUN=PASS
```

The fixture uses a disposable loopback HTTP upstream. An exclusive maintenance
lock refuses a request with 503 and leaves health readable; release restores
normal forwarding. An existing shared request lock excludes the updater.

Backup: `/root/vps-maint-hardening-backup-va6dvt58` (0700). The backup contains
the old source files and reviewed manifest. It excludes credentials and the
live authentication directory. The staged transaction refuses baseline drift
and active requests/sockets; the CPA container generation, current configuration,
and plugin hash are checked for preservation.

## Verification and acceptance boundary

- Final `pwsh -NoProfile -File scripts/run_gates.ps1` exited 0: 696 passed,
  327 subtests passed, 1 real SSH integration skipped by default. Build,
  Bandit, Ruff check/format, and mypy all passed. No dependency changes.
- Linux staging and native flock/HTTP simulation passed.
- Production activation is pending an explicit current maintenance window for
  the one admission process restart. The old generation has no request lock,
  so the initial migration additionally requires 30 seconds of idle sampling.
- No production code replacement, CPA restart, software upgrade, provider
  generation probe, or natural client acceptance is claimed by this staging receipt.

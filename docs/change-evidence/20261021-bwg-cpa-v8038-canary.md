# BWG CPA v8.0.38 canary upgrade (2026-10-21)

**Scope**: `bwg` only. `zz` was not connected or modified. No OAuth credential, IP, UA, concurrency, or retry-policy changes.

## Evidence

- Official release: CLIProxyAPI `v8.0.38`, published 2026-10-21; release notes cover Codex tool-call parameter fidelity, Claude/Responses cached namespace resolution, per-stream tool metadata caching, and Antigravity tool replay continuity.
- Docker Hub amd64 manifest: `sha256:113a70240fb48f5c6fd674c9de40b1af4e7ff629f7acc6a412c6014ab1922daf`.
- Preflight: running `v8.0.37` digest `sha256:6e36e6aee64a76fc8832b11e127072824dedce8c7eb0d295ee315bf4fdf31071`; admission idle; non-OAuth generation `HEALTH_OK`.
- Transaction: `outputs/deploy-cpa-v8038-canary-20261021.sh`; backup `/opt/cliproxyapi/backups/20261021T151951.384613308Z-from-v8.0.37-canary-v8.0.38`.
- Post-write: container `v8.0.38` running; readiness `HEALTH_OK`; non-OAuth generation `HEALTH_OK`; admission inflight/pending both zero.
- Cache observation: existing zhipu LCP cache miss followed by cache hit; no cache-policy change was introduced.
- Plugin: WorkBuddy remains existing local `0.9.62` build from upstream `v0.8.76`; latest upstream plugin release was unchanged, so no plugin replacement was performed.

## Risk boundary

Antigravity and WorkBuddy existing strict backoff states were preserved. ChatGPT OAuth remains under the existing `unverified_request` operator-review lock. No OAuth probe, lock clearing, credential rotation, or concurrency increase was performed.

## Verification

- `pytest -q tests/test_cpa_updater_apply.py tests/test_cpa_guardrails_script.py`: 56 passed, 60 subtests passed.
- `git diff --check`: pass.
- Strict BWG doctor reached all syntax/health/inventory checks but returned `DOCTOR_CONTRACT_FAILED` on the pre-existing ChatGPT OAuth `lane-hold=HELD reason=unverified_request`; this is an intentional safety stop, not cleared by this upgrade.

## Rollback

Restore the backed-up compose file and run `docker compose up -d --pull never` from `/opt/cliproxyapi`; the canary script's rollback path is digest-pinned and preserves the v8.0.37 image.

# BWG CPA updater cross-minor policy

- Scope: updater policy only, projected to the `bwg` profile. `zz` was not connected or modified.
- Repository change: commit `d067157` changes candidate selection to allow any newer minor within the current major line; major upgrades remain excluded and require an explicit canary.
- Eligibility remains unchanged: official GitHub release and Docker Hub tag, non-draft/non-prerelease, digest pinned, and both timestamps past the 72-hour maturity window.

## Practical acceptance

- Full local gate: `128 passed, 1 skipped, 88 subtests passed`; Bandit, Ruff, format, and Mypy passed.
- Projected updater SHA-256 on BWG: `79ae7a89568def147931311ef4f1b7c1ab101a02939353e0b1125268e91bd434`.
- Live `bash /opt/cliproxyapi/auto-update.sh --check`: `current=v7.3.4 target=v7.3.4`, `BACKUP_HEALTH status=ok`.
- Final strict doctor: `DOCTOR_CONTRACT_OK`; v7.3.4 container remained running with the previously verified digest and all gateway contracts intact.
- Cross-minor selection was exercised in the isolated selector fixture: a mature `v7.3.0` candidate is selected from hypothetical `v7.2.x`, while `v8.0.0` is excluded from a v7 current line.

No production image change was needed for this policy projection because BWG was already on v7.3.4, the current highest stable v7.3 release at acceptance time.

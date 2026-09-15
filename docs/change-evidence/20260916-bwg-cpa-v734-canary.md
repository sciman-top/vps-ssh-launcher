# BWG CPA v7.3.4 canary upgrade

- Scope: only the `bwg` profile; `zz` was not connected or modified. No SSH tunnel was used.
- Authorization: explicit user authorization received on 2026-09-16.
- Previous image: `eceasy/cli-proxy-api:v7.2.158@sha256:58178ab00cd1e54aa8520c35a62feca30b8ec2a227facb04b0fd765b96df0691`.
- Target image: `eceasy/cli-proxy-api:v7.3.4@sha256:97825da3009f98acf78b5c172fde650a5fbe7a690950a69ce6d7b535d77d4266`.
- Official metadata: [CLIProxyAPI v7.3.4 release](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.4) and [Docker Hub tag metadata](https://hub.docker.com/v2/repositories/eceasy/cli-proxy-api/tags/v7.3.4).
- v7.3.4 was newer than the updater's normal 72-hour soak window; this was an explicit canary, not an automatic updater promotion.

## Transaction and rollback

- Backup: `/opt/cliproxyapi/backups/20260915T164421Z-from-v7.2.158-canary-v7.3.4`.
- The canary backed up `config.yaml`, `compose.yml`, `cpa-health.py`, `auto-update.sh`, and top-level auth credentials before changing Compose.
- Compose was changed atomically, the pinned image was pulled, and `docker compose up -d --pull never` recreated the CPA container.
- Rollback was not required. The backup remains available for a scoped restore of the pre-canary Compose and service.

## Verification

- Target image loaded: running container and `RepoDigests` both matched the v7.3.4 digest.
- CPA readiness: `HEALTH_OK`.
- Explicit five-route `generation-all`: `HEALTH_OK`.
- Final strict BWG doctor: `DOCTOR_CONTRACT_OK`.
- CPA listener: `127.0.0.1:8317`; Nginx public listener: `0.0.0.0:8443`.
- Exact Docker port allowlist, random path, Nginx merged route, safe access log, Fail2ban, permissions, management isolation, and identity-confuse checks all passed.
- Public route semantics remained `401` for unauthenticated valid path and `404` for bare/wrong paths.
- `auto-update.sh --check`: `current=v7.3.4 target=v7.3.4`, backup health `ok`.

Residual upstream `503/502` history remains an availability observation, not a local contract failure; no provider behavior was changed as part of this canary.

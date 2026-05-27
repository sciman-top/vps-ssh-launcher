# 2026-05-20 zz sub2api low-memory deployment

## Scope

- Target profile: `zz`
- Risk level: medium, installed Docker and started new containers on a live VPS
- `bwg` status: not touched
- Real SSH triggered: yes
- Secrets: no passwords, tokens, private keys, account exports, or API keys recorded

## Goal

Deploy sub2api on `zz` using a conservative personal-use profile aligned with
the Cockpit hardened API direction:

- bind to VPS localhost only
- keep low memory and low connection-pool limits
- avoid public exposure by default
- avoid high-frequency account probing or request-level random routing
- keep credentials root-only on the VPS
- preserve the existing `sing-box` / `nginx` proxy services

## Pre-Install Snapshot

Created a root-only remote snapshot before installing Docker:

```text
/root/sub2api-preinstall-20260520T044911Z
```

Snapshot contents include host metadata, service states, ports, disk/memory,
iptables, root crontab, nginx configs, and sing-box config if present.

## Commands

Preflight and snapshot:

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<create /root/sub2api-preinstall-* snapshot>" --command-timeout 120
```

Docker and Compose install:

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<apt-get update; apt-get install docker.io docker-compose-v2; enable docker; verify services>" --command-timeout 900
```

Deployment config:

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<write /opt/sub2api/.env and docker-compose.yml; docker compose config --quiet>" --command-timeout 180
```

Startup:

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<docker compose pull; docker compose up -d>" --command-timeout 1800
```

Final verification:

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<health, admin login probe, bindary, services, sing-box check, stats, failed units, docker logging>" --command-timeout 240
```

## Remote Files

```text
/opt/sub2api/.env
/opt/sub2api/docker-compose.yml
/opt/sub2api/admin-credentials.txt
/opt/sub2api/data/
/opt/sub2api/postgres_data/
/opt/sub2api/redis_data/
/opt/sub2api/backups/docker-compose.yml.20260520T143615Z.bak
```

Permissions:

```text
/opt/sub2api: 700
/opt/sub2api/.env: 600
/opt/sub2api/docker-compose.yml: 600
/opt/sub2api/admin-credentials.txt: 600
```

Admin credentials are stored only on the VPS:

```text
/opt/sub2api/admin-credentials.txt
```

## Configuration Summary

Network:

```text
BIND_HOST=127.0.0.1
SERVER_PORT=8080
sub2api port mapping: 127.0.0.1:8080->8080/tcp
public 8080 probe: closed_or_filtered
```

Runtime:

```text
RUN_MODE=simple
SERVER_MODE=release
TZ=Asia/Shanghai
```

Low-memory limits:

```text
sub2api mem_limit: 384m
postgres mem_limit: 256m
redis mem_limit: 128m
DATABASE_MAX_OPEN_CONNS=8
DATABASE_MAX_IDLE_CONNS=2
REDIS_POOL_SIZE=16
REDIS_MIN_IDLE_CONNS=2
Redis maxmemory=96mb
PostgreSQL max_connections=40
PostgreSQL shared_buffers=96MB
```

Request/log guardrails:

```text
SERVER_MAX_REQUEST_BODY_SIZE=33554432
GATEWAY_MAX_BODY_SIZE=33554432
SERVER_H2C_MAX_CONCURRENT_STREAMS=4
GATEWAY_IMAGE_CONCURRENCY_MAX_CONCURRENT_REQUESTS=1
Docker json-file logs: max-size=10m, max-file=3
Sub2API file logs: max size 20MB, max backups 3, max age 7 days
```

## Key Output

Docker install:

```text
docker_version=Docker version 29.1.3, build 29.1.3-0ubuntu3~24.04.2
compose_version=Docker Compose version 2.40.3+ds1-0ubuntu1~24.04.1
docker_active=active
sing-box=active
nginx=active
xray=inactive
install_log=/root/sub2api-docker-install-20260520T130434Z.log
```

Compose startup:

```text
sub2api            Up (health: starting)   127.0.0.1:8080->8080/tcp
sub2api-postgres   Up (healthy)            5432/tcp
sub2api-redis      Up (healthy)            6379/tcp
compose_log=/root/sub2api-compose-up-20260520T131229Z.log
```

Final health:

```text
probe=01 health=healthy http=200
health={"status":"ok"}
sub2api            Up (healthy)   127.0.0.1:8080->8080/tcp
sub2api-postgres   Up (healthy)   5432/tcp
sub2api-redis      Up (healthy)   6379/tcp
```

Admin login API probe:

```text
code=200 has_token=true has_user=true
```

Access boundary:

```text
LISTEN 127.0.0.1:8080 users:(("docker-proxy",...))
public_8080=closed_or_filtered
```

Existing services:

```text
docker=active
sing-box=active
nginx=active
xray=inactive
sing_box_check rc=0
0 loaded failed units listed
```

Resource use after final recreate:

```text
sub2api            29.93MiB / 384MiB
sub2api-postgres   29.65MiB / 256MiB
sub2api-redis      3.648MiB / 128MiB
Mem: 961Mi total, 484Mi available
Swap: 1.0Gi total, 36Mi used
/dev/vda1 ext4 20G total, 14G available
```

Docker log rotation:

```text
/sub2api json-file {"max-file":"3","max-size":"10m"}
/sub2api-postgres json-file {"max-file":"3","max-size":"10m"}
/sub2api-redis json-file {"max-file":"3","max-size":"10m"}
```

## Access

The service is intentionally not public. Access it through an SSH local tunnel,
for example:

```powershell
ssh -p 31854 -L 18080:127.0.0.1:8080 root@38.244.39.84
```

Then open:

```text
http://127.0.0.1:18080
```

Read the admin password on the VPS only:

```bash
cat /opt/sub2api/admin-credentials.txt
```

## Account-Pool Boundary

No ChatGPT accounts were imported in this deployment because no account export
or token bundle was provided in this task.

Recommended rollout for hundreds of free accounts:

1. Start with one account and verify normal routing.
2. Add 2-3 accounts and verify sticky/fill-first behavior.
3. Expand in batches only after cooldown and health-state behavior is observed.
4. Avoid request-level random rotation, high-frequency probing, and whole-pool
   fallback.
5. Keep logs metadata-only and do not record prompts, responses, tokens,
   cookies, Authorization headers, or full account identifiers.

## Result

Deployment completed successfully.

`zz` now runs a private, low-memory sub2api stack under `/opt/sub2api` with
PostgreSQL and Redis. The app health endpoint returns `200`, admin login returns
a token-bearing response, the service is bound to `127.0.0.1:8080` only, public
port `8080` is not open, Docker logs have size rotation, and the existing
`sing-box` / `nginx` services remain healthy.

## Rollback

Stop only sub2api containers:

```bash
cd /opt/sub2api
docker compose down
```

Restore the compose file backup if needed:

```bash
cd /opt/sub2api
cp -a backups/docker-compose.yml.20260520T143615Z.bak docker-compose.yml
docker compose up -d
```

Remove the deployment data only after an explicit destructive confirmation:

```bash
cd /opt/sub2api
docker compose down
tar -C /opt -czf /root/sub2api-before-remove-$(date -u +%Y%m%dT%H%M%SZ).tar.gz sub2api
rm -rf /opt/sub2api
```

Docker package removal is separate and should only be done if no other Docker
services are expected on the host:

```bash
apt-get remove -y docker.io docker-compose-v2 containerd runc
```

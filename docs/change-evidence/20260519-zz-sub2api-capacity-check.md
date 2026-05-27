# 2026-05-19 zz sub2api capacity check

## Scope

- Target profile: `zz`
- Risk level: low, read-only SSH probes only
- `bwg` status: not touched
- Real SSH triggered: yes
- Secrets: no credentials, private keys, tokens, account exports, or target config content recorded

## Goal

Check whether `zz` is a reasonable host for a personal sub2api relay before
installing Docker or writing remote configuration.

The intended deployment profile is conservative self-use: low concurrency,
sticky or fill-first account selection, cooldown-aware routing, and metadata-only
logs. It is not intended for public exposure, request-level random rotation, or
high-frequency probing of a large ChatGPT free-account pool.

## Commands

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz check
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<identity, cpu, memory, disk, network, runtime, services, ports, limits, top>" --command-timeout 90
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<firewall, apt package candidates, existing sub2api, nginx sites>" --command-timeout 90
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<outbound deploy source probes, docker install dry-run, memory pressure>" --command-timeout 120
```

## Key Output

Connectivity:

```text
OK - root@38.244.39.84:31854
```

Host resources:

```text
host=C202604071640752
os=Ubuntu 24.04.4 LTS noble
kernel=Linux 6.8.0-107-generic x86_64 GNU/Linux
uptime=up 5 weeks, 1 day
load=0.17 0.06 0.01
CPU(s)=1
Model name=Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz
Mem: total 961Mi, used 355Mi, available 606Mi
Swap: total 1.0Gi, used 33Mi
/dev/vda1 ext4 20G total, 4.2G used, 16G available
```

Network and upstream reachability:

```text
public_ipv4=38.244.39.84
public_ipv6=
chatgpt_head=403 104.18.32.47 0.006258 0.132006
openai_api_head=401 162.159.140.245 0.005005 0.172860
```

Current runtime and ports:

```text
docker=missing
docker-compose=missing
python3=/usr/bin/python3
git=/usr/bin/git
curl=/usr/bin/curl
jq=/usr/bin/jq
nginx_active=active
sing-box_active=active
xray_active=inactive
ufw status: inactive
```

```text
udp *:443 sing-box
tcp 0.0.0.0:35622 nginx
tcp 0.0.0.0:31854 sshd
tcp 127.0.0.1:31300 nginx
tcp 127.0.0.1:31302 nginx
tcp *:22752 sing-box
```

Deploy source access and package availability:

```text
raw.githubusercontent.com docker-compose.local.yml: http=200
ghcr.io /v2/: http=401
registry-1.docker.io /v2/: http=401
auth.docker.io/token: http=200
docker.io candidate: 29.1.3-0ubuntu3~24.04.2
docker-compose-v2 candidate: 2.40.3+ds1-0ubuntu1~24.04.1
```

Sub2API local compose defaults observed from the upstream deploy files:

```text
sub2api image: weishaw/sub2api:latest
default host bind: BIND_HOST=0.0.0.0
default port: SERVER_PORT=8080
database: PostgreSQL
cache: Redis
default PostgreSQL shared buffers: 1GB
default PostgreSQL max connections: 1024
default Redis pool size: 1024
default gateway body size: 256MB
```

## Assessment

`zz` can run a small personal sub2api deployment, but it is not a comfortable
host for the upstream default local-compose settings.

Fit:

- CPU is acceptable for low-rate personal relay traffic.
- Disk is acceptable for app data, PostgreSQL, Redis, Docker images, and logs if
  log rotation and backups are capped.
- Public IPv4 and outbound HTTPS to GitHub, GHCR, Docker Hub, ChatGPT, and the
  OpenAI API are reachable.
- Existing ports leave `127.0.0.1:8080` or another local-only app port available.

Constraints:

- Memory is tight: total RAM is about 1GB and the host already runs sing-box,
  nginx, cloudflared, fail2ban, and system services.
- Docker, PostgreSQL, Redis, and sub2api will add steady memory pressure.
- The upstream default `.env.example` is sized for a much larger machine and
  should not be used as-is on this host.
- Installing Docker may modify networking/iptables and must be treated as a
  medium-risk change on a live proxy host.

## Recommended Deployment Profile

AI recommendation: proceed only with a low-memory, private, self-use profile.
Reason: it matches the Cockpit hardened API direction and avoids turning `zz`
into a public or high-churn account sweeper.

Suggested remote posture before install:

- Install Docker/Compose from Ubuntu packages only if the user approves the
  medium-risk live-host change.
- Place deployment under `/opt/sub2api`.
- Bind sub2api to `127.0.0.1` first, not `0.0.0.0`.
- Keep public exposure behind an explicit nginx vhost or tunnel only after local
  health checks pass.
- Reduce database and Redis pools aggressively for a 1GB VPS.
- Start with one account or a small pool, then expand in stages.
- Keep `maxRetryAccounts` at 1 initially; if fallback is needed, cap it at 2 and
  only before stream output starts.
- Use sticky or fill-first routing, not request-level random or round-robin.
- Disable high-frequency account probing, quota refresh, and full-pool scanning.
- Use metadata-only logs and cap log retention.

## Result

No remote files were changed. The host is suitable for a carefully tuned personal
sub2api proof-of-service, but not for upstream defaults or aggressive use of
hundreds of ChatGPT free accounts.

## Rollback

No rollback action is needed for this check because it was read-only.

If installation is approved later, create a pre-install snapshot or tarball for
the deployment directory and record the exact Docker, Compose, nginx, and
systemd changes in a separate evidence note.

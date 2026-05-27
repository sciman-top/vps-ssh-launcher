# zz sub2api uninstall evidence

- Date: 2026-05-25
- Target: `zz` profile, host `38.244.39.84`, SSH port `31854`
- Scope: remove only the previous `sub2api` deployment under `/opt/sub2api` and its Docker Compose artifacts.
- Risk: destructive remote cleanup requested by the user. No remote data backup was preserved because the requested action was to uninstall and clear the deployment.

## Precheck

- `/opt/sub2api` was present.
- Docker Compose project `sub2api` was running from `/opt/sub2api/docker-compose.yml`.
- Running containers before cleanup:
  - `sub2api` using `weishaw/sub2api:latest`
  - `sub2api-postgres` using `postgres:18-alpine`
  - `sub2api-redis` using `redis:8-alpine`
- Service was bound only to `127.0.0.1:8080`.
- `GET http://127.0.0.1:8080/health` returned HTTP 200 before cleanup.

## Commands

Local entrypoint:

```powershell
python .\ssh_tool.py --config $env:APPDATA\vps-ssh-launcher\target.json --profile zz run --command <remote-script> --command-timeout 180
```

Remote cleanup actions:

```sh
cd /opt/sub2api
docker compose down --volumes --remove-orphans
rm -rf /opt/sub2api
docker image rm weishaw/sub2api:latest postgres:18-alpine redis:8-alpine
```

Image removal was guarded by `docker ps -a --filter ancestor=<image>` and was only run when no remaining container referenced the image. A dangling anonymous Docker volume created on `2026-05-20T13:12:58Z` was also removed after verification showed there were no remaining containers, images, or `sub2api` references.

## Verification

Final remote verification at `2026-05-25T13:54:12+00:00`:

```text
dir_check:
OK_absent
docker_containers_check:
OK_none
docker_images_check:
OK_none
docker_volumes_check:
OK_none
docker_networks_check:
OK_none
systemd_check:
OK_none
port_8080_check:
OK_none
health_check:
OK_unreachable
docker_system_df:
Images          0         0         0B        0B
Containers      0         0         0B        0B
Local Volumes   1         0         0B        0B
Build Cache     0         0         0B        0B
```

Additional final verification after anonymous volume cleanup:

```text
final_dir:
OK_absent
final_containers:
OK_none
final_images:
OK_none
final_volumes:
final_networks:
OK_none
final_port_8080:
OK_none
final_health:
OK_unreachable
final_docker_system_df:
Images          0         0         0B        0B
Containers      0         0         0B        0B
Local Volumes   0         0         0B        0B
Build Cache     0         0         0B        0B
```

Local follow-up probes:

```text
python .\ssh_tool.py --config $env:APPDATA\vps-ssh-launcher\target.json --profile zz check
OK - root@38.244.39.84:31854

TCP connect probe to 38.244.39.84:8080 with 3s timeout:
TCP_8080_UNREACHABLE

git diff --check -- docs/change-evidence/20260525-zz-sub2api-uninstall.md
OK
```

## Rollback

There is no in-place restore after this cleanup. Restoring service requires a fresh `sub2api` redeploy and new configuration under `/opt/sub2api`.

"""Docker maintenance: pin schema, upgrade planning and guarded command."""

from __future__ import annotations

import shlex
from typing import Any

from .models import MaintenanceAction
from .pins import (
    normalize_compose_file,
    normalize_sha256,
    normalize_services,
    normalize_digests,
)


def normalize_pin(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("pins.docker must be a TOML table.")
    if set(value) != {"compose_file", "compose_sha256", "services", "digests"}:
        raise ValueError(
            "pins.docker requires exactly compose_file, compose_sha256, services and digests."
        )
    services = normalize_services(value.get("services"))
    digests = normalize_digests(value.get("digests"), services=services)
    return {
        "compose_file": normalize_compose_file(value.get("compose_file")),
        "compose_sha256": normalize_sha256(value.get("compose_sha256")),
        "services": services,
        "digests": digests,
    }


def plan_upgrade(
    profile: str, desired: str, observed: str, pin: dict[str, Any] | None
) -> MaintenanceAction:
    resource = "docker"
    if observed != "present":
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="blocked",
            reason="Docker must be present before a Compose reconciliation.",
        )
    if not pin:
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="blocked",
            reason="Docker upgrade requires an absolute Compose path, service allowlist and digest pins.",
        )
    return MaintenanceAction(
        profile=profile,
        resource=resource,
        desired=desired,
        observed=observed,
        status="planned",
        reason="Pinned non-CPA Compose services are ready for reconciliation.",
        target=str(pin["compose_file"]),
    )


def build_docker_upgrade_command(
    *,
    compose_file: str,
    compose_sha256: str,
    services: tuple[str, ...],
    digests: dict[str, str],
) -> str:
    compose_file = normalize_compose_file(compose_file)
    compose_sha256 = normalize_sha256(compose_sha256)
    services = normalize_services(list(services))
    digests = normalize_digests(digests, services=services)
    expected_services = " ".join(services)
    expected_pairs = " ".join(f"{service}|{digests[service]}" for service in services)
    return f"""set -Eeuo pipefail
compose_file={shlex.quote(compose_file)}
expected_compose_sha256={shlex.quote(compose_sha256)}
expected_services={shlex.quote(expected_services)}
expected_pairs={shlex.quote(expected_pairs)}
compose_project_dir=$(dirname -- "$compose_file")
exec 9>/run/vps-ssh-launcher-maintenance.lock
if ! flock -n 9; then
  echo MAINTENANCE_BUSY >&2
  exit 75
fi
backup_dir="$(mktemp -d /var/backups/vps-ssh-launcher-compose.XXXXXX)"
chmod 700 "$backup_dir"
backup_ready=0
mutation_started=0
old_image_pairs=""
old_existing_services=""
old_absent_services=""

rollback() {{
  rc="$?"
  trap - ERR INT TERM EXIT
  set +e
  if [ "$backup_ready" -ne 1 ] || [ "$mutation_started" -ne 1 ]; then
    echo APPLY_REFUSED_BEFORE_MUTATION >&2
    exit "$rc"
  fi
  if [ ! -f "$backup_dir/rollback-compose.yml" ]; then
    echo ROLLBACK_FAILED >&2
    exit "$rc"
  fi
  rollback_compose="$backup_dir/rollback-compose.yml"
  rollback_project_dir="$compose_project_dir"
  rollback_ok=1
  if [ -n "$old_existing_services" ]; then
    docker compose --project-directory "$rollback_project_dir" -f "$rollback_compose" up -d --pull never $old_existing_services >/dev/null 2>&1 || rollback_ok=0
  fi
  for service in $old_absent_services; do
    docker compose --project-directory "$compose_project_dir" -f "$compose_file" rm -sf "$service" >/dev/null 2>&1 || rollback_ok=0
  done
  for service in $old_existing_services; do
    container_id="$(docker compose --project-directory "$rollback_project_dir" -f "$rollback_compose" ps -q "$service" 2>/dev/null || true)"
    if [ -z "$container_id" ] || [ "$(docker inspect --format '{{{{.State.Status}}}}' "$container_id" 2>/dev/null || true)" != running ]; then
      rollback_ok=0
    fi
  done
  for service in $old_absent_services; do
    container_id="$(docker compose --project-directory "$compose_project_dir" -f "$compose_file" ps -aq "$service" 2>/dev/null)" || rollback_ok=0
    if [ -n "$container_id" ]; then
      rollback_ok=0
    fi
  done
  for pair in $old_image_pairs; do
    service="${{pair%%|*}}"
    old_image_id="${{pair#*|}}"
    container_id="$(docker compose --project-directory "$rollback_project_dir" -f "$rollback_compose" ps -q "$service" 2>/dev/null || true)"
    if [ -z "$container_id" ] || [ "$(docker inspect --format '{{{{.Image}}}}' "$container_id" 2>/dev/null || true)" != "$old_image_id" ]; then
      rollback_ok=0
    fi
  done
  if [ "$rollback_ok" -eq 1 ]; then
    echo ROLLBACK_VERIFIED
  else
    echo ROLLBACK_FAILED >&2
  fi
  exit "$rc"
}}
trap rollback ERR EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
test -f "$compose_file"
case "$compose_file" in
  *cliproxyapi*|*cli-proxy-api*) echo CPA_PATH_REFUSED >&2; exit 40 ;;
esac
cp -a "$compose_file" "$backup_dir/compose.yml"
actual_compose_sha256="$(sha256sum "$compose_file" | awk '{{print $1}}')"
if [ "$actual_compose_sha256" != "$expected_compose_sha256" ]; then
  echo COMPOSE_HASH_MISMATCH >&2
  exit 47
fi
services_output="$(docker compose -f "$compose_file" config --services)"
# Compose emits one service per line; token membership below needs spaces.
services_output="$(printf '%s\\n' "$services_output" | tr '\\n' ' ')"
images_output="$(docker compose -f "$compose_file" config --images)"
case "$images_output" in
  *cliproxyapi*|*cli-proxy-api*) echo CPA_IMAGE_REFUSED >&2; exit 41 ;;
esac
for service in $services_output; do
  case " $expected_services " in
    *" $service "*) : ;;
    *) echo SERVICE_NOT_ALLOWLISTED >&2; exit 42 ;;
  esac
done
for service in $expected_services; do
  case " $services_output " in
    *" $service "*) : ;;
    *) echo SERVICE_MISSING >&2; exit 43 ;;
  esac
done
for pair in $expected_pairs; do
  expected_digest="${{pair#*|}}"
  case "$images_output" in
    *"@$expected_digest"*) : ;;
    *) echo DIGEST_PIN_MISMATCH >&2; exit 44 ;;
  esac
done
for service in $expected_services; do
  # Stopped containers are existing state too. The rollback below restores
  # one running container per service; refuse states it cannot preserve.
  container_id="$(docker compose -f "$compose_file" ps -aq "$service")"
  if [ -n "$container_id" ]; then
    if [[ "$container_id" == *$'\\n'* ]] ||
       [ "$(docker inspect --format '{{{{.State.Status}}}}' "$container_id")" != running ]; then
      echo EXISTING_SERVICE_STATE_UNSUPPORTED >&2
      exit 52
    fi
    old_image_id="$(docker inspect --format '{{{{.Image}}}}' "$container_id" 2>/dev/null || true)"
    if [ -z "$old_image_id" ]; then
      echo OLD_IMAGE_READBACK_FAILED >&2
      exit 49
    fi
    old_image_pairs="$old_image_pairs $service|$old_image_id"
    old_existing_services="$old_existing_services $service"
  else
    old_absent_services="$old_absent_services $service"
  fi
done
command -v python3 >/dev/null 2>&1 || (echo PYTHON3_REQUIRED_FOR_ROLLBACK_SNAPSHOT >&2; exit 50)
docker compose -f "$compose_file" config --format json > "$backup_dir/rollback-compose.json"
python3 - "$backup_dir/rollback-compose.json" "$old_image_pairs" "$backup_dir/rollback-compose.yml" <<'PY'
import json
import re
import subprocess
import sys
from pathlib import Path

source = Path(sys.argv[1])
pairs_text = sys.argv[2].split()
destination = Path(sys.argv[3])
compose = json.loads(source.read_text())
for pair in pairs_text:
    service, image_id = pair.split("|", 1)
    image_ref = compose["services"][service]["image"]
    repository = image_ref.split("@", 1)[0]
    slash = repository.rfind("/")
    colon = repository.rfind(":")
    if colon > slash:
        repository = repository[:colon]
    output = subprocess.check_output(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{{{range .RepoDigests}}}}{{{{println .}}}}{{{{end}}}}",
            image_id,
        ],
        text=True,
    )
    old_refs = [
        ref
        for ref in output.splitlines()
        if re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{{64}}", ref)
    ]
    if not old_refs:
        raise SystemExit("ROLLBACK_OLD_IMAGE_DIGEST_UNAVAILABLE")
    compose["services"][service]["image"] = old_refs[0]
destination.write_text(json.dumps(compose, sort_keys=True) + "\\n")
destination.chmod(0o600)
PY
backup_ready=1
# Stage markers tell the operator where a truncated transaction stopped; the
# pull and up phases can each be silent for a long stretch.
echo ADAPTER_STAGE=pull
docker compose -f "$compose_file" pull $expected_services
echo ADAPTER_STAGE=up
mutation_started=1
docker compose -f "$compose_file" up -d --no-build --pull never $expected_services
echo ADAPTER_STAGE=verify
for service in $expected_services; do
  container_id="$(docker compose -f "$compose_file" ps -q "$service")"
  test -n "$container_id"
  test "$(docker inspect --format '{{{{.State.Status}}}}' "$container_id")" = running
  health="$(docker inspect --format '{{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{else}}}}none{{{{end}}}}' "$container_id")"
  case "$health" in
    healthy|none) : ;;
    *) echo HEALTH_NOT_READY >&2; exit 45 ;;
  esac
  expected_digest=""
  for pair in $expected_pairs; do
    if [ "${{pair%%|*}}" = "$service" ]; then
      expected_digest="${{pair#*|}}"
    fi
  done
  test -n "$expected_digest"
  image_id="$(docker inspect --format '{{{{.Image}}}}' "$container_id")"
  test -n "$image_id"
  repo_digests="$(docker image inspect --format '{{{{join .RepoDigests "\\n"}}}}' "$image_id")"
  case "$repo_digests" in
    *"@$expected_digest"*) : ;;
    *) echo DIGEST_READBACK_MISMATCH >&2; exit 46 ;;
  esac
done
trap - ERR INT TERM EXIT
while IFS= read -r old_backup; do
  [ "$old_backup" = "$backup_dir" ] && continue
  rm -rf -- "$old_backup" || echo BACKUP_PRUNE_FAILED >&2
done < <(
  find /var/backups -mindepth 1 -maxdepth 1 -type d \
    -name 'vps-ssh-launcher-compose.*' -printf '%T@ %p\n' |
    sort -rn | tail -n +9 | cut -d' ' -f2-
) || true
echo BACKUP_PRUNE scope=compose policy=keep_8
echo APPLY_VERIFIED
"""


def build_from_pin(pin: dict[str, Any]) -> str:
    return build_docker_upgrade_command(
        compose_file=str(pin["compose_file"]),
        compose_sha256=str(pin["compose_sha256"]),
        services=tuple(pin["services"]),
        digests=dict(pin["digests"]),
    )

"""Fail-closed remote adapters for explicitly pinned maintenance actions."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any, Callable

from .models import ActionStatus, MaintenanceAction

_VERSION_RE = re.compile(r"^(?:v)?([0-9]+\.[0-9]+\.[0-9]+)$")
_HEX_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CPA_MARKERS = ("cliproxyapi", "cli-proxy-api")

XRAY_BINARY = "/etc/v2ray-agent/xray/xray"
XRAY_CONFDIR = "/etc/v2ray-agent/xray/conf"
XRAY_SERVICE = "xray"


def normalize_version(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Xray version pin must be a string like 26.3.27.")
    match = _VERSION_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError("Xray version pin must be a semantic numeric version.")
    return match.group(1)


def normalize_sha256(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("SHA-256 pin must contain 64 hex characters.")
    normalized = value.strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.removeprefix("sha256:")
    if _HEX_SHA256_RE.fullmatch(normalized) is None:
        raise ValueError("SHA-256 pin must contain 64 hex characters.")
    return normalized


def normalize_digest(value: Any) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value.strip()) is None:
        raise ValueError(
            "Docker digest pin must be sha256: followed by 64 hex characters."
        )
    return value.strip().lower()


def normalize_compose_file(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Docker compose_file must be an absolute POSIX path.")
    path = value.strip()
    lowered = path.lower().replace("\\", "/")
    if (
        not path.startswith("/")
        or "\x00" in path
        or any(part == ".." for part in path.split("/"))
        or any(marker in lowered for marker in _CPA_MARKERS)
    ):
        raise ValueError(
            "Docker compose_file must be absolute and must not target CPA."
        )
    return path


def normalize_services(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("Docker services must be a non-empty array.")
    services: list[str] = []
    for service in value:
        if not isinstance(service, str) or _SERVICE_RE.fullmatch(service) is None:
            raise ValueError("Docker service names must be simple allowlisted tokens.")
        if any(marker in service.lower() for marker in _CPA_MARKERS):
            raise ValueError("The generic Docker adapter refuses CPA services.")
        services.append(service)
    if len(set(services)) != len(services):
        raise ValueError("Docker services must not contain duplicates.")
    return tuple(sorted(services))


def normalize_digests(value: Any, *, services: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Docker digests must be a service-to-digest table.")
    if set(value) != set(services):
        raise ValueError("Docker digests must cover exactly the allowlisted services.")
    return {service: normalize_digest(value[service]) for service in services}


def _quote(value: str) -> str:
    return shlex.quote(value)


def build_xray_upgrade_command(*, version: str, sha256: str) -> str:
    version = normalize_version(version)
    sha256 = normalize_sha256(sha256)
    archive_url = (
        "https://github.com/XTLS/Xray-core/releases/download/"
        f"v{version}/Xray-linux-64.zip"
    )
    return f"""set -Eeuo pipefail
version={_quote(version)}
archive_url={_quote(archive_url)}
expected_artifact_sha256={_quote(sha256)}
binary={_quote(XRAY_BINARY)}
confdir={_quote(XRAY_CONFDIR)}
arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) : ;;
  *) echo UNSUPPORTED_XRAY_ARCH >&2; exit 48 ;;
esac
exec 9>/run/vps-ssh-launcher-maintenance.lock
if ! flock -n 9; then
  echo MAINTENANCE_BUSY >&2
  exit 75
fi
backup_dir="$(mktemp -d /var/backups/vps-ssh-launcher-xray.XXXXXX)"
chmod 700 "$backup_dir"
tmp_dir="$(mktemp -d)"
backup_ready=0
service_restart_attempted=0

rollback() {{
  rc="$?"
  trap - ERR INT TERM EXIT
  set +e
  if [ "$backup_ready" -eq 1 ]; then
    if [ ! -f "$backup_dir/xray" ]; then
      echo ROLLBACK_FAILED >&2
      rm -rf "$tmp_dir"
      exit "$rc"
    fi
    if cmp -s "$binary" "$backup_dir/xray"; then
      if [ "$service_restart_attempted" -eq 1 ]; then
        if ! systemctl restart {XRAY_SERVICE} >/dev/null 2>&1; then
          echo ROLLBACK_FAILED >&2
          rm -rf "$tmp_dir"
          exit "$rc"
        fi
      else
        echo ROLLBACK_SKIPPED_BINARY_UNCHANGED
      fi
    else
      restore_tmp="$binary.rollback.$$"
      rm -f "$restore_tmp"
      if ! cp -a "$backup_dir/xray" "$restore_tmp" ||
         ! test -x "$restore_tmp" ||
         ! cmp -s "$backup_dir/xray" "$restore_tmp" ||
         ! mv -f "$restore_tmp" "$binary" ||
         ! test -x "$binary" ||
         ! cmp -s "$backup_dir/xray" "$binary"; then
        echo ROLLBACK_FAILED >&2
        rm -f "$restore_tmp"
        rm -rf "$tmp_dir"
        exit "$rc"
      fi
      if ! systemctl restart {XRAY_SERVICE} >/dev/null 2>&1; then
        echo ROLLBACK_FAILED >&2
        rm -rf "$tmp_dir"
        exit "$rc"
      fi
    fi
  fi
  if [ -x "$binary" ] && "$binary" run -test -confdir "$confdir" >/dev/null 2>&1 && systemctl is-active --quiet {XRAY_SERVICE}; then
    echo ROLLBACK_VERIFIED
  else
    echo ROLLBACK_FAILED >&2
  fi
  rm -rf "$tmp_dir"
  exit "$rc"
}}
trap rollback ERR INT TERM
cp -a "$binary" "$backup_dir/xray"
if [ ! -x "$backup_dir/xray" ] || ! cmp -s "$binary" "$backup_dir/xray"; then
  echo XRAY_BACKUP_VERIFY_FAILED >&2
  exit 51
fi
backup_ready=1
# Stage markers renew the launcher's idle timer across the silent download
# window (`curl --silent` prints nothing until it finishes) and tell the
# operator where a truncated transaction stopped.
echo ADAPTER_STAGE=download
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$archive_url" -o "$tmp_dir/xray.zip"
printf '%s  %s\\n' "$expected_artifact_sha256" "$tmp_dir/xray.zip" | sha256sum --check --status
echo ADAPTER_STAGE=install
unzip -oq "$tmp_dir/xray.zip" -d "$tmp_dir/extracted"
test -x "$tmp_dir/extracted/xray"
install -m 0755 "$tmp_dir/extracted/xray" "$binary.new"
mv -f "$binary.new" "$binary"
"$binary" version | awk '/^Xray / {{print $2; exit}}' | grep -Fx "$version" >/dev/null
"$binary" run -test -confdir "$confdir"
echo ADAPTER_STAGE=verify
service_restart_attempted=1
systemctl restart {XRAY_SERVICE}
systemctl is-active --quiet {XRAY_SERVICE}
"$binary" run -test -confdir "$confdir"
trap - ERR INT TERM
rm -rf "$tmp_dir"
while IFS= read -r old_backup; do
  [ "$old_backup" = "$backup_dir" ] && continue
  rm -rf -- "$old_backup" || echo BACKUP_PRUNE_FAILED >&2
done < <(
  find /var/backups -mindepth 1 -maxdepth 1 -type d \
    -name 'vps-ssh-launcher-xray.*' -printf '%T@ %p\n' |
    sort -rn | tail -n +9 | cut -d' ' -f2-
) || true
echo BACKUP_PRUNE scope=xray policy=keep_8
echo APPLY_VERIFIED
"""


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
compose_file={_quote(compose_file)}
expected_compose_sha256={_quote(compose_sha256)}
expected_services={_quote(expected_services)}
expected_pairs={_quote(expected_pairs)}
compose_project_dir=$(dirname -- "$compose_file")
exec 9>/run/vps-ssh-launcher-maintenance.lock
if ! flock -n 9; then
  echo MAINTENANCE_BUSY >&2
  exit 75
fi
backup_dir="$(mktemp -d /var/backups/vps-ssh-launcher-compose.XXXXXX)"
chmod 700 "$backup_dir"
backup_ready=0
old_image_pairs=""
old_existing_services=""
old_absent_services=""

rollback() {{
  rc="$?"
  trap - ERR INT TERM EXIT
  set +e
  if [ "$backup_ready" -ne 1 ]; then
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
trap rollback ERR INT TERM
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
  container_id="$(docker compose -f "$compose_file" ps -q "$service" 2>/dev/null || true)"
  if [ -n "$container_id" ]; then
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
trap - ERR INT TERM
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


@dataclass(frozen=True)
class AdapterResult:
    status: ActionStatus
    reason: str


RemoteExecutor = Callable[[str], tuple[int, str, str]]


def execute_action(
    action: MaintenanceAction,
    *,
    pins: dict[str, dict[str, Any]],
    executor: RemoteExecutor,
) -> AdapterResult:
    if action.resource == "xray":
        pin = pins.get("xray")
        if not pin:
            raise ValueError("Xray action has no version/SHA-256 pin.")
        command = build_xray_upgrade_command(
            version=str(pin["version"]),
            sha256=str(pin["sha256"]),
        )
    elif action.resource == "docker":
        pin = pins.get("docker")
        if not pin:
            raise ValueError("Docker action has no Compose/digest pin.")
        command = build_docker_upgrade_command(
            compose_file=str(pin["compose_file"]),
            compose_sha256=str(pin["compose_sha256"]),
            services=tuple(pin["services"]),
            digests=dict(pin["digests"]),
        )
    else:
        raise ValueError(f"No remote adapter is admitted for {action.resource}.")

    code, stdout, stderr = executor(command)
    marker_lines = {line.strip() for line in f"{stdout}\n{stderr}".splitlines()}
    if code == 0 and "APPLY_VERIFIED" in marker_lines:
        return AdapterResult(
            "verified",
            "Remote adapter completed and read back the target state.",
        )
    if "ROLLBACK_VERIFIED" in marker_lines:
        return AdapterResult(
            "rolled_back",
            "Remote adapter failed; its scoped rollback was verified.",
        )
    return AdapterResult(
        "unverified",
        "Remote adapter did not produce a verified success or rollback marker.",
    )

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
expected_sha256={_quote(sha256)}
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

rollback() {{
  rc="$?"
  trap - ERR INT TERM EXIT
  set +e
  if [ -f "$backup_dir/xray" ]; then
    cp -a "$backup_dir/xray" "$binary"
  fi
  systemctl restart {XRAY_SERVICE} >/dev/null 2>&1
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
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$archive_url" -o "$tmp_dir/xray.zip"
printf '%s  %s\\n' "$expected_sha256" "$tmp_dir/xray.zip" | sha256sum --check --status
unzip -oq "$tmp_dir/xray.zip" -d "$tmp_dir/extracted"
test -x "$tmp_dir/extracted/xray"
install -m 0755 "$tmp_dir/extracted/xray" "$binary.new"
mv -f "$binary.new" "$binary"
"$binary" run -test -confdir "$confdir"
systemctl restart {XRAY_SERVICE}
systemctl is-active --quiet {XRAY_SERVICE}
"$binary" run -test -confdir "$confdir"
trap - ERR INT TERM
rm -rf "$tmp_dir"
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

rollback() {{
  rc="$?"
  trap - ERR INT TERM EXIT
  set +e
  if [ "$backup_ready" -ne 1 ]; then
    echo APPLY_REFUSED_BEFORE_MUTATION >&2
    exit "$rc"
  fi
  if [ ! -f "$backup_dir/compose.yml" ]; then
    echo ROLLBACK_FAILED >&2
    exit "$rc"
  fi
  docker compose --project-directory "$compose_project_dir" -f "$backup_dir/compose.yml" up -d --pull never $expected_services >/dev/null 2>&1
  rollback_compose="$backup_dir/compose.yml"
  rollback_project_dir="$compose_project_dir"
  rollback_ok=1
  for service in $expected_services; do
    container_id="$(docker compose --project-directory "$rollback_project_dir" -f "$rollback_compose" ps -q "$service" 2>/dev/null || true)"
    if [ -z "$container_id" ] || [ "$(docker inspect --format '{{{{.State.Status}}}}' "$container_id" 2>/dev/null || true)" != running ]; then
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
backup_ready=1
docker compose -f "$compose_file" pull $expected_services
docker compose -f "$compose_file" up -d --no-build --pull never $expected_services
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
  repo_digests="$(docker inspect --format '{{{{join .RepoDigests "\\n"}}}}' "$container_id")"
  case "$repo_digests" in
    *"@$expected_digest"*) : ;;
    *) echo DIGEST_READBACK_MISMATCH >&2; exit 46 ;;
  esac
done
trap - ERR INT TERM
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
    markers = f"{stdout}\n{stderr}"
    if code == 0 and "APPLY_VERIFIED" in markers:
        return AdapterResult(
            "verified",
            "Remote adapter completed and read back the target state.",
        )
    if "ROLLBACK_VERIFIED" in markers:
        return AdapterResult(
            "rolled_back",
            "Remote adapter failed; its scoped rollback was verified.",
        )
    return AdapterResult(
        "unverified",
        "Remote adapter did not produce a verified success or rollback marker.",
    )

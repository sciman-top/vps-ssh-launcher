"""Remote inventory probe protocol and bounded fact decoding."""

from __future__ import annotations

import hashlib

from .models import InventoryRecord

INVENTORY_COMMAND = """set -eu
printf 'hostname=%s\n' "$(hostname 2>/dev/null || printf unknown)"
printf 'os=%s\n' "$(uname -s 2>/dev/null || printf unknown)"
printf 'kernel=%s\n' "$(uname -r 2>/dev/null || printf unknown)"
printf 'arch=%s\n' "$(uname -m 2>/dev/null || printf unknown)"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  printf 'os_id=%s\n' "${ID:-unknown}"
  printf 'os_version=%s\n' "${VERSION_ID:-unknown}"
else
  printf 'os_id=unknown\n'
  printf 'os_version=unknown\n'
fi
if command -v df >/dev/null 2>&1; then
  printf 'root_disk_used_percent=%s\n' "$(df -P / | awk 'NR == 2 {gsub(/%/, "", $5); print $5}')"
fi
if command -v free >/dev/null 2>&1; then
  printf 'memory_mb=%s\n' "$(free -m | awk '/^Mem:/ {print $2}')"
fi
if command -v ss >/dev/null 2>&1; then
  printf 'listeners=%s\n' "$(ss -ltnH 2>/dev/null | awk '{print $4}' | sort -u | paste -sd, -)"
  # UDP listeners are a separate transport surface (Hysteria2/QUIC). Keep
  # addresses and ports only; never collect protocol credentials or config.
  printf 'udp_listeners=%s\n' "$(ss -lunH 2>/dev/null | awk '{print $4}' | sort -u | paste -sd, -)"
fi
if command -v docker >/dev/null 2>&1; then
  printf 'docker=present\n'
  printf 'docker_version=%s\n' "$(docker version --format '{{.Server.Version}}' 2>/dev/null || printf unknown)"
  if docker compose version >/dev/null 2>&1; then
    printf 'compose_version=%s\n' "$(docker compose version --short 2>/dev/null || printf unknown)"
  else
    printf 'compose_version=absent\n'
  fi
  printf 'docker_services=%s\n' "$(docker ps --format '{{.Names}}' 2>/dev/null | sort | paste -sd, -)"
  if docker inspect cli-proxy-api >/dev/null 2>&1; then
    printf 'cpa_container_status=%s\n' "$(docker inspect --format '{{.State.Status}}' cli-proxy-api 2>/dev/null || printf unknown)"
    printf 'cpa_image=%s\n' "$(docker inspect --format '{{.Config.Image}}' cli-proxy-api 2>/dev/null || printf unknown)"
    printf 'cpa_image_digest=%s\n' "$(docker inspect --format '{{.Image}}' cli-proxy-api 2>/dev/null || printf unknown)"
  fi
else
  printf 'docker=absent\n'
  printf 'compose_version=absent\n'
fi
unit_state() {
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$1" 2>/dev/null; then
    printf 'active\n'
  elif command -v rc-service >/dev/null 2>&1 && rc-service "$1" status >/dev/null 2>&1; then
    printf 'active\n'
  else
    printf 'inactive\n'
  fi
}
service_backend=unknown
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  service_backend=systemd
elif command -v rc-service >/dev/null 2>&1; then
  service_backend=openrc
elif command -v systemctl >/dev/null 2>&1; then
  service_backend=systemd
fi
printf 'service_backend=%s\n' "$service_backend"
vasma_path=absent
for candidate in /usr/bin/vasma /usr/sbin/vasma; do
  if [ -x "$candidate" ]; then
    vasma_path="$candidate"
    break
  fi
done
printf 'vasma_path=%s\n' "$vasma_path"
if [ "$vasma_path" != absent ]; then
  printf 'vasma_sha256=%s\n' "$(sha256sum "$vasma_path" 2>/dev/null | awk '{print $1}')"
fi
if [ -f /run/reboot-required ]; then
  printf 'reboot_required=present\n'
else
  printf 'reboot_required=absent\n'
fi
xray_state=absent
if [ -x /etc/v2ray-agent/xray/xray ] || command -v xray >/dev/null 2>&1; then
  xray_state=present
elif [ "$(unit_state xray)" = active ]; then
  xray_state=present
fi
printf 'xray=%s\n' "$xray_state"
if [ -x /etc/v2ray-agent/xray/xray ]; then
  printf 'xray_version=%s\n' "$(/etc/v2ray-agent/xray/xray --version 2>/dev/null | awk 'NR == 1 {print $2}')"
  printf 'xray_sha256=%s\n' "$(sha256sum /etc/v2ray-agent/xray/xray 2>/dev/null | awk '{print $1}')"
  if [ -d /etc/v2ray-agent/xray/conf ]; then
    printf 'xray_config_sha256=%s\n' "$(find /etc/v2ray-agent/xray/conf -type f -print0 2>/dev/null | sort -z | xargs -0 -r sha256sum 2>/dev/null | sha256sum | awk '{print $1}')"
    if /etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf >/dev/null 2>&1; then
      printf 'xray_config_check=ok\n'
    else
      printf 'xray_config_check=failed\n'
    fi
  fi
  geodata_files="$(find /etc/v2ray-agent/xray -maxdepth 1 -type f \\( -name 'geoip.dat' -o -name 'geosite.dat' \\) -print 2>/dev/null | sort)"
  if [ -n "$geodata_files" ]; then
    printf 'xray_geodata_sha256=%s\n' "$(printf '%s\n' "$geodata_files" | xargs sha256sum | sha256sum | awk '{print $1}')"
  fi
fi
if [ ! -x /etc/v2ray-agent/xray/xray ] || [ ! -d /etc/v2ray-agent/xray/conf ]; then
  printf 'xray_config_check=missing\n'
fi
printf 'xray_unit=%s\n' "$(unit_state xray)"
sing_box_state=absent
if [ -x /etc/v2ray-agent/sing-box/sing-box ] || command -v sing-box >/dev/null 2>&1; then
  sing_box_state=present
elif [ "$(unit_state sing-box)" = active ]; then
  sing_box_state=present
fi
printf 'sing_box=%s\n' "$sing_box_state"
if [ -x /etc/v2ray-agent/sing-box/sing-box ]; then
  printf 'sing_box_version=%s\n' "$(/etc/v2ray-agent/sing-box/sing-box version 2>/dev/null | awk '/sing-box version/ {print $3; exit}')"
  printf 'sing_box_sha256=%s\n' "$(sha256sum /etc/v2ray-agent/sing-box/sing-box 2>/dev/null | awk '{print $1}')"
  if [ -f /etc/v2ray-agent/sing-box/conf/config.json ]; then
    printf 'sing_box_config_sha256=%s\n' "$(sha256sum /etc/v2ray-agent/sing-box/conf/config.json 2>/dev/null | awk '{print $1}')"
    if /etc/v2ray-agent/sing-box/sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json >/dev/null 2>&1; then
      printf 'sing_box_config_check=ok\n'
    else
      printf 'sing_box_config_check=failed\n'
    fi
  fi
  if [ -d /etc/v2ray-agent/sing-box/conf/config ]; then
    printf 'sing_box_source_config_sha256=%s\n' "$(find /etc/v2ray-agent/sing-box/conf/config -type f -print0 2>/dev/null | sort -z | xargs -0 -r sha256sum 2>/dev/null | sha256sum | awk '{print $1}')"
  fi
fi
if [ ! -x /etc/v2ray-agent/sing-box/sing-box ] || [ ! -f /etc/v2ray-agent/sing-box/conf/config.json ]; then
  printf 'sing_box_config_check=missing\n'
fi
printf 'sing_box_unit=%s\n' "$(unit_state sing-box)"
if [ "$(unit_state cpa)" = active ]; then
  printf 'cpa=active\n'
else
  printf 'cpa=unknown\n'
fi
printf 'nginx_unit=%s\n' "$(unit_state nginx)"
printf 'fail2ban_unit=%s\n' "$(unit_state fail2ban)"
"""

_ALLOWED_FACT_KEYS = frozenset(
    {
        "hostname",
        "os",
        "kernel",
        "arch",
        "os_id",
        "os_version",
        "root_disk_used_percent",
        "memory_mb",
        "listeners",
        "udp_listeners",
        "docker",
        "docker_version",
        "compose_version",
        "docker_services",
        "service_backend",
        "vasma_path",
        "vasma_sha256",
        "reboot_required",
        "cpa_container_status",
        "cpa_image",
        "cpa_image_digest",
        "xray",
        "xray_version",
        "xray_sha256",
        "xray_config_sha256",
        "xray_config_check",
        "xray_geodata_sha256",
        "xray_unit",
        "sing_box",
        "sing_box_version",
        "sing_box_sha256",
        "sing_box_config_sha256",
        "sing_box_config_check",
        "sing_box_source_config_sha256",
        "sing_box_unit",
        "cpa",
        "nginx_unit",
        "fail2ban_unit",
    }
)


_FACT_MAX_CHARS = 256


def _bound_fact(value: str) -> str:
    """Bound stored fact size while keeping fingerprints tail-sensitive.

    List-valued identity facts (listeners, udp_listeners, docker_services)
    can realistically exceed the cap; a plain truncation would make two
    different sets sharing a prefix indistinguishable to the plan/apply
    fingerprint, so a short content digest is appended when truncation fires.
    """
    if len(value) <= _FACT_MAX_CHARS:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{value[:_FACT_MAX_CHARS]}#{digest}"


def parse_probe_output(
    profile: str,
    output: str,
    *,
    return_code: int = 0,
) -> InventoryRecord:
    facts: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key in _ALLOWED_FACT_KEYS and value:
            facts[key] = _bound_fact(value)
    if return_code != 0:
        return InventoryRecord(
            profile=profile,
            reachable=False,
            facts={},
            error_class="remote_command_error",
        )
    return InventoryRecord(profile=profile, reachable=True, facts=facts)

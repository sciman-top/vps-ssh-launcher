"""Xray maintenance: pin schema, upgrade planning and guarded command."""

from __future__ import annotations

import shlex
from typing import Any

from .models import MaintenanceAction
from .pins import normalize_version, normalize_sha256

XRAY_BINARY = "/etc/v2ray-agent/xray/xray"
XRAY_CONFDIR = "/etc/v2ray-agent/xray/conf"
XRAY_SERVICE = "xray"


def normalize_pin(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("pins.xray must be a TOML table.")
    if set(value) != {"version", "sha256"}:
        raise ValueError("pins.xray requires exactly version and sha256.")
    return {
        "version": normalize_version(value.get("version")),
        "sha256": normalize_sha256(value.get("sha256")),
    }


def plan_upgrade(
    profile: str, desired: str, observed: str, pin: dict[str, Any] | None
) -> MaintenanceAction:
    resource = "xray"
    if not pin:
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="blocked",
            reason="Xray upgrade requires an explicit version and SHA-256 pin.",
        )
    target = str(pin["version"])
    if observed in {target, f"v{target}"}:
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="noop",
            reason="Observed Xray version already matches the pinned target.",
            target=target,
        )
    if observed in {"absent", "unknown", "present"}:
        return MaintenanceAction(
            profile=profile,
            resource=resource,
            desired=desired,
            observed=observed,
            status="blocked",
            reason="Fresh inventory must report a concrete Xray version before upgrade.",
            target=target,
        )
    return MaintenanceAction(
        profile=profile,
        resource=resource,
        desired=desired,
        observed=observed,
        status="planned",
        reason="Pinned Xray version differs from the fresh inventory.",
        target=target,
    )


def build_xray_upgrade_command(*, version: str, sha256: str) -> str:
    version = normalize_version(version)
    sha256 = normalize_sha256(sha256)
    archive_url = (
        "https://github.com/XTLS/Xray-core/releases/download/"
        f"v{version}/Xray-linux-64.zip"
    )
    return f"""set -Eeuo pipefail
version={shlex.quote(version)}
archive_url={shlex.quote(archive_url)}
expected_artifact_sha256={shlex.quote(sha256)}
binary={shlex.quote(XRAY_BINARY)}
confdir={shlex.quote(XRAY_CONFDIR)}
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
trap rollback ERR EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
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
trap - ERR INT TERM EXIT
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


def build_from_pin(pin: dict[str, Any]) -> str:
    return build_xray_upgrade_command(
        version=str(pin["version"]),
        sha256=str(pin["sha256"]),
    )
